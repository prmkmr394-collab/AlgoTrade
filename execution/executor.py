"""
Order executor.

Two modes (controlled by config.mode.trading_mode):
  - "paper": simulates orders. P&L computed using real option LTPs from WebSocket.
             No actual orders are placed with Kite.
  - "live":  places real orders via Kite API.

Fixes applied 2026-05-06:
  - Live mode now installs option tick listener too (was only paper)
  - Position state syncs with Kite at startup and on demand
  - Stuck-position protection: if bot state shows open position but Kite
    has no matching position, state is auto-cleared

Fixes applied 2026-05-07:
  - LIMIT orders with aggressive buffer (Zerodha disallows MARKET on options)
  - Tick-size rounding (₹0.05) on buffered prices
  - Buffered SELL exits to ensure fills on target/SL/hard-cap
"""
import time
import uuid
from datetime import datetime
from threading import Lock
from typing import Optional, Dict, Tuple
import pytz

from utils.config_loader import config
from utils.logger import log, log_trade
from utils import state
from strategy.position_tracker import position_tracker, Position


IST = pytz.timezone("Asia/Kolkata")

# NFO options tick size
TICK_SIZE = 0.05


def round_to_tick(price: float, tick: float = TICK_SIZE) -> float:
    """Round to nearest valid tick size for the exchange."""
    if price <= 0:
        return 0.0
    return round(round(price / tick) * tick, 2)


