"""
Strategy / signal logic — v3 (Simple trend-following).

Pure functional module — takes candle data in, returns a signal out.
No I/O, no side effects, no Kite calls. This makes it 100% unit-testable.

Signal logic (v3):
  - BUY  when HA close > VWAP (price is above value — trend-following)
  - SELL when HA close < VWAP (price is below value — trend-following)

This is intentionally simple. The edge comes from the EXIT side (trailing SL
in backtest.py / trade_manager.py), not from over-filtering entries.

Filters retained:
  1. Min distance from VWAP — reject signals too close to VWAP (chop zone)
  2. Opening rotation skip — VWAP unreliable in first N minutes
  3. HA body-size filter   — reject dojis / indecisive candles (optional)

Removed (v2 → v3):
  - VWAP crossover gate (killed win rate — entries came too late)
  - HA momentum confirmation (high > prev high / low < prev low)
  - VWAP slope filter (redundant when signal is simple trend-following)
"""
from dataclasses import dataclass
from datetime import time as dt_time
from utils.config_loader import config


@dataclass
class Signal:
    side: str                   # "BUY" | "SELL" | "NONE"
    timestamp: object           # datetime of the signal candle
    ha_close: float
    real_close: float
    vwap: float
    distance_pct: float         # |HA_close - VWAP| / spot * 100
    reason: str                 # human-readable explanation


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _ha_body_ratio(candle) -> float:
    """
    Ratio of HA body to total candle range.
    Returns 0.0-1.0. A doji ~ 0.0, a strong candle ~ 0.8-1.0.
    """
    total_range = candle.high - candle.low
    if total_range <= 0:
        return 0.0
    body = abs(candle.close - candle.open)
    return body / total_range


def _in_opening_rotation(timestamp, skip_until: dt_time) -> bool:
    """
    Returns True if the candle is within the opening rotation period.
    """
    try:
        t = timestamp.time() if hasattr(timestamp, 'time') else timestamp
        return t < skip_until
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main signal evaluation
# ---------------------------------------------------------------------------

def evaluate_signal(market_snapshot: dict) -> Signal:
    """
    Evaluate the strategy on the latest closed candle.

    market_snapshot expected keys (from MarketDataManager.get_latest()):
      - ha_current, ha_prev, ha_prev_prev   (Candle objects, HA)
      - real_current                         (Candle object, real)
      - vwap_current, vwap_prev              (floats)

    Returns Signal with side="BUY" | "SELL" | "NONE".
    """
    ha_curr = market_snapshot["ha_current"]
    ha_prev = market_snapshot["ha_prev"]
    real_curr = market_snapshot["real_current"]
    vwap_curr = market_snapshot["vwap_current"]

    spot = real_curr.close

    # =====================================================================
    # Load filter config
    # =====================================================================
    min_dist_pct = config.get("strategy", "min_distance_from_vwap_pct", default=0.05)
    min_ha_body_ratio = config.get("strategy", "min_ha_body_ratio", default=0.0)
    skip_opening_minutes = config.get("strategy", "skip_opening_minutes", default=0)

    # =====================================================================
    # FILTER: Opening rotation skip
    # =====================================================================
    if skip_opening_minutes > 0:
        skip_h = 9 + (15 + skip_opening_minutes) // 60
        skip_m = (15 + skip_opening_minutes) % 60
        skip_until = dt_time(skip_h, skip_m)
        if _in_opening_rotation(ha_curr.timestamp, skip_until):
            return Signal("NONE", ha_curr.timestamp, ha_curr.close, real_curr.close,
                          vwap_curr, 0.0,
                          f"Opening rotation: skipping first {skip_opening_minutes}min")

    # =====================================================================
    # FILTER: Min distance from VWAP
    # =====================================================================
    min_dist_points = spot * (min_dist_pct / 100.0)
    distance_now = abs(ha_curr.close - vwap_curr)
    distance_pct_now = (distance_now / spot * 100.0) if spot > 0 else 0.0
    distance_ok = distance_now >= min_dist_points

    # =====================================================================
    # FILTER: HA body-size (optional — set min_ha_body_ratio > 0 to enable)
    # =====================================================================
    body_ratio = _ha_body_ratio(ha_curr)
    body_ok = body_ratio >= min_ha_body_ratio

    # =====================================================================
    # Signal: simple HA close vs VWAP
    # =====================================================================
    buy_signal = ha_curr.close > vwap_curr
    sell_signal = ha_curr.close < vwap_curr

    # =====================================================================
    # Decision
    # =====================================================================
    if buy_signal and distance_ok and body_ok:
        return Signal(
            side="BUY",
            timestamp=ha_curr.timestamp,
            ha_close=ha_curr.close,
            real_close=real_curr.close,
            vwap=vwap_curr,
            distance_pct=distance_pct_now,
            reason=(f"BUY: HA close {ha_curr.close:.2f} > VWAP {vwap_curr:.2f}, "
                    f"distance {distance_pct_now:.3f}% >= {min_dist_pct}%"),
        )

    if sell_signal and distance_ok and body_ok:
        return Signal(
            side="SELL",
            timestamp=ha_curr.timestamp,
            ha_close=ha_curr.close,
            real_close=real_curr.close,
            vwap=vwap_curr,
            distance_pct=distance_pct_now,
            reason=(f"SELL: HA close {ha_curr.close:.2f} < VWAP {vwap_curr:.2f}, "
                    f"distance {distance_pct_now:.3f}% >= {min_dist_pct}%"),
        )

    # No trade — explain why
    reasons = []
    if not buy_signal and not sell_signal:
        reasons.append("HA close == VWAP (no direction)")
    if not distance_ok:
        reasons.append(f"distance {distance_pct_now:.3f}% < {min_dist_pct}%")
    if not body_ok:
        reasons.append(f"HA body ratio {body_ratio:.2f} < {min_ha_body_ratio}")

    reason_str = "; ".join(reasons) if reasons else "No signal"

    return Signal("NONE", ha_curr.timestamp, ha_curr.close, real_curr.close,
                  vwap_curr, distance_pct_now, reason_str)
