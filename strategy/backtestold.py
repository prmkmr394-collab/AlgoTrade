"""
Backtest engine.

Replays historical Nifty data through the EXACT same strategy logic
that runs in live trading. Simulates entries, SL/Target, time exits.

Important notes about backtest fidelity:
  1. We use Nifty FUTURES/SPOT price for signal generation (correct).
  2. We approximate option premium movement using Nifty point movement.
     - Rule of thumb: ITM options have delta ~0.65-0.75
     - 1 Nifty point ≈ 0.7 option points for ITM-100
     - We use delta=0.7 as approximation. Real options also have theta + vega,
       but for short-duration intraday trades these are minor.
  3. SL/Target hits are evaluated using Nifty point movement scaled by delta.
  4. Slippage assumed: 1 point on entry, 1 point on exit (conservative).
  5. Brokerage + STT + GST: ~₹40 per trade (Zerodha intraday options).

This is a CONSERVATIVE backtest. Real-world results may be slightly worse
due to spread, latency, and rare option-specific events (theta decay on
slow-moving days, IV crush on event days).
"""
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from typing import List, Optional, Dict
import pytz

from strategy.market_data import Candle, compute_heikin_ashi, compute_vwap
from strategy.signal_logic import evaluate_signal as evaluate_signal_vwap
from strategy.signal_logic_ema import evaluate_signal as evaluate_signal_ema, compute_ema_series
from utils.config_loader import config
from utils.logger import log


IST = pytz.timezone("Asia/Kolkata")


# Backtest assumptions
OPTION_DELTA = 0.70           # ITM-100 option delta approximation
SLIPPAGE_PER_LEG = 1.0        # 1 point each side
COSTS_PER_TRADE = 40.0        # brokerage + STT + GST


@dataclass
class BacktestTrade:
    entry_time: datetime
    exit_time: datetime
    side: str                  # "BUY" or "SELL"
    spot_entry: float
    spot_exit: float
    nifty_points_moved: float  # signed: + if favorable, - if adverse
    option_points_moved: float
    pnl_per_unit: float
    qty: int
    gross_pnl: float
    costs: float
    net_pnl: float
    exit_reason: str
    duration_minutes: float


@dataclass
class BacktestResult:
    trades: List[BacktestTrade] = field(default_factory=list)
    starting_capital: float = 40000
    final_capital: float = 40000
    skipped_signals: int = 0
    blocked_by_window: int = 0
    blocked_by_loss_cap: int = 0
    blocked_by_max_trades: int = 0
    blocked_by_reentry: int = 0
    blocked_by_distance: int = 0


