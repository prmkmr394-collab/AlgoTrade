"""
Local HTML dashboard. Run this in a separate terminal alongside the bot.
Open http://127.0.0.1:5555 in your browser to see live status.

Updated: Uses Server-Sent Events (SSE) for instant push updates.
The dashboard now updates within ~1 second of any state change,
instead of waiting for the 5-second poll cycle.
"""
import json
import time
from datetime import datetime, timedelta

from flask import Flask, Response, render_template_string
from utils.config_loader import config
from utils.state import get_state


app = Flask(__name__)


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NiftyAlgoBot — Live Status</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background: #0e1117; color: #e6edf3; padding: 20px; }
    h1 { font-size: 22px; margin-bottom: 4px; }
    .sub { color: #8b949e; font-size: 13px; margin-bottom: 20px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-bottom: 20px; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
    .card-title { font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
    .card-value { font-size: 24px; font-weight: 600; }
    .green { color: #3fb950; }
    .red { color: #f85149; }
    .yellow { color: #d29922; }
    .blue { color: #58a6ff; }
    .status-pill { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
    .status-running { background: #1f6e3a; color: #fff; }
    .status-idle { background: #424a53; color: #fff; }
    .status-halted { background: #b62324; color: #fff; }
    .status-error { background: #d29922; color: #000; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid #30363d; }
    th { color: #8b949e; font-weight: 500; font-size: 11px; text-transform: uppercase; }
    .section-title { font-size: 14px; color: #8b949e; margin: 24px 0 8px; text-transform: uppercase; letter-spacing: 0.5px; }
    .empty { color: #6e7681; font-style: italic; padding: 12px; }
    .heartbeat-old { color: #f85149; }
    .heartbeat-fresh { color: #3fb950; }
    .live-badge { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #3fb950;
                  animation: pulse 1.5s infinite; margin-right: 6px; }
    @keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:0.3;} }
    .conn-ok { color: #3fb950; }
    .conn-bad { color: #f85149; }
    #last-update { color: #8b949e; font-size: 11px; }
    .pnl-flash { animation: flash 0.4s ease; }
    @keyframes flash { 0%{background:#1f6e3a;} 100%{background:transparent;} }
  </style>
</head>
<body>
  <h1><span class="live-badge"></span>NiftyAlgoBot — Live Status</h1>
  <div class="sub">
    Mode: <b id="mode">--</b> | Manual approval: <b id="manual">--</b> |
    Last heartbeat: <span id="heartbeat">--</span> |
    <span id="last-update">connecting...</span>
  </div>

  <div class="grid">
    <div class="card">
      <div class="card-title">Status</div>
      <div class="card-value">
        <span id="status-pill" class="status-pill">--</span>
      </div>
      <div id="halt-reason" style="margin-top:6px;font-size:12px;color:#f85149;"></div>
    </div>

    <div class="card">
      <div class="card-title">Today's P&L (Realized)</div>
      <div class="card-value" id="pnl">--</div>
    </div>

    <div class="card">
      <div class="card-title">Trades Today</div>
      <div class="card-value" id="trades">--</div>
      <div style="font-size:12px;color:#8b949e;margin-top:6px;">
        <span class="green" id="wins">0</span> W ·
        <span class="red" id="losses">0</span> L
      </div>
    </div>

    <div class="card">
      <div class="card-title">Daily Loss Cap</div>
      <div class="card-value" id="cap">--</div>
    </div>

    <div class="card">
      <div class="card-title">Connections</div>
      <div style="font-size:14px;line-height:1.8;">
        Kite API: <span id="kite-conn">--</span><br>
        WebSocket: <span id="ws-conn">--</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Last Signal</div>
      <div class="card-value" id="signal-type">--</div>
      <div style="font-size:12px;color:#8b949e;margin-top:4px;" id="signal-detail"></div>
    </div>
  </div>

  <div class="section-title">Open Position</div>
  <div class="card" id="open-pos-card">
    <div class="empty">No open position.</div>
  </div>

  <div class="section-title">Today's Trade History</div>
  <div class="card">
    <div id="trade-history"><div class="empty">No completed trades yet today.</div></div>
  </div>

  <div class="section-title">Recent Errors</div>
  <div class="card">
    <div id="errors"><div class="empty">No errors.</div></div>
  </div>

  <div style="margin-top:24px;font-size:11px;color:#6e7681;" id="footer">
    Live updates via SSE · <span id="now">--</span>
  </div>

<script>
const fmt = (n) => '₹ ' + parseFloat(n).toFixed(2);
const fmtPnl = (n) => {
  const v = parseFloat(n);
  return (v >= 0 ? '+' : '') + '₹ ' + v.toFixed(2);
};

function updateDashboard(s) {
  // Header
  document.getElementById('mode').textContent = (s.mode || '--').toUpperCase();
  document.getElementById('manual').textContent = s.manual_approval ? 'ON' : 'OFF (FULL AUTO)';
  document.getElementById('now').textContent = new Date().toLocaleTimeString('en-IN');
  document.getElementById('last-update').textContent = 'Updated: ' + new Date().toLocaleTimeString('en-IN');

  // Heartbeat
  const hb = s.last_heartbeat;
  if (hb) {
    const last = new Date(hb);
    const ago = Math.round((Date.now() - last) / 1000);
    const hbEl = document.getElementById('heartbeat');
    hbEl.textContent = last.toLocaleTimeString('en-IN') + ' (' + ago + 's ago)';
    hbEl.className = ago < 15 ? 'heartbeat-fresh' : 'heartbeat-old';
  }

  // Status pill
  const status = (s.status || 'idle').toLowerCase();
  const pill = document.getElementById('status-pill');
  pill.textContent = status.toUpperCase();
  pill.className = 'status-pill status-' + status;
  document.getElementById('halt-reason').textContent = s.halt_reason || '';

  // P&L
  const pnl = s.today ? s.today.realized_pnl : 0;
  const pnlEl = document.getElementById('pnl');
  const prevPnl = parseFloat(pnlEl.dataset.prev || 0);
  pnlEl.textContent = fmtPnl(pnl);
  pnlEl.className = 'card-value ' + (pnl >= 0 ? 'green' : 'red');
  if (pnl !== prevPnl) { pnlEl.classList.add('pnl-flash'); setTimeout(() => pnlEl.classList.remove('pnl-flash'), 400); }
  pnlEl.dataset.prev = pnl;

  // Trades
  const tc = s.today ? s.today.trades_count : 0;
  const maxT = s.max_trades || 100;
  document.getElementById('trades').textContent = tc + ' / ' + maxT;
  document.getElementById('wins').textContent = s.today ? s.today.wins : 0;
  document.getElementById('losses').textContent = s.today ? s.today.losses : 0;

  // Cap
  const cap = s.loss_cap || 1500;
  const capEl = document.getElementById('cap');
  capEl.textContent = fmtPnl(pnl) + ' / -₹ ' + cap;
  capEl.className = 'card-value ' + (pnl <= -(cap * 0.7) ? 'red' : 'yellow');

  // Connections
  const kite = s.kite_connected;
  const ws = s.ws_connected;
  document.getElementById('kite-conn').innerHTML = '<span class="' + (kite ? 'conn-ok' : 'conn-bad') + '">' + (kite ? '● Connected' : '● Disconnected') + '</span>';
  document.getElementById('ws-conn').innerHTML = '<span class="' + (ws ? 'conn-ok' : 'conn-bad') + '">' + (ws ? '● Connected' : '● Disconnected') + '</span>';

  // Last signal
  const sig = s.last_signal;
  const sigEl = document.getElementById('signal-type');
  if (sig && sig.type !== 'NONE') {
    sigEl.textContent = sig.type;
    sigEl.className = 'card-value ' + (sig.type === 'BUY' ? 'green' : 'red');
    document.getElementById('signal-detail').textContent = '@ ' + sig.time + ' | Spot: ' + sig.spot;
  } else {
    sigEl.textContent = 'NONE';
    sigEl.className = 'card-value';
    document.getElementById('signal-detail').textContent = sig ? sig.reason || '' : '';
  }

  // Open position
  const pos = s.open_position;
  const posCard = document.getElementById('open-pos-card');
  if (pos) {
    const posClass = pos.pnl >= 0 ? 'green' : 'red';
    posCard.innerHTML = `<table>
      <tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>SL</th><th>Target</th><th>LTP</th><th>P&L</th></tr>
      <tr>
        <td>${pos.symbol}</td>
        <td>${pos.qty}</td>
        <td>₹ ${pos.entry}</td>
        <td>₹ ${pos.sl}</td>
        <td>₹ ${pos.target}</td>
        <td>₹ ${pos.ltp}</td>
        <td class="${posClass}">₹ ${parseFloat(pos.pnl).toFixed(2)}</td>
      </tr></table>`;
  } else {
    posCard.innerHTML = '<div class="empty">No open position.</div>';
  }

  // Trade history
  const trades = s.trade_history || [];
  const thEl = document.getElementById('trade-history');
  if (trades.length > 0) {
    let rows = trades.map(t => `<tr>
      <td>${t.entry_time}</td>
      <td class="${t.side === 'BUY' ? 'green' : 'red'}">${t.side}</td>
      <td>${t.symbol}</td>
      <td>₹ ${t.entry}</td>
      <td>₹ ${t.exit}</td>
      <td class="${parseFloat(t.pnl) >= 0 ? 'green' : 'red'}">₹ ${parseFloat(t.pnl).toFixed(2)}</td>
      <td>${t.exit_reason}</td>
    </tr>`).join('');
    thEl.innerHTML = `<table><tr><th>Time</th><th>Side</th><th>Symbol</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th></tr>${rows}</table>`;
  } else {
    thEl.innerHTML = '<div class="empty">No completed trades yet today.</div>';
  }

  // Errors
  const errors = s.errors || [];
  const errEl = document.getElementById('errors');
  if (errors.length > 0) {
    const recent = errors.slice(-10).reverse();
    let rows = recent.map(e => `<tr><td style="white-space:nowrap;">${e.time}</td><td style="color:#f85149;">${e.msg}</td></tr>`).join('');
    errEl.innerHTML = `<table><tr><th>Time</th><th>Message</th></tr>${rows}</table>`;
  } else {
    errEl.innerHTML = '<div class="empty">No errors. Smooth sailing.</div>';
  }
}

// SSE connection for live push updates
function connectSSE() {
  const evtSource = new EventSource('/stream');

  evtSource.onmessage = function(e) {
    try {
      const data = JSON.parse(e.data);
      updateDashboard(data);
    } catch(err) {
      console.error('Parse error:', err);
    }
  };

  evtSource.onerror = function() {
    document.getElementById('last-update').textContent = 'Reconnecting...';
    evtSource.close();
    // Reconnect after 2 seconds
    setTimeout(connectSSE, 2000);
  };
}

connectSSE();
</script>
</body>
</html>
"""


def _build_state_payload():
    """Build the full state dict to send to browser."""
    s = get_state()
    return {
        **s,
        "mode": config.get("mode", "trading_mode", default="paper"),
        "manual_approval": config.get("mode", "manual_approval", default=False),
        "max_trades": config.get("risk", "max_trades_per_day", default=100),
        "loss_cap": config.get("risk", "daily_loss_cap", default=1500),
    }


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/stream")
def stream():
    """SSE endpoint — pushes state to browser whenever it changes."""
    def event_generator():
        last_sent = None
        while True:
            try:
                current = _build_state_payload()
                payload = json.dumps(current, default=str)
                if payload != last_sent:
                    last_sent = payload
                    yield f"data: {payload}\n\n"
                # Also send a heartbeat every 5s to keep connection alive
                else:
                    yield f": heartbeat\n\n"
            except Exception as e:
                yield f"data: {{}}\n\n"
            time.sleep(1)   # Check for state changes every 1 second

    return Response(
        event_generator(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.route("/api/state")
def api_state():
    from flask import jsonify
    return jsonify(_build_state_payload())


def run_dashboard():
    host = config.get("dashboard", "host", default="127.0.0.1")
    port = config.get("dashboard", "port", default=5555)
    print(f"\n  Dashboard running at: http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    run_dashboard()
