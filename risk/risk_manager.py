"""
Risk manager.

ALL trade decisions pass through this module before reaching the executor.
Every gate is a hard NO if violated — the bot does not negotiate with risk rules.

Gates implemented:
  1. Kill switch active? (STOP.txt present)
  2. Daily halt active? (HALTED.txt present, e.g., from prior loss-cap breach today)
  3. Trading window — entries only between entries_start and entries_stop
  4. Max trades/day not yet reached
  5. Daily loss cap not breached
  6. No concurrent position (1-position rule)
  7. Re-entry block — same direction not allowed within N mins of an SL hit
  8. Liquidity check — bid-ask spread within tolerance
  9. Slippage guard — abort if LTP moved too much from signal price

Returns a tuple: (allowed: bool, reason: str)
"""
from datetime import datetime, time as dt_time, timedelta
from typing import Optional, Tuple
import pytz

from utils.config_loader import config
from utils.logger import log
from utils import state
from utils.kill_switch import is_kill_switch_active, is_halted_for_day, halt_for_day


IST = pytz.timezone("Asia/Kolkata")


def _parse_hhmm(s: str) -> dt_time:
    h, m = s.split(":")
    return dt_time(int(h), int(m))


class RiskManager:
    """Stateful risk manager — tracks per-day metrics in memory + state.json."""

    def __init__(self):
        self.last_sl_hit_time: Optional[datetime] = None
        self.last_sl_hit_side: Optional[str] = None  # "BUY" or "SELL"

    # ------------- public API -------------

    def can_enter_trade(self, signal_side: str, expected_premium: float,
                         current_premium: float, bid: float, ask: float) -> Tuple[bool, str]:
        """
        Comprehensive pre-trade check. All gates must pass.

        Args:
            signal_side: "BUY" or "SELL"
            expected_premium: premium at signal time
            current_premium: premium right now (pre-order)
            bid, ask: top-of-book bid/ask for liquidity check
        """
        now = datetime.now(IST)

        # Gate 1: Kill switch
        if is_kill_switch_active():
            return False, "KILL_SWITCH_ACTIVE"

        # Gate 2: Daily halt
        if is_halted_for_day():
            return False, "HALTED_FOR_DAY"

        # Gate 3: Trading window
        ok, reason = self._check_trading_window(now, for_entry=True)
        if not ok:
            return False, reason

        # Gate 4: Max trades/day
        snap = state.get_state()
        trades_today = snap["today"]["trades_count"]
        max_trades = config.get("risk", "max_trades_per_day", default=4)
        if trades_today >= max_trades:
            return False, f"MAX_TRADES_REACHED ({trades_today}/{max_trades})"

        # Gate 5: Daily loss cap
        realized_pnl = snap["today"]["realized_pnl"]
        loss_cap = config.get("risk", "daily_loss_cap", default=1500)
        if realized_pnl <= -loss_cap:
            halt_for_day(f"Daily loss cap breached: {realized_pnl:.2f} <= -{loss_cap}")
            return False, f"LOSS_CAP_BREACHED (realized {realized_pnl:.2f})"

        # Gate 6: No concurrent position
        if snap.get("open_position"):
            return False, "POSITION_ALREADY_OPEN"

        # Gate 7: Re-entry block (same direction within N mins of SL)
        ok, reason = self._check_reentry_block(signal_side, now)
        if not ok:
            return False, reason

        # Gate 8: Liquidity (spread)
        max_spread = config.get("risk", "liquidity_max_spread", default=2.0)
        if bid <= 0 or ask <= 0:
            return False, "INVALID_QUOTES"
        spread = ask - bid
        if spread > max_spread:
            return False, f"SPREAD_TOO_WIDE ({spread:.2f} > {max_spread})"

        # Gate 9: Slippage guard
        slip_pct = config.get("risk", "slippage_guard_pct", default=1.5)
        if expected_premium > 0:
            move_pct = abs(current_premium - expected_premium) / expected_premium * 100.0
            if move_pct > slip_pct:
                return False, f"SLIPPAGE_TOO_HIGH ({move_pct:.2f}% > {slip_pct}%)"

        return True, "OK"

    def can_be_in_market(self) -> Tuple[bool, str]:
        """Quick check used by main loop. Are we allowed to be running at all?"""
        if is_kill_switch_active():
            return False, "KILL_SWITCH_ACTIVE"
        if is_halted_for_day():
            return False, "HALTED_FOR_DAY"
        return True, "OK"

    def should_force_exit(self) -> Tuple[bool, str]:
        """Called every loop tick. Returns True if any open position must be closed NOW."""
        now = datetime.now(IST)

        if is_kill_switch_active():
            return True, "KILL_SWITCH_ACTIVE"

        # Hard square-off time
        sqoff = _parse_hhmm(config.get("trading_hours", "hard_square_off", default="15:15"))
        if now.time() >= sqoff:
            return True, "HARD_SQUARE_OFF_TIME"

        return False, "OK"

    def record_trade_close(self, pnl: float, exit_reason: str, side: str):
        """Called by position tracker when a trade is closed."""
        if exit_reason == "STOP_LOSS":
            self.last_sl_hit_time = datetime.now(IST)
            self.last_sl_hit_side = side
            log.info(f"SL hit on {side}. Re-entry block active for "
                     f"{config.get('risk', 'reentry_block_minutes', default=30)} min.")

        # Check if loss cap breached after this trade
        snap = state.get_state()
        loss_cap = config.get("risk", "daily_loss_cap", default=1500)
        if snap["today"]["realized_pnl"] <= -loss_cap:
            halt_for_day(f"Loss cap breached after trade close. "
                         f"Realized={snap['today']['realized_pnl']:.2f}")

    # ------------- private helpers -------------

    def _check_trading_window(self, now: datetime, for_entry: bool) -> Tuple[bool, str]:
        cfg = config.get("trading_hours")
        entries_start = _parse_hhmm(cfg.get("entries_start", "09:30"))
        entries_stop = _parse_hhmm(cfg.get("entries_stop", "14:45"))
        market_open = _parse_hhmm(cfg.get("market_open", "09:15"))
        market_close = _parse_hhmm(cfg.get("market_close", "15:30"))

        t = now.time()
        # Weekend check
        if now.weekday() >= 5:
            return False, "WEEKEND"

        if for_entry:
            if t < entries_start:
                return False, f"BEFORE_ENTRY_WINDOW ({t} < {entries_start})"
            if t >= entries_stop:
                return False, f"AFTER_ENTRY_WINDOW ({t} >= {entries_stop})"
        else:
            if t < market_open or t >= market_close:
                return False, "OUTSIDE_MARKET_HOURS"
        return True, "OK"

    def _check_reentry_block(self, signal_side: str, now: datetime) -> Tuple[bool, str]:
        if self.last_sl_hit_time is None:
            return True, "OK"
        if self.last_sl_hit_side != signal_side:
            return True, "OK"
        block_mins = config.get("risk", "reentry_block_minutes", default=30)
        elapsed = (now - self.last_sl_hit_time).total_seconds() / 60.0
        if elapsed < block_mins:
            remaining = block_mins - elapsed
            return False, f"REENTRY_BLOCKED ({remaining:.1f} min remaining for {signal_side})"
        return True, "OK"


# Singleton (one risk manager for the whole bot)
risk_manager = RiskManager()
