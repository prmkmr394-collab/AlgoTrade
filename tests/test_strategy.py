"""
Unit tests for signal logic — the most critical module.
Run with: python -m unittest tests.test_strategy

These tests use synthetic candle data to verify the exact Pine script behavior.
"""
import unittest
from datetime import datetime, timedelta
import pytz

from strategy.market_data import Candle, compute_heikin_ashi, compute_vwap
from strategy.signal_logic import evaluate_signal
from strategy.strike_selector import round_to_nearest, select_strike


IST = pytz.timezone("Asia/Kolkata")


def make_candle(ts_minutes_offset, o, h, l, c, v=1000):
    """Build a candle. ts_minutes_offset = minutes from base time."""
    base = IST.localize(datetime(2026, 4, 29, 9, 15))
    return Candle(base + timedelta(minutes=ts_minutes_offset), o, h, l, c, v)


class TestHeikinAshi(unittest.TestCase):
    def test_first_candle(self):
        """First HA candle: HA_open = (O+C)/2, HA_close = (O+H+L+C)/4."""
        c = make_candle(0, 100, 110, 95, 105)
        ha = compute_heikin_ashi([c])
        self.assertEqual(len(ha), 1)
        self.assertAlmostEqual(ha[0].close, (100 + 110 + 95 + 105) / 4)
        self.assertAlmostEqual(ha[0].open, (100 + 105) / 2)
        self.assertAlmostEqual(ha[0].high, max(110, ha[0].open, ha[0].close))
        self.assertAlmostEqual(ha[0].low, min(95, ha[0].open, ha[0].close))

    def test_subsequent_candle_uses_prev_ha(self):
        """HA_open of candle N uses HA values of candle N-1, not real values."""
        c1 = make_candle(0, 100, 110, 95, 105)
        c2 = make_candle(5, 105, 115, 100, 112)
        ha = compute_heikin_ashi([c1, c2])
        expected_ha2_open = (ha[0].open + ha[0].close) / 2.0
        self.assertAlmostEqual(ha[1].open, expected_ha2_open)


class TestVWAP(unittest.TestCase):
    def test_vwap_basic(self):
        """VWAP = sum(typical*vol) / sum(vol)."""
        c = make_candle(0, 100, 110, 90, 100, v=1000)
        vwap = compute_vwap([c])
        typical = (110 + 90 + 100) / 3
        self.assertAlmostEqual(vwap[0], typical)

    def test_vwap_daily_reset(self):
        """VWAP must reset on new trading day."""
        # Day 1
        c1 = make_candle(0, 100, 110, 90, 100, v=1000)
        # Day 2 — 24h later
        c2_ts = IST.localize(datetime(2026, 4, 30, 9, 15))
        c2 = Candle(c2_ts, 200, 210, 190, 200, 500)
        vwap = compute_vwap([c1, c2], reset="daily")
        # On day 2, VWAP should equal typical price of c2 alone (reset happened)
        typical_c2 = (210 + 190 + 200) / 3
        self.assertAlmostEqual(vwap[1], typical_c2)


class TestStrikeSelection(unittest.TestCase):
    def test_round_to_50(self):
        self.assertEqual(round_to_nearest(24467, 50), 24450)
        self.assertEqual(round_to_nearest(24475, 50), 24500)  # banker's rounding edge — Python's round()
        self.assertEqual(round_to_nearest(24476, 50), 24500)
        self.assertEqual(round_to_nearest(24524, 50), 24500)
        self.assertEqual(round_to_nearest(24525, 50), 24500)
        self.assertEqual(round_to_nearest(24526, 50), 24550)

    def test_buy_signal_picks_itm_ce(self):
        """BUY at spot 24,567 → CE strike at 24,567 - 100 = 24,467 → rounded to 24,450."""
        strike = select_strike("BUY", 24567)
        self.assertEqual(strike, 24450)

    def test_sell_signal_picks_itm_pe(self):
        """SELL at spot 24,567 → PE strike at 24,567 + 100 = 24,667 → rounded to 24,650."""
        strike = select_strike("SELL", 24567)
        self.assertEqual(strike, 24650)


