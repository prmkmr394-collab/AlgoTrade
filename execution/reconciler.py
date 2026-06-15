"""
Position reconciler.

Periodically compares the bot's internal state vs Kite's actual position book.
Detects:
  - Orphaned positions (Kite shows a position the bot doesn't know about)
  - Missing positions (bot thinks it has a position but Kite shows nothing)
  - Quantity mismatches
  - Manual interventions (user placed an order outside the bot)

On any mismatch, the bot HALTS for the day and alerts. Trading resumes only
after manual review and explicit reset by the user.

In paper mode, reconciler is mostly a no-op since there are no real positions.
"""
from datetime import datetime
from threading import Lock
from typing import Optional
import pytz

from utils.config_loader import config
from utils.logger import log
from utils import state
from utils.kill_switch import halt_for_day
from strategy.position_tracker import position_tracker


IST = pytz.timezone("Asia/Kolkata")


class Reconciler:
    def __init__(self, kite, executor):
        self.kite = kite
        self.executor = executor
        self._lock = Lock()
        self._last_check: Optional[datetime] = None
        self._mismatch_count = 0
        # Allow 2 transient mismatches before halting (orders in flight, etc.)
        self.mismatch_tolerance = 2

    def reconcile(self) -> bool:
        """
        Run a single reconciliation pass.
        Returns True if state matches, False if mismatch.
        """
        with self._lock:
            self._last_check = datetime.now(IST)

            # Paper mode: nothing to reconcile
            if self.executor.is_paper():
                return True

            try:
                kite_positions = self.kite.positions()
            except Exception as e:
                log.warning(f"Reconciler: failed to fetch positions: {e}")
                return True  # Don't halt on transient API errors

            # Get NFO positions only, with non-zero quantity
            net_positions = [
                p for p in kite_positions.get("net", [])
                if p.get("exchange") == "NFO" and p.get("quantity", 0) != 0
            ]

            bot_pos = position_tracker.get_position()
            bot_has_open = bot_pos is not None and bot_pos.is_open

            # ---- CASE 1: Bot has no position, Kite has none ----
            if not bot_has_open and len(net_positions) == 0:
                self._mismatch_count = 0
                return True

            # ---- CASE 2: Bot has position, Kite has matching position ----
            if bot_has_open and len(net_positions) == 1:
                kite_p = net_positions[0]
                if (kite_p.get("tradingsymbol") == bot_pos.tradingsymbol
                        and abs(kite_p.get("quantity", 0)) == bot_pos.quantity):
                    self._mismatch_count = 0
                    return True

            # ---- MISMATCH ----
            self._mismatch_count += 1
            mismatch_desc = (
                f"Bot open: {bot_has_open}; "
                f"Bot symbol: {bot_pos.tradingsymbol if bot_has_open else 'none'}; "
                f"Bot qty: {bot_pos.quantity if bot_has_open else 0}; "
                f"Kite NFO positions: {len(net_positions)} "
                f"({[(p['tradingsymbol'], p['quantity']) for p in net_positions]})"
            )
            log.warning(f"Reconciler MISMATCH (count={self._mismatch_count}): {mismatch_desc}")
            state.add_error(f"reconciler_mismatch: {mismatch_desc}")

            if self._mismatch_count >= self.mismatch_tolerance:
                msg = (f"Reconciler halted bot — persistent state mismatch. "
                       f"Manual review required. {mismatch_desc}")
                log.critical(msg)
                halt_for_day(msg)
                return False

            return False
