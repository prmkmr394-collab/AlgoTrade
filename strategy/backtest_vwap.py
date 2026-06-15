from dataclasses import dataclass
from typing import List

from strategy.market_data import Candle, compute_heikin_ashi, compute_vwap


@dataclass
class Trade:
    side: str
    entry_price: float
    exit_price: float
    pnl: float


class Backtester:

    def __init__(self, candles: List[Candle]):
        self.real = candles
        self.ha = compute_heikin_ashi(candles)
        self.vwap = compute_vwap(candles)

    def run(self):

        trades = []
        position = None

        for i in range(2, len(self.real)):

            close = self.real[i].close
            prev_close = self.real[i - 1].close

            current_vwap = self.vwap[i]
            prev_vwap = self.vwap[i - 1]

            # BUY
            if position is None:

                if prev_close < prev_vwap and close > current_vwap:
                    position = {
                        "side": "BUY",
                        "entry": close
                    }

                elif prev_close > prev_vwap and close < current_vwap:
                    position = {
                        "side": "SELL",
                        "entry": close
                    }

            else:

                if position["side"] == "BUY":

                    pnl = close - position["entry"]

                    if pnl >= 15 or pnl <= -8:
                        trades.append(
                            Trade(
                                "BUY",
                                position["entry"],
                                close,
                                pnl
                            )
                        )
                        position = None

                else:

                    pnl = position["entry"] - close

                    if pnl >= 15 or pnl <= -8:
                        trades.append(
                            Trade(
                                "SELL",
                                position["entry"],
                                close,
                                pnl
                            )
                        )
                        position = None

        return trades