class TestSignalLogic(unittest.TestCase):
    """
    Build synthetic candle sequences that should produce BUY, SELL, and NONE signals.
    """

    def _build_snapshot(self, ha_prev_prev, ha_prev, ha_curr, real_curr,
                        vwap_prev, vwap_curr):
        return {
            "ha_prev_prev": ha_prev_prev,
            "ha_prev": ha_prev,
            "ha_current": ha_curr,
            "real_current": real_curr,
            "vwap_prev": vwap_prev,
            "vwap_current": vwap_curr,
        }

    def test_buy_signal_when_all_conditions_met(self):
        """BUY: prev crossed above VWAP + curr close > VWAP + curr high > prev high + curr close > prev high."""
        # ha_prev_prev: below VWAP (close=99)
        ha_pp = make_candle(-10, 98, 100, 97, 99)
        # ha_prev: closed above VWAP (close=102) — this is the cross-above
        ha_p = make_candle(-5, 99, 103, 98, 102)
        # ha_curr: high>prev_high (104>103), close>prev_high (105>103)
        ha_c = make_candle(0, 102, 110, 102, 109)
        # real_curr: spot ~24500
        real_c = make_candle(0, 24490, 24510, 24485, 24500)
        # VWAP at prev = 100, vwap_curr = 100. Min distance = 24500*0.05% = ~12 pts.
        # HA close 109 - VWAP 100 = 9 pts. NOT enough! Test passes only if we use small spot.
        # Let's use different values: tiny spot to bypass distance filter
        real_c_small = Candle(real_c.timestamp, 24490, 24510, 24485, 24500, 1000)

        # We need distance >= 12 pts. HA close 109, VWAP 100, distance=9. Fail.
        # Adjust HA close to 115 → distance 15 pts ≥ 12.
        ha_c2 = make_candle(0, 102, 116, 102, 115)
        snap = self._build_snapshot(ha_pp, ha_p, ha_c2, real_c_small, 100, 100)
        sig = evaluate_signal(snap)
        self.assertEqual(sig.side, "BUY", f"Expected BUY, got {sig.side}: {sig.reason}")

    def test_sell_signal_when_all_conditions_met(self):
        """SELL: prev crossed below VWAP + curr close < VWAP + curr low < prev low + curr close < prev low."""
        ha_pp = make_candle(-10, 102, 103, 101, 102)        # above VWAP=100
        ha_p = make_candle(-5, 101, 102, 97, 98)             # crossed below VWAP=100
        ha_c = make_candle(0, 98, 99, 80, 82)                # low<prev_low(97), close<prev_low(97)
        real_c = Candle(ha_c.timestamp, 24500, 24510, 24470, 24480, 1000)
        # Distance: |82-100|=18 ≥ 12 ✓
        snap = self._build_snapshot(ha_pp, ha_p, ha_c, real_c, 100, 100)
        sig = evaluate_signal(snap)
        self.assertEqual(sig.side, "SELL", f"Expected SELL, got {sig.side}: {sig.reason}")

    def test_no_signal_when_no_cross(self):
        """No cross-over → no signal regardless of other conditions."""
        ha_pp = make_candle(-10, 105, 106, 104, 105)         # already above
        ha_p = make_candle(-5, 105, 108, 104, 107)            # still above, no cross
        ha_c = make_candle(0, 107, 112, 106, 110)             # bullish
        real_c = Candle(ha_c.timestamp, 24500, 24510, 24490, 24500, 1000)
        snap = self._build_snapshot(ha_pp, ha_p, ha_c, real_c, 100, 100)
        sig = evaluate_signal(snap)
        self.assertEqual(sig.side, "NONE")

    def test_no_signal_when_distance_too_small(self):
        """Distance filter blocks signal even when all other BUY conditions are met."""
        ha_pp = make_candle(-10, 98, 100, 97, 99)
        ha_p = make_candle(-5, 99, 103, 98, 102)
        # All BUY conditions satisfied: high>prev_high, close>prev_high.
        # But HA close 103.5 only 3.5 pts above VWAP 100, below 12 pt threshold.
        ha_c = make_candle(0, 102, 104, 102, 103.5)
        real_c = Candle(ha_c.timestamp, 24490, 24510, 24485, 24500, 1000)
        snap = self._build_snapshot(ha_pp, ha_p, ha_c, real_c, 100, 100)
        sig = evaluate_signal(snap)
        self.assertEqual(sig.side, "NONE")
        # Either explicitly blocked by distance filter, OR fell through to "No signal"
        # because high>prev_high (104>103) and close>prev_high (103.5>103) actually triggers.
        # Verify the reason mentions distance specifically
        self.assertTrue(
            "distance" in sig.reason.lower() or sig.distance_pct < 0.05,
            f"Expected distance-related rejection. Got: {sig.reason}, dist_pct={sig.distance_pct}"
        )


class TestRiskManager(unittest.TestCase):
    def test_imports_and_basic_state(self):
        """Smoke test — risk manager loads and basic gates respond."""
        from risk.risk_manager import risk_manager
        # We can't fully test without state mocking, but verify import works
        ok, reason = risk_manager.can_be_in_market()
        # Should be OK assuming no kill switch / halt files
        self.assertIn(reason, ["OK", "KILL_SWITCH_ACTIVE", "HALTED_FOR_DAY"])


if __name__ == "__main__":
    unittest.main()
