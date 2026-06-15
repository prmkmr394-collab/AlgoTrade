"""
Main orchestrator.

Wires everything together and runs the main loop.

Lifecycle:
  1. Load config, authenticate with Kite
  2. Resolve futures token, backfill historical candles
  3. Initialize executor, reconciler, position tracker
  4. Connect WebSocket, subscribe to futures
  5. Main loop:
       - Heartbeat every 5 sec
       - On candle close: evaluate signal -> risk check -> entry
       - Time-based: hard squareoff at 3:15 PM
       - Reconcile every 30 sec
       - Watch kill switch
  6. On shutdown: close any open position, disconnect WebSocket

Usage:
  python -m main

The bot only executes during market hours (Mon-Fri 9:15-15:30 IST).
Outside that window it sleeps.
"""
import signal
import sys
import time
import uuid
from datetime import datetime, timedelta, time as dt_time
from threading import Thread
import pytz

from utils.config_loader import config
from utils.logger import log
from utils import state
from utils.kill_switch import (
    is_kill_switch_active, is_halted_for_day, clear_daily_halt, acknowledge_kill_switch,
)
from utils.auth import get_kite_client
from strategy.market_data import MarketDataManager
from strategy.signal_logic import evaluate_signal
from strategy.strike_selector import find_futures_token, find_nifty_index_token, select_option_contract
from strategy.position_tracker import position_tracker
from risk.risk_manager import risk_manager
from execution.executor import Executor
from execution.reconciler import Reconciler


IST = pytz.timezone("Asia/Kolkata")


def _parse_time(s: str) -> dt_time:
    h, m = s.split(":")
    return dt_time(int(h), int(m))


