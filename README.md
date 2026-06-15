# NiftyAlgoBot

Automated Nifty 50 options trading bot for Zerodha Kite — VWAP + Heikin Ashi crossover strategy.

## Status: COMPLETE — Paper trading ready

All batches delivered. Currently runs in paper mode (default). Switch to live after validation.

## Daily Workflow

### Every market day (Mon–Fri) before 9:15 AM IST:

**Terminal 1 — Generate today's access token (~30 sec):**
```cmd
cd "C:\Users\Suresh.Kumar\OneDrive - Magnit\Documents\Python Program\NiftyAlgoBot"
.venv\Scripts\activate
python -m utils.auth
```
(Login in browser, paste request_token.)

**Terminal 2 — Start the dashboard (keep running all day):**
```cmd
.venv\Scripts\activate
python -m dashboard.app
```
Then open http://127.0.0.1:5555 in your browser.

**Terminal 3 — Start the bot (keep running all day):**
```cmd
.venv\Scripts\activate
python -m main
```

The bot will:
- Connect to Kite, subscribe to Nifty futures live ticks
- Wait for market open (9:15 AM)
- Form 5-min candles, compute HA + VWAP in real-time
- Evaluate signals at every 5-min candle close
- (In paper mode) Simulate orders. (In live mode) Place real orders.
- Force-close all positions at 3:15 PM
- Idle until next session

### To stop the bot:
- Press `Ctrl+C` in Terminal 3 → graceful shutdown (closes open positions first)
- OR create a file named `STOP.txt` in the bot folder → bot self-halts within 5 sec

## Switch from Paper to Live (after 5+ days of validation)

Edit `config/config.yaml`:
```yaml
mode:
  trading_mode: "live"   # was "paper"
```
Restart the bot. Now real orders will be placed.

## Safety controls

| Control | How |
|---|---|
| Kill switch | Create `STOP.txt` in bot folder. Bot exits all positions and halts. |
| Daily loss cap | Auto: bot halts when realized P&L below -₹1,500 |
| Max trades/day | Auto: bot halts after 4 trades |
| Hard squareoff | Auto: all positions closed at 3:15 PM IST |
| Reconciler | Auto: bot halts if its state diverges from Kite's positions |

## Strategy summary

| Parameter | Value |
|---|---|
| Underlying | Nifty 50 current-month futures |
| Timeframe | 5-min Heikin Ashi |
| VWAP | Real OHLC + volume, daily reset |
| Entry BUY/CE | HA prev crossed above VWAP + close > VWAP + new HA high + close > prev high |
| Entry SELL/PE | HA prev crossed below VWAP + close < VWAP + new HA low + close < prev low |
| Min distance | HA close at least 0.05% of spot from VWAP |
| Strike | ITM by 100 pts, rounded to nearest 50 |
| Expiry | Next-week weekly |
| Direction | Long options only |
| Lot size | 1 lot = 75 qty |
| SL / Target | -12 / +12 pts on premium |
| Trading window | 9:30-14:45 entries; 15:15 hard exit |
| Max trades/day | 4 |
| Daily loss cap | ₹1,500 |
| Re-entry block | 30 min same-direction after SL |
