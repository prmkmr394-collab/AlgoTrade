"""
EMA + 3-consecutive-candle trend strategy.

Direct translation of your Pine Script v5:

  // 3 consecutive higher candles (each: close>prev_close, low>prev_low, high>prev_high)
  // + close > EMA(20)  → BUY
  
  // 3 consecutive lower candles (each: close<prev_close, low<prev_low, high<prev_high)
  // + close < EMA(20)  → SELL

This is independent from VWAP signal_logic.py. Both modules coexist; the
strategy choice is controlled by config.strategy.signal_type:
  - "vwap_ha"  → original VWAP + Heikin Ashi crossover (signal_logic.evaluate_signal)
  - "ema_3"    → this module (signal_logic_ema.evaluate_signal)

Notes on adapting to options:
  - Pine uses low[1] as stop loss for BUY trades (the prior candle's low).
    For options, the bot doesn't trade Nifty directly — it trades options.
    So the "stop loss" defined here is in NIFTY POINTS (from entry spot).
    The bot's executor still uses option-premium SL/Target from config,
    but the EXIT PRICE is monitored against the spot SL level when this
    strategy is active.
  - For the first phase (paper backtest), we use the standard premium-based
    SL/Target from config to keep math comparable to VWAP backtest.
"""
from dataclasses import dataclass
from collections import deque
from typing import Optional, List
from utils.config_loader import config


@dataclass
class EmaSignal:
    side: str                   # "BUY" | "SELL" | "NONE"
    timestamp: object
    close: float
    ema: float
    distance_pct: float
    suggested_sl_spot: float    # Pine's low[1] (BUY) or high[1] (SELL)
    reason: str


# EMA stateful computation across calls
class EmaCalculator:
    """Maintains EMA state. Call update(price) for each candle close."""
    def __init__(self, period: int):
        self.period = period
        self.alpha = 2.0 / (period + 1)
        self.value: Optional[float] = None

    def update(self, price: float) -> float:
        if self.value is None:
            self.value = price
        else:
            self.value = self.value * (1 - self.alpha) + price * self.alpha
        return self.value

    def reset(self):
        self.value = None


def compute_ema_series(closes: List[float], period: int) -> List[float]:
    """Compute EMA over a list of closes. Returns list of same length."""
    if not closes:
        return []
    alpha = 2.0 / (period + 1)
    result = [closes[0]]
    for i in range(1, len(closes)):
        result.append(result[-1] * (1 - alpha) + closes[i] * alpha)
    return result


def evaluate_signal(market_snapshot: dict) -> EmaSignal:
    """
    Evaluate the EMA + 3-consecutive-candle strategy.

    Required keys in market_snapshot:
      - candles: list of last 4+ candles (most recent last)
      - ema_current: latest EMA value
      - ema_period: from config (for logging)

    Returns EmaSignal.
    """
    candles = market_snapshot.get("candles") or []
    ema_current = market_snapshot.get("ema_current")
    ema_period = market_snapshot.get("ema_period", 20)

    if len(candles) < 4 or ema_current is None:
        last = candles[-1] if candles else None
        return EmaSignal(
            side="NONE",
            timestamp=last.timestamp if last else None,
            close=last.close if last else 0.0,
            ema=ema_current or 0.0,
            distance_pct=0.0,
            suggested_sl_spot=0.0,
            reason="Insufficient candle history",
        )

    # Pine indexing: c[0] is current, c[1] is prev, c[2] is prev-prev, c[3] is prev-prev-prev
    # In Python list (oldest first), candles[-1] is current, candles[-2] is prev, etc.
    c0 = candles[-1]   # current (just-closed candle)
    c1 = candles[-2]   # prev
    c2 = candles[-3]   # prev-prev
    c3 = candles[-4]   # prev-prev-prev

    # ---- BUY conditions (3 consecutive higher candles, each strictly higher OHLC) ----
    cond1_up = c0.close > c1.close and c0.low > c1.low and c0.high > c1.high
    cond2_up = c1.close > c2.close and c1.low > c2.low and c1.high > c2.high
    cond3_up = c2.close > c3.close and c2.low > c3.low and c2.high > c3.high
    uptrend_confirm = True

    buy_signal = cond1_up and cond2_up and cond3_up and uptrend_confirm

    # ---- SELL conditions (3 consecutive lower candles) ----
    cond1_dn = c0.close < c1.close and c0.low < c1.low and c0.high < c1.high
    cond2_dn = c1.close < c2.close and c1.low < c2.low and c1.high < c2.high
    cond3_dn = c2.close < c3.close and c2.low < c3.low and c2.high < c3.high
    downtrend_confirm = True

    sell_signal = cond1_dn and cond2_dn and cond3_dn and downtrend_confirm

    # Distance from EMA in % of spot (for optional filtering)
    distance = abs(c0.close - ema_current)
    distance_pct = (distance / c0.close * 100.0) if c0.close > 0 else 0.0

    # Min distance filter (optional)
    min_dist_pct = config.get("strategy", "min_distance_from_ema_pct", default=0.0)
    distance_ok = distance_pct >= min_dist_pct

    if buy_signal and distance_ok:
        return EmaSignal(
            side="BUY",
            timestamp=c0.timestamp,
            close=c0.close,
            ema=ema_current,
            distance_pct=distance_pct,
            suggested_sl_spot=c1.low,   # Pine: stopLossBuy = low[1]
            reason=(f"BUY: 3 consecutive higher candles, close {c0.close:.2f} > "
                    f"EMA{ema_period} {ema_current:.2f}, distance {distance_pct:.3f}%"),
        )

    if sell_signal and distance_ok:
        return EmaSignal(
            side="SELL",
            timestamp=c0.timestamp,
            close=c0.close,
            ema=ema_current,
            distance_pct=distance_pct,
            suggested_sl_spot=c1.high,  # Pine: stopLossSell = high[1]
            reason=(f"SELL: 3 consecutive lower candles, close {c0.close:.2f} < "
                    f"EMA{ema_period} {ema_current:.2f}, distance {distance_pct:.3f}%"),
        )

    if buy_signal and not distance_ok:
        reason = f"BUY blocked: distance {distance_pct:.3f}% < {min_dist_pct}% threshold"
    elif sell_signal and not distance_ok:
        reason = f"SELL blocked: distance {distance_pct:.3f}% < {min_dist_pct}% threshold"
    else:
        # Explain why no signal — useful for debug
        if not (cond1_up and cond2_up and cond3_up) and not (cond1_dn and cond2_dn and cond3_dn):
            reason = "No signal — not 3 consecutive trending candles"
        elif buy_signal and not uptrend_confirm:
            reason = f"BUY rejected — close {c0.close:.2f} not > EMA {ema_current:.2f}"
        elif sell_signal and not downtrend_confirm:
            reason = f"SELL rejected — close {c0.close:.2f} not < EMA {ema_current:.2f}"
        else:
            reason = "No signal"

    return EmaSignal(
        side="NONE",
        timestamp=c0.timestamp,
        close=c0.close,
        ema=ema_current,
        distance_pct=distance_pct,
        suggested_sl_spot=0.0,
        reason=reason,
    )