class TradingBot:
    def __init__(self):
        self.kite = None
        self.md: MarketDataManager = None
        self.executor: Executor = None
        self.reconciler: Reconciler = None

        self.running = False
        self.last_signal_id = None
        self.last_reconcile = datetime.now(IST)
        self.last_heartbeat = datetime.now(IST)

        self.market_open = _parse_time(config.get("trading_hours", "market_open", default="09:15"))
        self.market_close = _parse_time(config.get("trading_hours", "market_close", default="15:30"))
        self.hard_squareoff = _parse_time(config.get("trading_hours", "hard_square_off", default="15:15"))

    # =================================================================
    # SETUP
    # =================================================================

    def setup(self):
        log.info("=" * 70)
        log.info(" NiftyAlgoBot — Starting up")
        log.info("=" * 70)
        log.info(f" Mode: {config.get('mode', 'trading_mode', default='paper').upper()}")
        log.info(f" Capital: ₹{config.get('risk', 'capital', default=40000):,}")
        log.info(f" Max trades/day: {config.get('risk', 'max_trades_per_day')}")
        log.info(f" Daily loss cap: ₹{config.get('risk', 'daily_loss_cap')}")
        log.info(f" SL/Target: {config.get('risk', 'stop_loss_points')}/{config.get('risk', 'target_points')} pts")
        log.info("=" * 70)

        # 1. Auth
        try:
            self.kite = get_kite_client()
        except Exception as e:
            log.critical(f"Authentication failed: {e}")
            sys.exit(1)

        state.update_state(kite_connected=True, status="running")
        clear_daily_halt()

        # 2. Find the underlying token — Nifty 50 spot index (matches TradingView setup)
        underlying_token = find_nifty_index_token(self.kite)
        if not underlying_token:
            log.critical("Could not resolve Nifty 50 index token. Aborting.")
            sys.exit(1)

        # 3. Init market data — use_spot=True since spot has no real volume
        self.md = MarketDataManager(
    self.kite,
    futures_token=underlying_token,
    max_candles=200,
)

        # 4. Backfill historical candles for VWAP + HA seed
        log.info("Backfilling historical 5-min candles...")
        self.md.backfill_historical(lookback_days=2)

        # 5. Register signal evaluation as a candle-close listener
        self.md.register_candle_listener(self._on_candle_close)

        # 6. Init executor
        self.executor = Executor(self.kite, self.md)

        # 7. Wire position tracker exit callback to executor
        position_tracker.register_exit_callback(self.executor.exit_position)

        # 8. Init reconciler
        self.reconciler = Reconciler(self.kite, self.executor)

        # 9. Reset state for new trading day
        state.reset_for_new_day()

        log.info("Setup complete. Starting WebSocket...")

    # =================================================================
    # MAIN LOOP
    # =================================================================

    def run(self):
        self.running = True
        self._install_signal_handlers()

        # Start WebSocket in its own thread
        self.md.start_websocket()
        time.sleep(2)  # let it connect

        log.info("Bot is now running. Watching for signals...")

        try:
            while self.running:
                now = datetime.now(IST)

                # Heartbeat
                if (now - self.last_heartbeat).total_seconds() >= 5:
                    state.heartbeat()
                    self.last_heartbeat = now

                # Kill switch
                if is_kill_switch_active():
                    acknowledge_kill_switch()
                    self._emergency_exit()
                    break

                # Daily halt
                if is_halted_for_day():
                    if position_tracker.has_open_position():
                        log.warning("Halted but position open. Forcing exit.")
                        position_tracker.trigger_exit("HALTED_FOR_DAY")
                    log.info("Bot halted for the day. Waiting for next session.")
                    time.sleep(30)
                    continue

                # Outside market hours: idle
                if not self._is_market_open(now):
                    state.update_state(status="idle")
                    time.sleep(15)
                    continue

                state.update_state(status="running")

                # Force squareoff time?
                should_exit, reason = risk_manager.should_force_exit()
                if should_exit and position_tracker.has_open_position():
                    log.warning(f"Force exit triggered: {reason}")
                    position_tracker.trigger_exit(reason)

                # Periodic reconciliation
                if (now - self.last_reconcile).total_seconds() >= config.get("execution", "reconciliation_interval_seconds", default=30):
                    self.reconciler.reconcile()
                    self.last_reconcile = now

                time.sleep(1)

        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — shutting down gracefully.")
        except Exception as e:
            log.exception(f"Fatal error in main loop: {e}")
            state.add_error(f"main_loop_fatal: {e}")
        finally:
            self._shutdown()

    # =================================================================
    # CANDLE CLOSE HANDLER (signal evaluation)
    # =================================================================

    def _on_candle_close(self, real_candle, ha_candle, vwap):
        """
        Called by MarketDataManager when a 5-min candle closes.
        This is the heart of the bot — signal eval + entry.
        """
        try:
            # Already in a position?
            if position_tracker.has_open_position():
                # Check for opposite signal -> exit only (no reverse, per spec)
                snapshot = self.md.get_latest()
                if snapshot is None:
                    return
                sig = evaluate_signal(snapshot)
                pos = position_tracker.get_position()
                if sig.side != "NONE" and sig.side != pos.side:
                    log.info(f"Opposite signal {sig.side} fired while {pos.side} open. Exit only.")
                    position_tracker.trigger_exit("OPPOSITE_SIGNAL")
                return

            # No position — evaluate signal
            snapshot = self.md.get_latest()
            if snapshot is None:
                return
            sig = evaluate_signal(snapshot)

            # Update last_signal in state for dashboard
            state.update_state(last_signal={
                "type": sig.side,
                "time": ha_candle.timestamp.strftime("%H:%M:%S"),
                "spot": round(real_candle.close, 2),
                "vwap": round(vwap, 2),
                "ha_close": round(ha_candle.close, 2),
                "reason": sig.reason,
            })

            if sig.side == "NONE":
                log.info(f"No signal: {sig.reason}")
                return

            log.info(f"SIGNAL: {sig.side} | {sig.reason}")

            # Resolve option contract
            option = select_option_contract(self.kite, sig.side, real_candle.close)
            if option is None:
                log.error("Could not resolve option contract.")
                return

            # Get current option quote for risk checks
            quote = self.executor._get_quote("NFO", option["tradingsymbol"])
            if quote is None:
                log.error("Could not get option quote. Skipping signal.")
                return
            current_premium = quote["last_price"]
            depth = quote.get("depth", {})
            bid = depth.get("buy", [{}])[0].get("price", 0) or current_premium
            ask = depth.get("sell", [{}])[0].get("price", 0) or current_premium

            # Risk check
            allowed, reason = risk_manager.can_enter_trade(
                signal_side=sig.side,
                expected_premium=current_premium,
                current_premium=current_premium,
                bid=bid,
                ask=ask,
            )
            if not allowed:
                log.warning(f"Trade blocked by risk manager: {reason}")
                return

            # Place entry
            signal_id = uuid.uuid4().hex
            self.last_signal_id = signal_id
            self.executor.enter_position(
                side=sig.side,
                option_instrument=option,
                signal_id=signal_id,
            )

        except Exception as e:
            log.exception(f"Error in candle close handler: {e}")
            state.add_error(f"candle_close_error: {e}")

    # =================================================================
    # HELPERS
    # =================================================================

    def _is_market_open(self, now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        t = now.time()
        return self.market_open <= t < self.market_close

    def _install_signal_handlers(self):
        def graceful_shutdown(signum, frame):
            log.info(f"Received signal {signum}. Initiating shutdown.")
            self.running = False
        signal.signal(signal.SIGINT, graceful_shutdown)
        signal.signal(signal.SIGTERM, graceful_shutdown)

    def _emergency_exit(self):
        """Close any open position immediately."""
        if position_tracker.has_open_position():
            log.critical("Emergency exit: closing open position.")
            position_tracker.trigger_exit("KILL_SWITCH")
        self.running = False

    def _shutdown(self):
        log.info("Shutting down...")
        if position_tracker.has_open_position():
            log.warning("Position still open during shutdown. Forcing exit.")
            position_tracker.trigger_exit("SHUTDOWN")
            time.sleep(2)
        if self.md:
            self.md.stop_websocket()
        state.update_state(status="idle", kite_connected=False, ws_connected=False)
        log.info("Shutdown complete.")


def main():
    bot = TradingBot()
    bot.setup()
    bot.run()


if __name__ == "__main__":
    main()
