"""
Market data module.

Responsibilities:
- Fetch historical OHLCV candles from Kite (for VWAP backfill on bot startup)
- Subscribe to live ticks via Kite WebSocket
- Aggregate ticks into 5-minute candles
- Compute Heikin Ashi candles from real OHLC
- Compute VWAP from real (not HA) OHLC + volume, with daily reset
- Maintain a rolling buffer of the last N candles for the strategy to consume

Design notes:
- VWAP is computed on REAL price (not HA). HA is only for signal evaluation.
- Two parallel candle series are maintained: real OHLC and HA OHLC.
- Daily VWAP reset happens automatically at start of each trading day.
"""
from collections import deque
from datetime import datetime, timedelta, time as dt_time
from threading import Lock
from typing import Optional, Callable, List, Dict
import pandas as pd
import pytz

from kiteconnect import KiteTicker
from utils.config_loader import config
from utils.logger import log
from utils import state


IST = pytz.timezone("Asia/Kolkata")


class Candle:
    """Single OHLCV candle."""
    __slots__ = ("timestamp", "open", "high", "low", "close", "volume")

    def __init__(self, timestamp: datetime, o: float, h: float, l: float, c: float, v: float):
        self.timestamp = timestamp
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v

    def __repr__(self):
        return f"Candle({self.timestamp.strftime('%H:%M')}, O={self.open}, H={self.high}, L={self.low}, C={self.close}, V={self.volume})"


def compute_heikin_ashi(real_candles: List[Candle]) -> List[Candle]:
    """
    Convert real OHLC candles to Heikin Ashi.

    HA_Close = (O + H + L + C) / 4
    HA_Open  = (prev_HA_Open + prev_HA_Close) / 2     [first candle: (O+C)/2]
    HA_High  = max(High, HA_Open, HA_Close)
    HA_Low   = min(Low, HA_Open, HA_Close)

    Volume is preserved (HA doesn't change volume).
    """
    if not real_candles:
        return []

    ha_candles: List[Candle] = []
    for i, c in enumerate(real_candles):
        ha_close = (c.open + c.high + c.low + c.close) / 4.0
        if i == 0:
            ha_open = (c.open + c.close) / 2.0
        else:
            prev = ha_candles[-1]
            ha_open = (prev.open + prev.close) / 2.0
        ha_high = max(c.high, ha_open, ha_close)
        ha_low = min(c.low, ha_open, ha_close)
        ha_candles.append(Candle(c.timestamp, ha_open, ha_high, ha_low, ha_close, c.volume))
    return ha_candles


def compute_vwap(real_candles: List[Candle], reset: str = "daily") -> List[float]:
    """
    Compute VWAP from REAL OHLC + volume.
    Reset behavior:
      - 'daily' : new VWAP each trading day (recommended)
      - 'hourly': new VWAP each hour
    """
    if not real_candles:
        return []

    vwap_series: List[float] = []
    cum_vol = 0.0
    cum_pv = 0.0
    last_key = None

    for c in real_candles:
        ts_ist = c.timestamp.astimezone(IST) if c.timestamp.tzinfo else IST.localize(c.timestamp)
        if reset == "daily":
            key = ts_ist.date()
        elif reset == "hourly":
            key = (ts_ist.date(), ts_ist.hour)
        else:
            key = ts_ist.date()

        if key != last_key:
            cum_vol = 0.0
            cum_pv = 0.0
            last_key = key

        typical_price = (c.high + c.low + c.close) / 3.0
        cum_pv += typical_price * c.volume
        cum_vol += c.volume
        vwap = cum_pv / cum_vol if cum_vol > 0 else c.close
        vwap_series.append(vwap)

    return vwap_series


