"""
Historical data fetcher for backtest.

Two sources supported:
  1. yfinance (free, no API key) — pulls Bank Nifty spot data
  2. Kite historical API (if you have api_key + access_token)

Both fetchers cache results under data/historical_cache/, with the cache
filename including the configured timeframe so different timeframes
don't collide.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import List
import pickle

import pandas as pd
import pytz

from strategy.market_data import Candle
from utils.logger import log
from utils.config_loader import config

IST = pytz.timezone("Asia/Kolkata")

CACHE_DIR = Path(__file__).parent.parent / "data" / "historical_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_banknifty_5min_yfinance(days_back: int = 60, use_cache: bool = True) -> List[Candle]:
    """
    Fetch Bank Nifty candles from yfinance at the configured timeframe.
    Index data has volume=0 — we substitute 1 so VWAP math works.
    """

    tf = config.get("strategy", "timeframe_minutes", default=5)

    symbol_tag = config.get(
        "strategy",
        "underlying_symbol",
        default="BANKNIFTY"
    ).lower()

    cache_file = CACHE_DIR / f"{symbol_tag}_yf_{tf}min_{days_back}d.pkl"

    if use_cache and cache_file.exists():
        age_hours = (
            datetime.now().timestamp() - cache_file.stat().st_mtime
        ) / 3600

        if age_hours < 12:
            log.info(
                f"Loading cached data from {cache_file} "
                f"(age: {age_hours:.1f}h)"
            )

            with open(cache_file, "rb") as f:
                return pickle.load(f)

    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance not installed. Run: pip install yfinance")
        raise

    log.info(
        f"Fetching {days_back} days of Bank Nifty "
        f"{tf}-min data from yfinance..."
    )

    end = datetime.now()
    start = end - timedelta(days=days_back)

    yf_interval_map = {
        1: "1m",
        5: "5m",
        15: "15m",
        30: "30m",
        60: "1h",
    }

    yf_interval = yf_interval_map.get(tf, f"{tf}m")

    ticker = yf.Ticker("^NSEBANK")
    df = ticker.history(
        start=start,
        end=end,
        interval=yf_interval
    )

    if df.empty:
        raise ValueError(
            "yfinance returned empty data. Check internet connection."
        )

    candles: List[Candle] = []

    for ts, row in df.iterrows():

        ts_ist = (
            ts.tz_convert(IST)
            if ts.tz
            else IST.localize(ts.to_pydatetime())
        )

        vol = (
            float(row["Volume"])
            if row["Volume"] > 0
            else 1.0
        )

        candles.append(
            Candle(
                timestamp=(
                    ts_ist.to_pydatetime()
                    if hasattr(ts_ist, "to_pydatetime")
                    else ts_ist
                ),
                o=float(row["Open"]),
                h=float(row["High"]),
                l=float(row["Low"]),
                c=float(row["Close"]),
                v=vol,
            )
        )

    log.info(
        f"Fetched {len(candles)} candles from "
        f"{candles[0].timestamp.date()} "
        f"to {candles[-1].timestamp.date()}"
    )

    with open(cache_file, "wb") as f:
        pickle.dump(candles, f)

    log.info(f"Cached to {cache_file}")

    return candles


def fetch_banknifty_5min_kite(
    kite,
    days_back: int = 90,
    use_cache: bool = True,
) -> List[Candle]:
    """
    Fetch Bank Nifty index candles via Kite API at the configured timeframe.
    Cache filename includes timeframe so different timeframes don't collide.
    """

    tf = config.get("strategy", "timeframe_minutes", default=5)

    cache_file = CACHE_DIR / f"banknifty_kite_{tf}min_{days_back}d.pkl"

    if use_cache and cache_file.exists():
        age_hours = (
            datetime.now().timestamp() - cache_file.stat().st_mtime
        ) / 3600

        if age_hours < 12:
            log.info(
                f"Loading cached Kite data from {cache_file}"
            )

            with open(cache_file, "rb") as f:
                return pickle.load(f)

    from strategy.strike_selector import find_banknifty_index_token

    token = find_banknifty_index_token(kite)

    if not token:
        raise ValueError(
            "Could not find Bank Nifty index token."
        )

    if tf == 1:
        interval = "minute"
    elif tf == 60:
        interval = "60minute"
    else:
        interval = f"{tf}minute"

    log.info(f"Using Kite interval: {interval}")

    end = datetime.now(IST)

    candles: List[Candle] = []

    chunk_days = 50 if tf <= 5 else 100

    cursor_end = end
    fetched_chunks = 0

    while (
        cursor_end > end - timedelta(days=days_back)
        and fetched_chunks < 10
    ):

        earliest_allowed = end - timedelta(days=days_back)
        cursor_start = max(cursor_end - timedelta(days=chunk_days), earliest_allowed)

        log.info(
            f"Fetching {cursor_start.date()} "
            f"to {cursor_end.date()}..."
        )

        try:
            data = kite.historical_data(
                instrument_token=token,
                from_date=cursor_start,
                to_date=cursor_end,
                interval=interval,
            )

        except Exception as e:
            log.error(
                f"Kite historical fetch failed: {e}"
            )
            break

        for row in data:

            ts = row["date"]

            ts_ist = (
                ts.astimezone(IST)
                if ts.tzinfo
                else IST.localize(ts)
            )

            vol = float(row["volume"])

            if vol == 0:
                vol = 1.0

            candles.append(
                Candle(
                    timestamp=ts_ist,
                    o=float(row["open"]),
                    h=float(row["high"]),
                    l=float(row["low"]),
                    c=float(row["close"]),
                    v=vol,
                )
            )

        cursor_end = cursor_start
        fetched_chunks += 1

    candles.sort(key=lambda c: c.timestamp)

    log.info(
        f"Fetched {len(candles)} candles via Kite from "
        f"{candles[0].timestamp.date() if candles else 'N/A'} "
        f"to "
        f"{candles[-1].timestamp.date() if candles else 'N/A'}"
    )

    with open(cache_file, "wb") as f:
        pickle.dump(candles, f)

    return candles


def filter_market_hours(
    candles: List[Candle]
) -> List[Candle]:
    """
    Keep only candles within 9:15-15:30 IST.
    Drop weekends.
    """

    out = []

    for c in candles:

        ts = (
            c.timestamp.astimezone(IST)
            if c.timestamp.tzinfo
            else IST.localize(c.timestamp)
        )

        if ts.weekday() >= 5:
            continue

        t = ts.time()

        if t < datetime.strptime(
            "09:15",
            "%H:%M"
        ).time():
            continue

        if t >= datetime.strptime(
            "15:30",
            "%H:%M"
        ).time():
            continue

        out.append(c)

    return out
def fetch_nifty_5min_yfinance(days_back=60, use_cache=True):
    return fetch_banknifty_5min_yfinance(
        days_back=days_back,
        use_cache=use_cache
    )


def fetch_nifty_5min_kite(kite, days_back=90, use_cache=True):
    return fetch_banknifty_5min_kite(
        kite=kite,
        days_back=days_back,
        use_cache=use_cache
    )