class Backtester:
    def __init__(self, real_candles: List[Candle]):
        self.real = real_candles
        self.ha = compute_heikin_ashi(real_candles)
        self.vwap = compute_vwap(real_candles, reset="daily")

        # EMA series — used by ema_3 strategy
        ema_period = config.get("strategy", "ema_period", default=20)
        # Apply EMA on HA close if HA is enabled, else on real close
        use_ha = config.get("strategy", "use_heikin_ashi", default=True)
        ema_input = [c.close for c in (self.ha if use_ha else self.real)]
        self.ema = compute_ema_series(ema_input, ema_period)
        self.ema_period = ema_period

        # Strategy choice
        self.signal_type = config.get("strategy", "signal_type", default="vwap_ha").lower()
        log.info(f"Backtest using signal_type={self.signal_type}")

        # Strategy params
        self.lot_size = config.get("strategy", "lot_size", default=75)
        self.lots = config.get("strategy", "lots_per_trade", default=1)
        self.qty = self.lot_size * self.lots
        self.sl_pts = config.get("risk", "stop_loss_points", default=12)
        self.tgt_pts = config.get("risk", "target_points", default=12)
        self.max_trades_day = config.get("risk", "max_trades_per_day", default=4)
        self.daily_loss_cap = config.get("risk", "daily_loss_cap", default=1500)
        self.reentry_block_min = config.get("risk", "reentry_block_minutes", default=30)

        self.entries_start = self._parse_time(config.get("trading_hours", "entries_start", default="09:30"))
        self.entries_stop = self._parse_time(config.get("trading_hours", "entries_stop", default="14:45"))
        self.hard_squareoff = self._parse_time(config.get("trading_hours", "hard_square_off", default="15:15"))

    @staticmethod
    def _parse_time(s: str) -> dt_time:
        h, m = s.split(":")
        return dt_time(int(h), int(m))

    def run(self, starting_capital: float = 40000) -> BacktestResult:
        result = BacktestResult(starting_capital=starting_capital, final_capital=starting_capital)

        # Per-day state
        current_day = None
        daily_pnl = 0.0
        daily_trades = 0
        last_sl_time = None
        last_sl_side = None

        # Open position state
        in_trade = False
        entry_idx = None
        entry_spot = None
        entry_time = None
        side = None

        for i in range(2, len(self.real)):
            real_curr = self.real[i]
            ts_ist = real_curr.timestamp.astimezone(IST) if real_curr.timestamp.tzinfo else IST.localize(real_curr.timestamp)
            day = ts_ist.date()

            # New day reset
            if day != current_day:
                current_day = day
                daily_pnl = 0.0
                daily_trades = 0
                last_sl_time = None
                last_sl_side = None
                # If still in trade from yesterday (shouldn't happen with hard squareoff, but safety)
                if in_trade:
                    in_trade = False

            t = ts_ist.time()

            # ---- Check exit conditions if in trade ----
            if in_trade:
                # Time-based exit
                if t >= self.hard_squareoff:
                    trade = self._close_trade(entry_idx, i, side, entry_spot, real_curr.close,
                                               entry_time, ts_ist, "TIME_EXIT")
                    result.trades.append(trade)
                    daily_pnl += trade.net_pnl
                    in_trade = False
                    continue

                # SL / Target check on this candle's high/low
                # If BUY (long CE): we profit when spot goes up. SL hit if low moves enough against us.
                # If SELL (long PE): we profit when spot goes down. SL hit if high moves up against us.
                if side == "BUY":
                    adverse_move_pts = entry_spot - real_curr.low      # positive = adverse
                    favorable_move_pts = real_curr.high - entry_spot   # positive = favorable
                else:  # SELL
                    adverse_move_pts = real_curr.high - entry_spot
                    favorable_move_pts = entry_spot - real_curr.low

                # Convert nifty points to option points using delta
                adverse_opt_pts = adverse_move_pts * OPTION_DELTA
                favorable_opt_pts = favorable_move_pts * OPTION_DELTA

                if adverse_opt_pts >= self.sl_pts and favorable_opt_pts >= self.tgt_pts:
                    # Both hit in same candle — assume worst (SL first) for conservative backtest
                    exit_spot = entry_spot - (self.sl_pts / OPTION_DELTA) if side == "BUY" else entry_spot + (self.sl_pts / OPTION_DELTA)
                    trade = self._close_trade(entry_idx, i, side, entry_spot, exit_spot,
                                               entry_time, ts_ist, "STOP_LOSS")
                    result.trades.append(trade)
                    daily_pnl += trade.net_pnl
                    last_sl_time = ts_ist
                    last_sl_side = side
                    in_trade = False
                    continue
                elif adverse_opt_pts >= self.sl_pts:
                    exit_spot = entry_spot - (self.sl_pts / OPTION_DELTA) if side == "BUY" else entry_spot + (self.sl_pts / OPTION_DELTA)
                    trade = self._close_trade(entry_idx, i, side, entry_spot, exit_spot,
                                               entry_time, ts_ist, "STOP_LOSS")
                    result.trades.append(trade)
                    daily_pnl += trade.net_pnl
                    last_sl_time = ts_ist
                    last_sl_side = side
                    in_trade = False
                    continue
                elif favorable_opt_pts >= self.tgt_pts:
                    exit_spot = entry_spot + (self.tgt_pts / OPTION_DELTA) if side == "BUY" else entry_spot - (self.tgt_pts / OPTION_DELTA)
                    trade = self._close_trade(entry_idx, i, side, entry_spot, exit_spot,
                                               entry_time, ts_ist, "TARGET")
                    result.trades.append(trade)
                    daily_pnl += trade.net_pnl
                    in_trade = False
                    continue

                # Still in trade, check next candle
                continue

            # ---- Look for new signal ----
            if t < self.entries_start or t >= self.entries_stop:
                continue

            if daily_trades >= self.max_trades_day:
                result.blocked_by_max_trades += 1
                continue

            if daily_pnl <= -self.daily_loss_cap:
                result.blocked_by_loss_cap += 1
                continue

            # Build snapshot for signal evaluation
            if self.signal_type == "ema_3":
                # EMA+3 strategy needs last 4 candles + current EMA
                use_ha = config.get("strategy", "use_heikin_ashi", default=True)
                source_candles = self.ha if use_ha else self.real
                if i < 3 or i >= len(self.ema):
                    continue
                snap = {
                    "candles": [source_candles[i-3], source_candles[i-2],
                                source_candles[i-1], source_candles[i]],
                    "ema_current": self.ema[i],
                    "ema_period": self.ema_period,
                }
                sig = evaluate_signal_ema(snap)
            else:
                # VWAP+HA strategy
                snap = {
                    "ha_current": self.ha[i],
                    "ha_prev": self.ha[i-1],
                    "ha_prev_prev": self.ha[i-2],
                    "real_current": real_curr,
                    "vwap_current": self.vwap[i],
                    "vwap_prev": self.vwap[i-1],
                }
                sig = evaluate_signal_vwap(snap)
            if sig.side == "NONE":
                if "distance" in sig.reason.lower():
                    result.blocked_by_distance += 1
                continue

            # Re-entry block
            if last_sl_time and last_sl_side == sig.side:
                elapsed_min = (ts_ist - last_sl_time).total_seconds() / 60.0
                if elapsed_min < self.reentry_block_min:
                    result.blocked_by_reentry += 1
                    continue

            # Enter trade
            in_trade = True
            entry_idx = i
            entry_spot = real_curr.close + (SLIPPAGE_PER_LEG if sig.side == "BUY" else -SLIPPAGE_PER_LEG)
            entry_time = ts_ist
            side = sig.side
            daily_trades += 1
            result.skipped_signals = result.skipped_signals  # placeholder

        result.final_capital = starting_capital + sum(t.net_pnl for t in result.trades)
        return result

    def _close_trade(self, entry_idx: int, exit_idx: int, side: str,
                     entry_spot: float, exit_spot_raw: float,
                     entry_time: datetime, exit_time: datetime,
                     reason: str) -> BacktestTrade:
        """Construct a closed trade record."""
        # Apply exit-side slippage
        exit_spot = exit_spot_raw - SLIPPAGE_PER_LEG if side == "BUY" else exit_spot_raw + SLIPPAGE_PER_LEG

        if side == "BUY":
            nifty_pts = exit_spot - entry_spot
        else:
            nifty_pts = entry_spot - exit_spot

        opt_pts = nifty_pts * OPTION_DELTA
        gross = opt_pts * self.qty
        net = gross - COSTS_PER_TRADE
        duration = (exit_time - entry_time).total_seconds() / 60.0

        return BacktestTrade(
            entry_time=entry_time,
            exit_time=exit_time,
            side=side,
            spot_entry=round(entry_spot, 2),
            spot_exit=round(exit_spot, 2),
            nifty_points_moved=round(nifty_pts, 2),
            option_points_moved=round(opt_pts, 2),
            pnl_per_unit=round(opt_pts, 2),
            qty=self.qty,
            gross_pnl=round(gross, 2),
            costs=COSTS_PER_TRADE,
            net_pnl=round(net, 2),
            exit_reason=reason,
            duration_minutes=round(duration, 1),
        )


