from strategy.backtest_vwap import Backtester
from strategy.historical_data import fetch_nifty_5min_kite
from utils.auth import get_kite_client


def main():

    kite = get_kite_client()

    candles = fetch_nifty_5min_kite(
        kite,
        days_back=30
    )

    bt = Backtester(candles)

    trades = bt.run()

    total = sum(t.pnl for t in trades)

    wins = len([t for t in trades if t.pnl > 0])

    print("\n===== BACKTEST RESULT =====")

    print(f"Trades     : {len(trades)}")
    print(f"Wins       : {wins}")
    print(f"Win Rate   : {(wins/max(len(trades),1))*100:.2f}%")
    print(f"Total PnL  : {total:.2f}")

    print("\nLast 10 Trades:\n")

    for t in trades[-10:]:
        print(t)


if __name__ == "__main__":
    main()