class Executor:
    """
    Single executor instance for the bot. Wraps Kite order placement.
    """

    def __init__(self, kite, market_data_mgr):
        self.kite = kite
        self.md = market_data_mgr
        self.mode = config.get("mode", "trading_mode", default="paper").lower()
        self.lot_size = config.get("strategy", "lot_size", default=75)
        self.lots = config.get("strategy", "lots_per_trade", default=1)
        self.qty = self.lot_size * self.lots
        self.product = config.get("execution", "product", default="MIS")

        # FORCE LIMIT: Zerodha rejects MARKET orders on options for retail algo accounts.
        # We use LIMIT with an aggressive buffer to ensure instant fill.
        self.order_type = "LIMIT"
        self.entry_buffer = config.get("execution", "entry_price_buffer", default=2.0)
        self.exit_buffer = config.get("execution", "exit_price_buffer", default=2.0)

        self.variety = config.get("execution", "variety", default="regular")
        self.retries = config.get("execution", "retry_attempts", default=3)
        self.retry_delay = config.get("execution", "retry_delay_seconds", default=1)
        self._lock = Lock()
        self._processed_signals = set()  # idempotency keys

        # Latest option LTP — works for both paper and live now
        self._option_ltp: Optional[float] = None
        self._option_token_active: Optional[int] = None

        log.info(f"Executor initialized in {self.mode.upper()} mode. "
                 f"Quantity per trade: {self.qty} ({self.lots} lot × {self.lot_size}). "
                 f"Order type: LIMIT (entry buffer +{self.entry_buffer}, exit buffer -{self.exit_buffer})")

        # On startup: reconcile bot's view of positions with Kite reality
        self._reconcile_with_kite_on_startup()

    def is_paper(self) -> bool:
        return self.mode == "paper"

    # =================================================================
    # STARTUP RECONCILIATION
    # =================================================================

    def _reconcile_with_kite_on_startup(self):
        """
        At bot startup, check if our state thinks we have an open position.
        If yes, verify against Kite. If Kite shows no matching position,
        clear our state — the position was likely closed manually outside
        the bot, and stale state would block future entries.
        """
        try:
            if not position_tracker.has_open_position():
                log.info("Startup: no open position in bot state. Clean slate.")
                return

            current_pos = position_tracker.get_position()
            if current_pos is None:
                return

            log.warning(
                f"Startup: bot state shows open position {current_pos.tradingsymbol}. "
                f"Verifying against Kite..."
            )

            # In paper mode, just trust local state
            if self.is_paper():
                log.info("Paper mode — trusting bot state. No Kite check.")
                return

            # Live mode: ask Kite for actual positions
            kite_positions = self.kite.positions()
            net_positions = kite_positions.get("net", [])

            matching = None
            for p in net_positions:
                if p.get("tradingsymbol") == current_pos.tradingsymbol:
                    if int(p.get("quantity", 0)) != 0:
                        matching = p
                        break

            if matching:
                log.info(
                    f"Startup: Kite confirms open position {current_pos.tradingsymbol} "
                    f"qty={matching.get('quantity')}. Keeping state."
                )
                # Re-subscribe option ticks so monitoring resumes
                try:
                    self.md.subscribe_option(current_pos.instrument_token)
                    self._install_option_tick_listener(current_pos.instrument_token)
                    self._option_token_active = current_pos.instrument_token
                    log.info("Re-installed tick listener for resumed position.")
                except Exception as e:
                    log.warning(f"Re-subscribe failed: {e}")
            else:
                log.warning(
                    f"Startup: bot state shows open {current_pos.tradingsymbol} "
                    f"but Kite has no matching position. CLEARING stale state."
                )
                position_tracker.force_clear_position(reason="STARTUP_RECONCILE")
                state.add_error(
                    f"Startup reconcile: cleared stale position {current_pos.tradingsymbol}"
                )
        except Exception as e:
            log.error(f"Startup reconciliation failed: {e}")
            state.add_error(f"startup_reconcile_error: {e}")

    def force_clear_stuck_position(self) -> bool:
        """
        Manual recovery: clear bot's view of an open position when you
        know Kite has none. Use when target/SL didn't fire and you
        closed manually.
        """
        try:
            if not position_tracker.has_open_position():
                log.info("No stuck position to clear.")
                return True
            pos = position_tracker.get_position()
            log.warning(f"Manual force-clear of position: {pos.tradingsymbol if pos else 'unknown'}")
            position_tracker.force_clear_position(reason="MANUAL_CLEAR")
            try:
                self.md.unsubscribe_option()
            except Exception:
                pass
            self._option_token_active = None
            self._option_ltp = None
            return True
        except Exception as e:
            log.error(f"force_clear_stuck_position failed: {e}")
            return False

    # =================================================================
    # ENTRY
    # =================================================================

    def enter_position(self, side: str, option_instrument: dict, signal_id: str) -> Optional[Position]:
        """Place an entry order. Returns Position on success, None on failure."""
        with self._lock:
            if signal_id in self._processed_signals:
                log.warning(f"Signal {signal_id} already processed. Skipping.")
                return None
            self._processed_signals.add(signal_id)

        tradingsymbol = option_instrument["tradingsymbol"]
        instrument_token = option_instrument["instrument_token"]
        exchange = option_instrument.get("exchange", "NFO")

        quote = self._get_quote(exchange, tradingsymbol)
        if quote is None:
            log.error(f"Could not fetch quote for {tradingsymbol}. Aborting entry.")
            return None

        ltp = quote["last_price"]
        bid = quote.get("depth", {}).get("buy", [{}])[0].get("price", 0) or ltp
        ask = quote.get("depth", {}).get("sell", [{}])[0].get("price", 0) or ltp
        spread = ask - bid

        log.info(f"Entry quote {tradingsymbol}: LTP={ltp}, bid={bid}, ask={ask}, spread={spread:.2f}")

        if self.is_paper():
            fill_price = ask if ask > 0 else ltp
            order_id = f"PAPER-{uuid.uuid4().hex[:10]}"
            log.info(f"PAPER ORDER: BUY {self.qty} {tradingsymbol} @ {fill_price} (order_id={order_id})")
        else:
            # LIMIT BUY at ask + buffer (or LTP + buffer if ask unavailable).
            # This guarantees fill since we're crossing the ask.
            ref_price = ask if ask > 0 else ltp
            limit_price = round_to_tick(ref_price + self.entry_buffer)
            log.info(f"LIVE LIMIT BUY: ref={ref_price}, +buffer={self.entry_buffer} → limit={limit_price}")

            order_id, fill_price = self._place_live_order(
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type="BUY",
                quantity=self.qty,
                limit_price=limit_price,
                tag=f"NAB-E-{signal_id[:8]}",
            )
            if order_id is None:
                log.error(f"Live entry failed for {tradingsymbol}.")
                return None

        # Subscribe to option ticks (works in both paper and live)
        try:
            self.md.subscribe_option(instrument_token)
        except Exception as e:
            log.warning(f"Failed to subscribe option ticks: {e}")

        position = position_tracker.open_position(
            side=side,
            tradingsymbol=tradingsymbol,
            instrument_token=instrument_token,
            quantity=self.qty,
            entry_price=fill_price,
        )

        # FIX: install tick listener for BOTH paper and live mode
        self._install_option_tick_listener(instrument_token)
        self._option_token_active = instrument_token

        return position

    # =================================================================
    # EXIT
    # =================================================================

    def exit_position(self, position: Position, reason: str) -> bool:
        """Close the open position. Called by position_tracker via callback."""
        tradingsymbol = position.tradingsymbol
        log.info(f"Exiting {tradingsymbol} reason={reason}")

        if self.is_paper():
            exit_price = self._option_ltp or position.current_ltp
            order_id = f"PAPER-X-{uuid.uuid4().hex[:10]}"
            log.info(f"PAPER EXIT: SELL {position.quantity} {tradingsymbol} @ {exit_price} (order_id={order_id})")
        else:
            # For exit, prefer the live tick LTP we already have (fastest, no API call).
            # Fall back to a fresh quote if no tick LTP yet.
            ref_price = self._option_ltp
            if not ref_price:
                quote = self._get_quote("NFO", tradingsymbol)
                if quote:
                    bid = quote.get("depth", {}).get("buy", [{}])[0].get("price", 0)
                    ref_price = bid if bid > 0 else quote.get("last_price", 0)
                else:
                    ref_price = position.current_ltp or 0

            if ref_price <= 0:
                log.error(f"EXIT: no valid reference price for {tradingsymbol}. Aborting exit.")
                state.add_error(f"EXIT_NO_PRICE: {tradingsymbol} reason={reason}")
                return False

            # LIMIT SELL at ref - buffer to hit the bid side, ensuring fill.
            limit_price = round_to_tick(ref_price - self.exit_buffer)
            if limit_price <= 0:
                limit_price = round_to_tick(TICK_SIZE)  # safety floor
            log.info(f"LIVE LIMIT SELL: ref={ref_price}, -buffer={self.exit_buffer} → limit={limit_price}")

            order_id, exit_price = self._place_live_order(
                exchange="NFO",
                tradingsymbol=tradingsymbol,
                transaction_type="SELL",
                quantity=position.quantity,
                limit_price=limit_price,
                tag=f"NAB-X-{reason[:6]}",
            )
            if order_id is None:
                log.error(f"Live exit FAILED for {tradingsymbol}. CRITICAL.")
                state.add_error(f"EXIT_FAILED: {tradingsymbol} reason={reason}")
                return False

        try:
            self.md.unsubscribe_option()
        except Exception as e:
            log.warning(f"Failed to unsubscribe option: {e}")

        self._option_token_active = None
        self._option_ltp = None

        closed = position_tracker.close_position(exit_price=exit_price, exit_reason=reason)

        from risk.risk_manager import risk_manager
        if closed:
            risk_manager.record_trade_close(
                pnl=closed.current_pnl,
                exit_reason=reason,
                side=closed.side,
            )
        return True

    # =================================================================
    # KITE INTERACTIONS
    # =================================================================

    def _get_quote(self, exchange: str, tradingsymbol: str) -> Optional[Dict]:
        symbol = f"{exchange}:{tradingsymbol}"
        for attempt in range(self.retries):
            try:
                quotes = self.kite.quote([symbol])
                return quotes.get(symbol)
            except Exception as e:
                log.warning(f"Quote fetch attempt {attempt+1} failed: {e}")
                time.sleep(self.retry_delay)
        return None

    def _place_live_order(self, exchange: str, tradingsymbol: str,
                          transaction_type: str, quantity: int,
                          limit_price: float, tag: str) -> Tuple[Optional[str], Optional[float]]:
        """
        Place a live LIMIT order with retries.
        Returns (order_id, avg_fill_price).
        Re-prices the limit on each retry using fresh quote (so we don't get stuck
        at a stale buffer if the option moved away from us).
        """
        current_limit = limit_price

        for attempt in range(self.retries):
            try:
                order_id = self.kite.place_order(
                    variety=self.variety,
                    exchange=exchange,
                    tradingsymbol=tradingsymbol,
                    transaction_type=transaction_type,
                    quantity=quantity,
                    product=self.product,
                    order_type="LIMIT",
                    price=current_limit,
                    tag=tag,
                )
                log.info(f"LIVE LIMIT ORDER placed: {transaction_type} {quantity} {tradingsymbol} "
                         f"@ ₹{current_limit} order_id={order_id}")

                # Wait briefly then check fill
                time.sleep(1.5)
                fill_price, status = self._get_order_status(order_id)

                if status == "COMPLETE":
                    log.info(f"Order {order_id} filled @ ₹{fill_price}")
                    return order_id, fill_price

                # Not filled — could be partially filled or sitting in queue
                if status in ("OPEN", "TRIGGER PENDING"):
                    log.warning(f"Order {order_id} not filled (status={status}). "
                                f"Cancelling and re-pricing.")
                    try:
                        self.kite.cancel_order(variety=self.variety, order_id=order_id)
                    except Exception as e:
                        log.warning(f"Cancel failed: {e}")

                    # Re-price for next attempt with fresh quote
                    quote = self._get_quote(exchange, tradingsymbol)
                    if quote:
                        ltp = quote["last_price"]
                        if transaction_type == "BUY":
                            ask = quote.get("depth", {}).get("sell", [{}])[0].get("price", 0) or ltp
                            current_limit = round_to_tick(ask + self.entry_buffer + (attempt + 1) * 1.0)
                        else:
                            bid = quote.get("depth", {}).get("buy", [{}])[0].get("price", 0) or ltp
                            current_limit = round_to_tick(bid - self.exit_buffer - (attempt + 1) * 1.0)
                            if current_limit <= 0:
                                current_limit = round_to_tick(TICK_SIZE)
                        log.info(f"Re-pricing attempt {attempt+2}: new limit={current_limit}")

                elif status == "REJECTED":
                    log.error(f"Order {order_id} REJECTED by Kite. Stopping retries.")
                    state.add_error(f"order_rejected: {tradingsymbol} {transaction_type}")
                    return None, None
                else:
                    log.warning(f"Order {order_id} status={status}. Returning as-is.")
                    return order_id, fill_price or 0.0

            except Exception as e:
                log.error(f"Order placement attempt {attempt+1} failed: {e}")
                state.add_error(f"order_failed: {e}")
                time.sleep(self.retry_delay * (attempt + 1))

        return None, None

    def _get_order_status(self, order_id: str) -> Tuple[Optional[float], Optional[str]]:
        """Returns (avg_fill_price, status)."""
        try:
            orders = self.kite.orders()
            for o in orders:
                if o.get("order_id") == order_id:
                    return float(o.get("average_price", 0) or 0), o.get("status")
        except Exception as e:
            log.warning(f"Order fetch failed: {e}")
        return None, None

    def _get_avg_fill_price(self, order_id: str) -> Optional[float]:
        """Legacy helper — kept for backwards compat."""
        price, _ = self._get_order_status(order_id)
        return price

    # =================================================================
    # OPTION TICK LISTENER (now works in both paper and live)
    # =================================================================

    def _install_option_tick_listener(self, option_token: int):
        """
        Hook into the WebSocket tick stream to forward option LTP updates
        to position_tracker. This drives SL/Target/HARD_CAP monitoring.

        Previously only installed in paper mode — that meant live trades
        had no SL/Target enforcement. Now installed in both modes.
        """
        original_on_ticks = self.md._on_ticks

        def patched_on_ticks(ws, ticks):
            original_on_ticks(ws, ticks)
            for t in ticks:
                if t.get("instrument_token") == option_token:
                    ltp = t.get("last_price")
                    if ltp is not None:
                        self._option_ltp = ltp
                        position_tracker.on_option_tick(ltp)

        self.md._on_ticks = patched_on_ticks
        if self.md.ticker:
            self.md.ticker.on_ticks = patched_on_ticks
        log.info(f"Option tick listener installed for token {option_token} (mode={self.mode})")