def print_report(result: BacktestResult):
    """Print a comprehensive backtest report."""
    trades = result.trades
    n = len(trades)

    print("\n" + "=" * 80)
    print(f" BACKTEST REPORT — {n} trades")
    print("=" * 80)

    if n == 0:
        print(" No trades generated. Strategy did not fire any signals.")
        print(f" Blocked by distance filter: {result.blocked_by_distance}")
        print(f" Blocked by max trades:      {result.blocked_by_max_trades}")
        print(f" Blocked by loss cap:        {result.blocked_by_loss_cap}")
        print(f" Blocked by re-entry:        {result.blocked_by_reentry}")
        print("=" * 80)
        return

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    win_rate = len(wins) / n * 100

    total_pnl = sum(t.net_pnl for t in trades)
    avg_win = sum(t.net_pnl for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.net_pnl for t in losses) / len(losses) if losses else 0
    profit_factor = abs(sum(t.net_pnl for t in wins) / sum(t.net_pnl for t in losses)) if losses and sum(t.net_pnl for t in losses) != 0 else float('inf')

    # Drawdown calculation
    equity = [result.starting_capital]
    for t in trades:
        equity.append(equity[-1] + t.net_pnl)
    peak = equity[0]
    max_dd = 0
    for eq in equity:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    # Exit reason breakdown
    sl_count = sum(1 for t in trades if t.exit_reason == "STOP_LOSS")
    tgt_count = sum(1 for t in trades if t.exit_reason == "TARGET")
    time_count = sum(1 for t in trades if t.exit_reason == "TIME_EXIT")

    # Date range
    first_dt = trades[0].entry_time
    last_dt = trades[-1].entry_time
    days_span = (last_dt - first_dt).days + 1
    trades_per_day = n / max(days_span, 1)

    print(f" Period:            {first_dt.date()} to {last_dt.date()} ({days_span} days)")
    print(f" Trades/day avg:    {trades_per_day:.2f}")
    print()
    print(f" Total trades:      {n}")
    print(f" Wins:              {len(wins)} ({win_rate:.1f}%)")
    print(f" Losses:            {len(losses)} ({100-win_rate:.1f}%)")
    print()
    print(f" Starting capital:  ₹{result.starting_capital:,.0f}")
    print(f" Final capital:     ₹{result.final_capital:,.0f}")
    print(f" Total P&L:         ₹{total_pnl:+,.2f}")
    print(f" Return:            {total_pnl/result.starting_capital*100:+.2f}%")
    print(f" Max drawdown:      ₹{max_dd:,.2f} ({max_dd/result.starting_capital*100:.2f}%)")
    print()
    print(f" Avg win:           ₹{avg_win:+,.2f}")
    print(f" Avg loss:          ₹{avg_loss:+,.2f}")
    print(f" Profit factor:     {profit_factor:.2f}")
    print()
    print(f" Exit reasons:")
    print(f"   Target hit:      {tgt_count} ({tgt_count/n*100:.1f}%)")
    print(f"   Stop loss hit:   {sl_count} ({sl_count/n*100:.1f}%)")
    print(f"   Time exit:       {time_count} ({time_count/n*100:.1f}%)")
    print()
    print(f" Filters triggered:")
    print(f"   Distance filter blocked:  {result.blocked_by_distance}")
    print(f"   Max trades/day blocked:   {result.blocked_by_max_trades}")
    print(f"   Loss cap blocked:         {result.blocked_by_loss_cap}")
    print(f"   Re-entry blocked:         {result.blocked_by_reentry}")
    print("=" * 80)

    # Verdict
    print()
    if total_pnl > 0 and win_rate >= 45 and max_dd < result.starting_capital * 0.5:
        print(" ✅ VERDICT: Strategy is potentially viable. Recommend paper trading next.")
    elif total_pnl > 0 and max_dd >= result.starting_capital * 0.5:
        print(" ⚠️  VERDICT: Profitable but high drawdown. Consider tighter risk controls.")
    elif total_pnl <= 0:
        print(" ❌ VERDICT: Strategy lost money on backtest. Do NOT deploy live.")
        print("    Consider: wider SL/Target, different distance filter, or different signal.")
    print("=" * 80)
    print()


def save_trades_csv(result: BacktestResult, path: str):
    """Save all trades to a CSV for manual review."""
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "entry_time", "exit_time", "side", "duration_min",
            "spot_entry", "spot_exit", "nifty_pts", "option_pts",
            "qty", "gross_pnl", "costs", "net_pnl", "exit_reason",
        ])
        for t in result.trades:
            w.writerow([
                t.entry_time.strftime("%Y-%m-%d %H:%M"),
                t.exit_time.strftime("%Y-%m-%d %H:%M"),
                t.side, t.duration_minutes,
                t.spot_entry, t.spot_exit,
                t.nifty_points_moved, t.option_points_moved,
                t.qty, t.gross_pnl, t.costs, t.net_pnl, t.exit_reason,
            ])
    print(f" Trades saved to: {path}")
