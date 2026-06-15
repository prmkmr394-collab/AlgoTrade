"""
Position tracker.

Holds the state of any open position and monitors it against SL / Target / time exit
on every option-LTP tick.

When SL / Target / time triggers, it calls the registered exit_callback so the
executor can place the closing order.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from threading import Lock
from typing import Optional, Callable
import pytz

from utils.config_loader import config
from utils.logger import log, log_trade
from utils import state


IST = pytz.timezone("Asia/Kolkata")


@dataclass
class Position:
    side: str                       # "BUY" (CE) or "SELL" (PE)  — direction of view
    tradingsymbol: str
    instrument_token: int
    quantity: int
    entry_price: float
    entry_time: datetime
    sl_price: float                 # premium at which to exit on SL
    target_price: float             # premium at which to exit on target
    current_ltp: float = 0.0
    current_pnl: float = 0.0
    is_open: bool = True
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None  # "STOP_LOSS" | "TARGET" | "TIME_EXIT" | "OPPOSITE_SIGNAL" | "KILL_SWITCH" | "MANUAL"


class PositionTracker:
    """Single-position tracker (config: max_concurrent_positions=1)."""

    def __init__(self):
        self._position: Optional[Position] = None
        self._lock = Lock()
        self._exit_callback: Optional[Callable[[Position, str], None]] = None
        self._exit_in_progress = False  # idempotency: don't fire exit twice

    def register_exit_callback(self, fn: Callable[[Position, str], None]):
        """Executor registers here. Called as fn(position, reason) when exit triggered."""
        self._exit_callback = fn

    def open_position(self, side: str, tradingsymbol: str, instrument_token: int,
                       quantity: int, entry_price: float) -> Position:
        with self._lock:
            if self._position is not None and self._position.is_open:
                raise RuntimeError("Position already open. Cannot open another.")

            sl_pts = config.get("risk", "stop_loss_points", default=12)
            tgt_pts = config.get("risk", "target_points", default=12)

            position = Position(
                side=side,
                tradingsymbol=tradingsymbol,
                instrument_token=instrument_token,
                quantity=quantity,
                entry_price=entry_price,
                entry_time=datetime.now(IST),
                sl_price=round(entry_price - sl_pts, 2),
                target_price=round(entry_price + tgt_pts, 2),
                current_ltp=entry_price,
            )
            self._position = position
            self._exit_in_progress = False

            log_trade("ENTRY",
                      side=side, symbol=tradingsymbol, qty=quantity,
                      entry=entry_price, sl=position.sl_price, target=position.target_price)
            log.info(f"Position OPENED: {side} {tradingsymbol} qty={quantity} "
                     f"entry={entry_price} SL={position.sl_price} Target={position.target_price}")

            self._publish_state()
            return position

    def close_position(self, exit_price: float, exit_reason: str) -> Optional[Position]:
        with self._lock:
            if self._position is None or not self._position.is_open:
                return None

            pos = self._position
            pos.exit_price = exit_price
            pos.exit_time = datetime.now(IST)
            pos.exit_reason = exit_reason
            pos.is_open = False

            # P&L = (exit - entry) * quantity. Always long-options, so direction is irrelevant.
            pnl_per_unit = exit_price - pos.entry_price
            pos.current_pnl = pnl_per_unit * pos.quantity

            log_trade("EXIT",
                      side=pos.side, symbol=pos.tradingsymbol, qty=pos.quantity,
                      entry=pos.entry_price, exit=exit_price,
                      pnl=pos.current_pnl, reason=exit_reason)
            log.info(f"Position CLOSED: {pos.tradingsymbol} exit={exit_price} "
                     f"pnl=₹{pos.current_pnl:.2f} reason={exit_reason}")

            # Persist to state
            state.add_completed_trade({
                "side": pos.side,
                "symbol": pos.tradingsymbol,
                "qty": pos.quantity,
                "entry": pos.entry_price,
                "exit": exit_price,
                "pnl": round(pos.current_pnl, 2),
                "entry_time": pos.entry_time.strftime("%H:%M:%S"),
                "exit_time": pos.exit_time.strftime("%H:%M:%S"),
                "exit_reason": exit_reason,
            })
            state.set_open_position(None)
            self._position = None
            return pos

    def on_option_tick(self, ltp: float):
        """Called on every option LTP update. Triggers exit if SL/Target hit."""
        if self._exit_in_progress:
            return

        with self._lock:
            if self._position is None or not self._position.is_open:
                return
            pos = self._position
            pos.current_ltp = ltp
            pos.current_pnl = (ltp - pos.entry_price) * pos.quantity

            should_exit = False
            reason = ""
            if ltp <= pos.sl_price:
                should_exit = True
                reason = "STOP_LOSS"
            elif ltp >= pos.target_price:
                should_exit = True
                reason = "TARGET"

        if should_exit:
            self._fire_exit(reason)
        else:
            self._publish_state()

    def trigger_exit(self, reason: str):
        """External trigger (time exit, kill switch, opposite signal)."""
        with self._lock:
            if self._position is None or not self._position.is_open:
                return
        self._fire_exit(reason)

    def _fire_exit(self, reason: str):
        """Idempotent exit call to executor."""
        with self._lock:
            if self._exit_in_progress or self._position is None:
                return
            self._exit_in_progress = True
            pos = self._position

        log.warning(f"EXIT TRIGGERED: {reason} on {pos.tradingsymbol} @ LTP={pos.current_ltp}")
        if self._exit_callback:
            try:
                self._exit_callback(pos, reason)
            except Exception as e:
                log.exception(f"Exit callback failed: {e}")
                # Reset flag so next tick can retry. Critical: do NOT swallow exits.
                with self._lock:
                    self._exit_in_progress = False

    def has_open_position(self) -> bool:
        with self._lock:
            return self._position is not None and self._position.is_open

    def get_position(self) -> Optional[Position]:
        with self._lock:
            return self._position

    def _publish_state(self):
        """Push current position to shared state for the dashboard."""
        if self._position and self._position.is_open:
            state.set_open_position({
                "symbol": self._position.tradingsymbol,
                "side": self._position.side,
                "qty": self._position.quantity,
                "entry": self._position.entry_price,
                "sl": self._position.sl_price,
                "target": self._position.target_price,
                "ltp": self._position.current_ltp,
                "pnl": round(self._position.current_pnl, 2),
            })


# Singleton
position_tracker = PositionTracker()
