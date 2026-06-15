from datetime import datetime, timedelta
import pytz

from strategy.market_data import Candle

IST = pytz.timezone("Asia/Kolkata")


def fetch_nifty_5min_kite(kite, days_back=30):

    token = 256265

    data = kite.historical_data(
        instrument_token=token,
        from_date=datetime.now() - timedelta(days=days_back),
        to_date=datetime.now(),
        interval="5minute"
    )

    candles = []

    for row in data:
        candles.append(
            Candle(
                row["date"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"]
            )
        )

    return candles


def filter_market_hours(candles):
    return candles