from dataclasses import dataclass


@dataclass
class Signal:
    side: str
    reason: str


def evaluate_signal(snapshot):

    candles = snapshot["candles"]

    c0 = candles[3]
    c1 = candles[2]
    c2 = candles[1]
    c3 = candles[0]

    ema = snapshot["ema_current"]

    # BUY

    buy = (
        c0.close > c1.close and
        c0.high > c1.high and
        c0.low > c1.low and

        c1.close > c2.close and
        c1.high > c2.high and
        c1.low > c2.low and

        c2.close > c3.close and
        c2.high > c3.high and
        c2.low > c3.low and

        c0.close > ema
    )

    # SELL

    sell = (
        c0.close < c1.close and
        c0.high < c1.high and
        c0.low < c1.low and

        c1.close < c2.close and
        c1.high < c2.high and
        c1.low < c2.low and

        c2.close < c3.close and
        c2.high < c3.high and
        c2.low < c3.low and

        c0.close < ema
    )

    if buy:
        return Signal("BUY", "3 Candle EMA Buy")

    if sell:
        return Signal("SELL", "3 Candle EMA Sell")

    return Signal("NONE", "No signal")