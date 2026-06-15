"""
Backtest runner — entry point for backtest.

Usage:
  python -m run_backtest                 # uses yfinance, ~60 days
  python -m run_backtest --kite          # uses Kite API (more data, real volume)
  python -m run_backtest --days 30       # custom lookback

Output:
  - Console report
  - data/backtest_trades.csv (all trades for review)
"""
import argparse
import sys
from pathlib import Path

from strategy.backtest import Backtester, print_report, save_trades_csv
from strategy.historical_data import filter_market_hours
from utils.config_loader import config
from utils.logger import log

# Import the data fetch functions dynamically — supports both
# "nifty" and "banknifty" named functions in historical_data.py
try:
    from strategy.historical_data import fetch_nifty_5min_yfinance as fetch_yfinance
except ImportError:
    try:
        from strategy.historical_data import fetch_banknifty_5min_yfinance as fetch_yfinance
    except ImportError:
        fetch_yfinance = None

try:
    from strategy.historical_data import fetch_nifty_5min_kite as fetch_kite
except ImportError:
    try:
        from strategy.historical_data import fetch_banknifty_5min_kite as fetch_kite
    except ImportError:
        fetch_kite = None


def main():
    parser = argparse.ArgumentParser(description="NiftyAlgoBot backtest runner")
    parser.add_argument("--kite", action="store_true",
                        help="Use Kite API for historical data (requires auth)")
    parser.add_argument("--days", type=int, default=60,
                        help="Number of days of historical data to fetch")
    parser.add_argument("--no-cache", action="store_true",
                        help="Skip cache, refetch data")
    parser.add_argument("--csv", type=str, default="data/backtest_trades.csv",
                        help="Output CSV path for trade-by-trade results")
    args = parser.parse_args()

    underlying = config.get("strategy", "underlying_symbol", default="NIFTY")
    log.info(f"Starting backtest: source={'kite' if args.kite else 'yfinance'}, "
             f"days={args.days}, underlying={underlying}")

    if args.kite:
        if fetch_kite is None:
            print("\nERROR: No Kite fetch function found in historical_data.py")
            sys.exit(1)
        from utils.auth import get_kite_client
        try:
            kite = get_kite_client()
        except Exception as e:
            print(f"\nERROR: Kite authentication failed: {e}")
            print("Run `python -m utils.auth` first to generate today's access token.")
            sys.exit(1)
        candles = fetch_kite(kite, days_back=args.days, use_cache=not args.no_cache)
    else:
        if fetch_yfinance is None:
            print("\nERROR: No yfinance fetch function found in historical_data.py")
            sys.exit(1)
        candles = fetch_yfinance(days_back=args.days, use_cache=not args.no_cache)

    if not candles:
        print("ERROR: No data fetched.")
        sys.exit(1)

    candles = filter_market_hours(candles)
    log.info(f"After market-hours filter: {len(candles)} candles")

    starting_capital = config.get("risk", "capital", default=40000)

    bt = Backtester(candles)
    result = bt.run(starting_capital=starting_capital)

    print_report(result)

    # Save trades CSV
    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    save_trades_csv(result, str(csv_path))


if __name__ == "__main__":
    main()