class CandleAggregator:
    """
    Aggregates incoming ticks into N-minute candles.
    Emits a completed candle to the registered callback when each candle closes.
    """

    def __init__(self, timeframe_minutes: int, on_candle_close: Callable[[Candle], None]):
        self.tf_minutes = timeframe_minutes
        self.on_candle_close = on_candle_close
        self._current: Optional[Dict] = None
        self._lock = Lock()

    def _bucket_start(self, ts: datetime) -> datetime:
        """Round timestamp down to nearest tf_minutes boundary in IST."""
        ts_ist = ts.astimezone(IST) if ts.tzinfo else IST.localize(ts)
        minute = (ts_ist.minute // self.tf_minutes) * self.tf_minutes
        return ts_ist.replace(minute=minute, second=0, microsecond=0)

    def on_tick(self, price: float, volume: float, ts: datetime):
        with self._lock:
            bucket = self._bucket_start(ts)

            if self._current is None:
                self._start_new(bucket, price, volume)
                return

            if bucket == self._current["bucket"]:
                self._current["high"] = max(self._current["high"], price)
                self._current["low"] = min(self._current["low"], price)
                self._current["close"] = price
                self._current["volume"] += volume
            else:
                self._emit_current()
                self._start_new(bucket, price, volume)

    def _start_new(self, bucket: datetime, price: float, volume: float):
        self._current = {
            "bucket": bucket,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": volume,
        }

    def _emit_current(self):
        c = self._current
        candle = Candle(c["bucket"], c["open"], c["high"], c["low"], c["close"], c["volume"])
        try:
            self.on_candle_close(candle)
        except Exception as e:
            log.exception(f"Candle close handler raised: {e}")

    def force_close(self):
        """Force-close current candle (e.g., at market close)."""
        with self._lock:
            if self._current is not None:
                self._emit_current()
                self._current = None


class MarketDataManager:
    """
    Holds a rolling buffer of real candles + HA candles + VWAP.
    Single source of truth for the strategy module.
    """

    def __init__(self, kite, futures_token: int, max_candles: int = 200, use_spot: bool = False):
        self.kite = kite
        self.futures_token = futures_token       # actually "underlying_token" — futures or spot
        self.use_spot = use_spot                  # True if subscribing to NIFTY 50 index (no volume)
        self.max_candles = max_candles

        self.real_candles: deque = deque(maxlen=max_candles)
        self.ha_candles: deque = deque(maxlen=max_candles)
        self.vwap_series: deque = deque(maxlen=max_candles)

        self._lock = Lock()
        self._listeners: List[Callable[[Candle, Candle, float], None]] = []

        tf = config.get("strategy", "timeframe_minutes", default=5)
        self.aggregator = CandleAggregator(tf, self._on_new_candle)

        # Live tick storage
        self.last_ltp: Optional[float] = None
        self.last_tick_time: Optional[datetime] = None

        # WebSocket
        self.ticker: Optional[KiteTicker] = None
        self.option_token: Optional[int] = None  # set when a position is open

    def register_candle_listener(self, fn: Callable[[Candle, Candle, float], None]):
        """Listener gets called with (real_candle, ha_candle, vwap) on each candle close."""
        self._listeners.append(fn)

    def backfill_historical(self, lookback_days: int = 2):
        """Fetch recent historical candles from Kite to seed VWAP and HA series."""
        tf = config.get("strategy", "timeframe_minutes", default=5)
        # Kite uses "minute" for 1-min, not "1minute"
        interval = "minute" if tf == 1 else f"{tf}minute"
        to_dt = datetime.now(IST)
        from_dt = to_dt - timedelta(days=lookback_days)

        try:
            data = self.kite.historical_data(
                instrument_token=self.futures_token,
                from_date=from_dt,
                to_date=to_dt,
                interval=interval,
                continuous=False,
            )
        except Exception as e:
            log.error(f"Historical data fetch failed: {e}")
            state.add_error(f"historical_fetch_failed: {e}")
            return

        if not data:
            log.warning("Historical data returned empty.")
            return

        # Filter to today only for VWAP (since we reset daily)
        today_ist = datetime.now(IST).date()
        for row in data:
            ts = row["date"]
            if ts.tzinfo is None:
                ts = IST.localize(ts)
            else:
                ts = ts.astimezone(IST)
            # For spot index, volume is 0 — replace with 1 for VWAP math
            vol = row["volume"]
            if self.use_spot or vol == 0:
                vol = 1
            self.real_candles.append(Candle(ts, row["open"], row["high"], row["low"], row["close"], vol))

        self._recompute_derived()
        log.info(f"Backfilled {len(self.real_candles)} historical candles. "
                 f"Latest: {self.real_candles[-1] if self.real_candles else 'none'}")

    def _on_new_candle(self, real_candle: Candle):
        """Called by CandleAggregator on each candle close."""
        with self._lock:
            self.real_candles.append(real_candle)
            self._recompute_derived()
            ha = self.ha_candles[-1] if self.ha_candles else real_candle
            vwap = self.vwap_series[-1] if self.vwap_series else real_candle.close

        log.info(f"Candle close: {real_candle} | HA close: {ha.close:.2f} | VWAP: {vwap:.2f}")

        for listener in self._listeners:
            try:
                listener(real_candle, ha, vwap)
            except Exception as e:
                log.exception(f"Listener raised: {e}")

    def _recompute_derived(self):
        """Recompute HA + VWAP for full buffer. Cheap for 200-candle buffer."""
        real_list = list(self.real_candles)
        self.ha_candles = deque(compute_heikin_ashi(real_list), maxlen=self.max_candles)
        reset = config.get("strategy", "vwap_reset", default="daily")
        self.vwap_series = deque(compute_vwap(real_list, reset=reset), maxlen=self.max_candles)

    def get_latest(self) -> Optional[Dict]:
        """Returns the latest candles + VWAP for the strategy."""
        with self._lock:
            if len(self.real_candles) < 2 or len(self.ha_candles) < 2:
                return None
            return {
                "real_current": self.real_candles[-1],
                "real_prev": self.real_candles[-2],
                "ha_current": self.ha_candles[-1],
                "ha_prev": self.ha_candles[-2],
                "ha_prev_prev": self.ha_candles[-3] if len(self.ha_candles) >= 3 else None,
                "vwap_current": self.vwap_series[-1],
                "vwap_prev": self.vwap_series[-2],
            }

    # ---------------- WebSocket ----------------

    def start_websocket(self):
        api_key = config.get("broker", "api_key")
        access_token = self.kite.access_token if hasattr(self.kite, "access_token") else None
        # KiteConnect doesn't expose access_token; read from file
        from pathlib import Path
        token_file = Path(__file__).parent.parent / "data" / "access_token.txt"
        access_token = token_file.read_text().strip()

        self.ticker = KiteTicker(api_key, access_token)
        self.ticker.on_ticks = self._on_ticks
        self.ticker.on_connect = self._on_connect
        self.ticker.on_close = self._on_close
        self.ticker.on_error = self._on_error
        self.ticker.on_reconnect = self._on_reconnect

        log.info("Starting WebSocket connection...")
        self.ticker.connect(threaded=True)

    def _on_connect(self, ws, response):
        log.info(f"WebSocket connected. Subscribing to futures token {self.futures_token}.")
        ws.subscribe([self.futures_token])
        ws.set_mode(ws.MODE_FULL, [self.futures_token])
        state.update_state(ws_connected=True)

    def _on_ticks(self, ws, ticks):
        for t in ticks:
            token = t.get("instrument_token")
            ltp = t.get("last_price")
            volume = t.get("last_traded_quantity", 0)
            ts = t.get("exchange_timestamp") or datetime.now(IST)
            if ts.tzinfo is None:
                ts = IST.localize(ts)

            if token == self.futures_token and ltp is not None:
                self.last_ltp = ltp
                self.last_tick_time = ts
                self.aggregator.on_tick(ltp, volume or 0, ts)

            if token == self.option_token:
                # Option LTP is tracked separately for SL/target monitoring
                self._option_ltp = ltp
                self._option_tick_time = ts

    def _on_close(self, ws, code, reason):
        log.warning(f"WebSocket closed. code={code}, reason={reason}")
        state.update_state(ws_connected=False)

    def _on_error(self, ws, code, reason):
        log.error(f"WebSocket error. code={code}, reason={reason}")
        state.add_error(f"ws_error: {code} {reason}")

    def _on_reconnect(self, ws, attempts_count):
        log.info(f"WebSocket reconnecting (attempt {attempts_count})...")

    def subscribe_option(self, option_token: int):
        """Add an option contract to the live feed (when entering a position)."""
        self.option_token = option_token
        if self.ticker and self.ticker.is_connected():
            self.ticker.subscribe([option_token])
            self.ticker.set_mode(self.ticker.MODE_FULL, [option_token])
            log.info(f"Subscribed to option token {option_token}")

    def unsubscribe_option(self):
        if self.ticker and self.option_token and self.ticker.is_connected():
            self.ticker.unsubscribe([self.option_token])
            log.info(f"Unsubscribed option token {self.option_token}")
        self.option_token = None

    def stop_websocket(self):
        if self.ticker:
            try:
                self.ticker.close()
            except Exception:
                pass
            log.info("WebSocket stopped.")
