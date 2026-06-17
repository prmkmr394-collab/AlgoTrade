"""
NiftyAlgoBot — Professional Dashboard
Live SSE push updates, equity curve, win rate, risk score,
hide/show P&L, market status, trader card, trade distribution chart.
"""
import json
import time
from datetime import datetime

from flask import Flask, Response, render_template_string
from utils.config_loader import config
from utils.state import get_state

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>NiftyAlgoBot</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@2.44.0/tabler-icons.min.css">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"Segoe UI",Roboto,sans-serif;background:#0b0f14;color:#e6edf3;padding:0;font-size:13px}
.dash{max-width:1200px;margin:0 auto;padding:14px}
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;padding:12px 16px;background:#161b22;border:1px solid #21262d;border-radius:12px}
.header-left{display:flex;align-items:center;gap:12px}
.avatar{width:40px;height:40px;border-radius:50%;background:#1f4068;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;color:#58a6ff}
.trader-name{font-size:15px;font-weight:600;color:#e6edf3}
.trader-sub{font-size:11px;color:#6e7681;margin-top:2px}
.header-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;padding:4px 10px;border-radius:6px;font-weight:500}
.badge-live{background:rgba(248,81,73,0.15);color:#f85149;border:1px solid rgba(248,81,73,0.3)}
.badge-strategy{background:#1c2128;color:#8b949e;border:1px solid #21262d}
.badge-paper{background:rgba(88,166,255,0.15);color:#58a6ff;border:1px solid rgba(88,166,255,0.3)}
.pulse{width:6px;height:6px;border-radius:50%;background:#f85149;animation:blink 1.2s infinite;display:inline-block}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0.2}}
.btn{font-size:11px;padding:5px 11px;border:1px solid #30363d;border-radius:6px;background:transparent;color:#8b949e;cursor:pointer;display:inline-flex;align-items:center;gap:5px;transition:all 0.15s}
.btn:hover{background:#21262d;color:#e6edf3}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:12px}
.kpi{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:12px 14px}
.kpi-label{font-size:10px;color:#6e7681;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:7px;display:flex;align-items:center;gap:5px}
.kpi-value{font-size:20px;font-weight:600;line-height:1}
.kpi-sub{font-size:11px;color:#6e7681;margin-top:4px}
.green{color:#3fb950}.red{color:#f85149}.yellow{color:#e3b341}.blue{color:#58a6ff}.muted{color:#6e7681}
.row2{display:grid;grid-template-columns:1.2fr 0.8fr;gap:10px;margin-bottom:12px}
.card{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:14px}
.card-title{font-size:10px;color:#6e7681;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px;display:flex;align-items:center;gap:5px}
.progress-wrap{margin-bottom:9px}
.progress-label{display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;color:#8b949e}
.progress-label span:last-child{color:#e6edf3}
.progress-bar{height:5px;background:#21262d;border-radius:3px;overflow:hidden}
.progress-fill{height:100%;border-radius:3px;transition:width 0.6s ease}
.full-card{margin-bottom:12px}
.pos-table{width:100%;border-collapse:collapse;font-size:12px}
.pos-table th{color:#6e7681;font-weight:500;font-size:10px;text-transform:uppercase;padding:5px 8px;border-bottom:1px solid #21262d;text-align:left}
.pos-table td{padding:8px 8px;border-bottom:1px solid #21262d}
.pill{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:600;letter-spacing:0.3px}
.pill-buy{background:rgba(63,185,80,0.15);color:#3fb950}
.pill-sell{background:rgba(248,81,73,0.15);color:#f85149}
.pill-target{background:rgba(63,185,80,0.15);color:#3fb950}
.pill-sl{background:rgba(248,81,73,0.15);color:#f85149}
.pill-opp{background:rgba(227,179,65,0.15);color:#e3b341}
.pill-cap{background:rgba(248,81,73,0.15);color:#f85149}
.pill-shut{background:#21262d;color:#8b949e}
.section-title{font-size:10px;color:#6e7681;text-transform:uppercase;letter-spacing:0.5px;margin:12px 0 8px;display:flex;align-items:center;gap:5px}
.trade-table{width:100%;border-collapse:collapse;font-size:12px}
.trade-table th{color:#6e7681;font-weight:500;font-size:10px;text-transform:uppercase;padding:5px 10px;border-bottom:1px solid #21262d;text-align:left}
.trade-table td{padding:7px 10px;border-bottom:1px solid #21262d;color:#e6edf3}
.trade-row:hover{background:#1c2128}
.conn-dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:4px;vertical-align:middle}
.conn-ok{background:#3fb950}.conn-bad{background:#f85149}
.row3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px}
.risk-meter{display:flex;flex-direction:column;gap:7px;margin-top:4px}
.risk-bar-row{display:flex;align-items:center;gap:8px;font-size:11px}
.risk-label{width:72px;color:#8b949e;flex-shrink:0}
.risk-track{flex:1;height:4px;background:#21262d;border-radius:3px;overflow:hidden}
.risk-fill{height:100%;border-radius:3px}
.chart-wrap{position:relative;width:100%;height:115px;margin-top:8px}
.footer{display:flex;align-items:center;justify-content:space-between;padding:10px 0 0;font-size:10px;color:#6e7681;border-top:1px solid #21262d;margin-top:8px}
.hidden-pnl{filter:blur(7px);pointer-events:none;user-select:none}
.empty{color:#6e7681;font-style:italic;padding:10px 0}
.status-pill{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600}
.status-running{background:rgba(63,185,80,0.15);color:#3fb950}
.status-idle{background:#21262d;color:#8b949e}
.status-halted{background:rgba(248,81,73,0.15);color:#f85149}
.pnl-flash{animation:flash 0.5s ease}
@keyframes flash{0%{background:rgba(63,185,80,0.2)}100%{background:transparent}}
.conn-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #21262d;font-size:12px}
.conn-row:last-child{border-bottom:none}
</style>
</head>
<body>
<div class="dash">

<div class="header">
  <div class="header-left">
    <div class="avatar">SK</div>
    <div>
      <div class="trader-name">Suresh Kumar</div>
      <div class="trader-sub">KL3080 &middot; Zerodha Kite &middot; <span id="hdr-mode">--</span></div>
    </div>
  </div>
  <div class="header-right">
    <span id="mode-badge" class="badge badge-live"><span class="pulse" id="mode-pulse"></span><span id="mode-label">LIVE</span></span>
    <span class="badge badge-strategy"><i class="ti ti-chart-line" style="font-size:12px"></i>{{ strategy_label }}</span>
    <span id="status-badge" class="status-pill status-idle">IDLE</span>
    <button class="btn" onclick="togglePnl()" id="pnl-btn"><i class="ti ti-eye-off" id="pnl-icon" style="font-size:13px"></i><span id="pnl-btn-txt">Hide P&L</span></button>
  </div>
</div>

<div class="kpi-grid">
  <div class="kpi">
    <div class="kpi-label"><i class="ti ti-trending-up" style="font-size:12px"></i>Today's P&L</div>
    <div class="kpi-value" id="pnl-val">--</div>
    <div class="kpi-sub" id="pnl-sub">--</div>
  </div>
  <div class="kpi">
    <div class="kpi-label"><i class="ti ti-trophy" style="font-size:12px"></i>Win rate</div>
    <div class="kpi-value" id="wr-val">--</div>
    <div class="kpi-sub" id="wr-sub">--</div>
  </div>
  <div class="kpi">
    <div class="kpi-label"><i class="ti ti-receipt" style="font-size:12px"></i>Brokerage</div>
    <div class="kpi-value red" id="brok-val">--</div>
    <div class="kpi-sub" id="net-pnl-sub">Net P&L: --</div>
  </div>
  <div class="kpi">
    <div class="kpi-label"><i class="ti ti-activity" style="font-size:12px"></i>Market</div>
    <div class="kpi-value" id="mkt-val">--</div>
    <div class="kpi-sub" id="mkt-sub">--</div>
  </div>
  <div class="kpi">
    <div class="kpi-label"><i class="ti ti-shield" style="font-size:12px"></i>Risk score</div>
    <div class="kpi-value" id="risk-val">--</div>
    <div class="kpi-sub" id="risk-sub">--</div>
  </div>
  <div class="kpi">
    <div class="kpi-label"><i class="ti ti-arrows-exchange" style="font-size:12px"></i>Last signal</div>
    <div class="kpi-value" id="sig-val" style="font-size:16px;padding-top:2px">--</div>
    <div class="kpi-sub" id="sig-sub">--</div>
  </div>
  <div class="kpi">
    <div class="kpi-label"><i class="ti ti-clock" style="font-size:12px"></i>Session time</div>
    <div class="kpi-value" id="sess-val" style="font-size:16px;padding-top:2px">--</div>
    <div class="kpi-sub" id="hb-sub">--</div>
  </div>
</div>

<div class="row2">
  <div class="card">
    <div class="card-title"><i class="ti ti-target" style="font-size:12px"></i>Daily progress</div>
    <div class="progress-wrap">
      <div class="progress-label"><span>P&L vs cap</span><span id="prog-pnl">--</span></div>
      <div class="progress-bar"><div class="progress-fill" id="prog-pnl-bar" style="width:0%;background:#3fb950"></div></div>
    </div>
    <div class="progress-wrap">
      <div class="progress-label"><span>Trades used</span><span id="prog-trades">--</span></div>
      <div class="progress-bar"><div class="progress-fill" id="prog-trades-bar" style="width:0%;background:#58a6ff"></div></div>
    </div>
    <div class="progress-wrap">
      <div class="progress-label"><span>Drawdown</span><span id="prog-dd">--</span></div>
      <div class="progress-bar"><div class="progress-fill" id="prog-dd-bar" style="width:0%;background:#e3b341"></div></div>
    </div>
    <div class="progress-wrap" style="margin-bottom:0">
      <div class="progress-label"><span>Session</span><span id="prog-sess">--</span></div>
      <div class="progress-bar"><div class="progress-fill" id="prog-sess-bar" style="width:0%;background:#6e7681"></div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="ti ti-chart-area" style="font-size:12px"></i>Intraday equity</div>
    <div class="chart-wrap">
      <canvas id="eqChart" role="img" aria-label="Intraday equity curve">Equity curve for today.</canvas>
    </div>
  </div>
</div>

<div class="section-title"><i class="ti ti-database" style="font-size:12px"></i>Open position</div>
<div class="card full-card" id="pos-card">
  <div class="empty">No open position.</div>
</div>

<div class="section-title"><i class="ti ti-list" style="font-size:12px"></i>Today's executions</div>
<div class="card full-card" style="padding:0;overflow:hidden">
  <div id="trade-table-wrap"><div class="empty" style="padding:14px">No trades yet.</div></div>
</div>

<div class="row3">
  <div class="card">
    <div class="card-title"><i class="ti ti-shield-check" style="font-size:12px"></i>Risk controls</div>
    <div class="risk-meter">
      <div class="risk-bar-row"><span class="risk-label">Daily cap</span><div class="risk-track"><div class="risk-fill" id="r1" style="background:#3fb950;width:0%"></div></div><span id="r1-pct" class="green">0%</span></div>
      <div class="risk-bar-row"><span class="risk-label">Per-trade</span><div class="risk-track"><div class="risk-fill" id="r2" style="background:#e3b341;width:48%"></div></div><span id="r2-pct" class="yellow">48%</span></div>
      <div class="risk-bar-row"><span class="risk-label">Trades</span><div class="risk-track"><div class="risk-fill" id="r3" style="background:#58a6ff;width:0%"></div></div><span id="r3-pct" class="blue">0%</span></div>
    </div>
    <div style="margin-top:10px;font-size:11px;color:#6e7681">Hard cap ₹{{ hard_cap }} &middot; Daily ₹{{ daily_cap }}</div>
  </div>
  <div class="card">
    <div class="card-title"><i class="ti ti-chart-pie" style="font-size:12px"></i>Trade distribution</div>
    <div class="chart-wrap" style="height:100px">
      <canvas id="distChart" role="img" aria-label="Win/Loss distribution donut chart">Win/loss distribution.</canvas>
    </div>
    <div style="display:flex;gap:12px;justify-content:center;margin-top:8px;font-size:11px">
      <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#3fb950;margin-right:4px"></span><span id="dist-win-label">Wins</span></span>
      <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#f85149;margin-right:4px"></span><span id="dist-loss-label">Losses</span></span>
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="ti ti-server" style="font-size:12px"></i>Connections</div>
    <div id="conn-block">
      <div class="conn-row"><span><span class="conn-dot conn-bad" id="kite-dot"></span>Kite API</span><span class="muted" id="kite-txt">--</span></div>
      <div class="conn-row"><span><span class="conn-dot conn-bad" id="ws-dot"></span>WebSocket</span><span class="muted" id="ws-txt">--</span></div>
      <div class="conn-row"><span><span class="conn-dot conn-ok"></span>SSE stream</span><span class="muted" id="sse-txt">Live</span></div>
      <div class="conn-row" style="margin-top:4px;border-top:1px solid #21262d;padding-top:8px;border-bottom:none">
        <span class="muted">Heartbeat</span><span id="hb-ago">--</span>
      </div>
    </div>
  </div>
</div>

<div class="footer">
  <span>NiftyAlgoBot &middot; {{ strategy_label }} &middot; Last update: <span id="last-upd">--</span></span>
  <span id="clock">--</span>
</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
let pnlVisible = true;
const pnlEls = ['pnl-val','pnl-sub'];
const tradeTableId = 'trade-table-wrap';

function togglePnl() {
  pnlVisible = !pnlVisible;
  pnlEls.forEach(id => document.getElementById(id).classList.toggle('hidden-pnl', !pnlVisible));
  document.getElementById('pnl-btn-txt').textContent = pnlVisible ? 'Hide P&L' : 'Show P&L';
  document.getElementById('pnl-icon').className = pnlVisible ? 'ti ti-eye-off' : 'ti ti-eye';
  const rows = document.getElementById(tradeTableId);
  if (rows) rows.classList.toggle('hidden-pnl', !pnlVisible);
}

function fmt(n) { return '₹' + parseFloat(n).toLocaleString('en-IN', {minimumFractionDigits:2,maximumFractionDigits:2}); }
function fmtSgn(n) { const v = parseFloat(n); return (v >= 0 ? '+' : '') + fmt(v); }
function pct(a,b) { return b > 0 ? Math.min(100, Math.round(Math.abs(a)/b*100)) : 0; }

function clock() {
  const now = new Date();
  const pad = n => String(n).padStart(2,'0');
  document.getElementById('clock').textContent = pad(now.getHours())+':'+pad(now.getMinutes())+':'+pad(now.getSeconds())+' IST';
  const h = now.getHours(), m = now.getMinutes();
  const open = (h > 9 || (h === 9 && m >= 15)) && (h < 15 || (h === 15 && m <= 30));
  const mv = document.getElementById('mkt-val');
  if (mv) { mv.textContent = open ? 'Open' : 'Closed'; mv.className = 'kpi-value ' + (open ? 'green' : 'red'); }
  const ms = document.getElementById('mkt-sub');
  if (ms) ms.textContent = open ? 'Market hours active' : 'Market closed';
  const seVal = document.getElementById('sess-val');
  if (seVal) { const hm = pad(h)+':'+pad(m); seVal.textContent = hm; }
  const sessBar = document.getElementById('prog-sess-bar');
  const sessLbl = document.getElementById('prog-sess');
  if (sessBar) {
    const startMin = 9*60+15, endMin = 15*60+30, cur = h*60+m;
    const elapsed = Math.max(0, cur - startMin);
    const total = endMin - startMin;
    const p = Math.min(100, Math.round(elapsed/total*100));
    sessBar.style.width = p + '%';
    if (sessLbl) sessLbl.textContent = p + '% of session';
  }
}
setInterval(clock, 1000); clock();

const eqData = { labels:[], values:[] };
let eqChart, distChart;

function initCharts() {
  const grd = 'rgba(255,255,255,0.05)';
  const tc = '#6e7681';
  eqChart = new Chart(document.getElementById('eqChart'), {
    type: 'line',
    data: { labels: eqData.labels, datasets: [{ label:'P&L', data: eqData.values, borderColor:'#3fb950', backgroundColor:'rgba(63,185,80,0.07)', borderWidth:1.5, fill:true, tension:0.35, pointRadius:0 }] },
    options: { responsive:true, maintainAspectRatio:false, animation:false, plugins:{ legend:{display:false}, tooltip:{ callbacks:{ label: ctx => fmt(ctx.parsed.y) } } }, scales:{ x:{ grid:{color:grd}, ticks:{color:tc,font:{size:9},maxTicksLimit:6,maxRotation:0} }, y:{ grid:{color:grd}, ticks:{color:tc,font:{size:9}, callback: v => '₹'+(v>=0?'+':'')+v.toLocaleString('en-IN')} } } }
  });
  distChart = new Chart(document.getElementById('distChart'), {
    type:'doughnut',
    data:{ labels:['Wins','Losses'], datasets:[{ data:[1,0], backgroundColor:['#3fb950','#f85149'], borderWidth:0, hoverOffset:3 }] },
    options:{ responsive:true, maintainAspectRatio:false, cutout:'70%', plugins:{ legend:{display:false}, tooltip:{ callbacks:{ label: ctx => ctx.label+': '+ctx.parsed } } } }
  });
}
initCharts();

function reasonPill(r) {
  if (!r) return r;
  const map = { TARGET:'pill-target', STOP_LOSS:'pill-sl', HARD_CAP:'pill-cap', OPPOSITE_SIGNAL:'pill-opp', SHUTDOWN:'pill-shut', TIME_EXIT:'pill-shut' };
  const cls = map[r] || 'pill-shut';
  return '<span class="pill '+cls+'">'+(r==='OPPOSITE_SIGNAL'?'OPP_SIG':r)+'</span>';
}

let prevPnl = null;
function update(s) {
  document.getElementById('last-upd').textContent = new Date().toLocaleTimeString('en-IN');
  const mode = (s.mode || 'paper').toUpperCase();
  document.getElementById('hdr-mode').textContent = mode;
  document.getElementById('mode-label').textContent = mode;
  const mb = document.getElementById('mode-badge');
  mb.className = 'badge ' + (mode === 'LIVE' ? 'badge-live' : 'badge-paper');
  const sb = document.getElementById('status-badge');
  const st = (s.status || 'idle').toLowerCase();
  sb.textContent = st.toUpperCase();
  sb.className = 'status-pill status-'+st;

  const today = s.today || {};
  const pnl = parseFloat(today.realized_pnl || 0);
  const wins = parseInt(today.wins || 0);
  const losses = parseInt(today.losses || 0);
  const tc2 = parseInt(today.trades_count || 0);
  const maxT = parseInt(s.max_trades || 100);
  const cap = parseFloat(s.loss_cap || 5900);
  const hardCap = parseFloat(s.hard_cap || 2500);

  const pnlEl = document.getElementById('pnl-val');
  if (prevPnl !== null && pnl !== prevPnl) { pnlEl.classList.add('pnl-flash'); setTimeout(()=>pnlEl.classList.remove('pnl-flash'),500); }
  prevPnl = pnl;
  pnlEl.textContent = fmtSgn(pnl);
  pnlEl.className = 'kpi-value ' + (pnl >= 0 ? 'green' : 'red');
  document.getElementById('pnl-sub').textContent = 'Realized · '+tc2+' trades';

  const wr = (wins + losses) > 0 ? Math.round(wins/(wins+losses)*100) : 0;
  document.getElementById('wr-val').textContent = wr + '%';
  document.getElementById('wr-val').className = 'kpi-value ' + (wr >= 60 ? 'green' : wr >= 40 ? 'yellow' : 'red');
  document.getElementById('wr-sub').textContent = wins+'W · '+losses+'L today';

  const brok = parseFloat(today.total_brokerage || 0);
  const netPnl = parseFloat(today.net_pnl || pnl - brok);
  document.getElementById('brok-val').textContent = '-' + fmt(brok);
  document.getElementById('net-pnl-sub').textContent = 'Net P&L: ' + (netPnl >= 0 ? '+' : '') + fmt(netPnl);
  document.getElementById('net-pnl-sub').style.color = netPnl >= 0 ? '#3fb950' : '#f85149';

  const drawdown = Math.abs(Math.min(0, pnl));
  const riskPct = pct(Math.abs(pnl), cap);
  let riskLabel = 'Low', riskClass = 'green';
  if (riskPct >= 80) { riskLabel = 'Critical'; riskClass = 'red'; }
  else if (riskPct >= 50) { riskLabel = 'High'; riskClass = 'red'; }
  else if (riskPct >= 30) { riskLabel = 'Medium'; riskClass = 'yellow'; }
  document.getElementById('risk-val').textContent = riskLabel;
  document.getElementById('risk-val').className = 'kpi-value ' + riskClass;
  document.getElementById('risk-sub').textContent = fmt(Math.abs(pnl))+' / '+fmt(cap)+' cap';

  const sig = s.last_signal || {};
  const sigEl = document.getElementById('sig-val');
  sigEl.textContent = sig.type || 'NONE';
  sigEl.className = 'kpi-value ' + (sig.type==='BUY'?'green':sig.type==='SELL'?'red':'muted');
  document.getElementById('sig-sub').textContent = sig.time ? sig.time+' · Spot '+sig.spot : (sig.reason||'--');

  const hb = s.last_heartbeat;
  if (hb) {
    const ago = Math.round((Date.now() - new Date(hb))/1000);
    document.getElementById('hb-ago').textContent = ago+'s ago';
    document.getElementById('hb-ago').className = ago < 15 ? 'green' : 'red';
    document.getElementById('hb-sub').textContent = 'Last: '+new Date(hb).toLocaleTimeString('en-IN');
  }

  const pnlP = pct(Math.abs(pnl), cap);
  document.getElementById('prog-pnl-bar').style.width = pnlP+'%';
  document.getElementById('prog-pnl-bar').style.background = pnl>=0?'#3fb950':'#f85149';
  document.getElementById('prog-pnl').textContent = fmtSgn(pnl)+' / '+fmt(cap);

  const tradeP = pct(tc2, maxT);
  document.getElementById('prog-trades-bar').style.width = tradeP+'%';
  document.getElementById('prog-trades').textContent = tc2+' / '+maxT;

  const ddP = pct(drawdown, hardCap);
  document.getElementById('prog-dd-bar').style.width = ddP+'%';
  document.getElementById('prog-dd').textContent = fmt(drawdown)+' / '+fmt(hardCap);

  document.getElementById('r1').style.width = pnlP+'%';
  document.getElementById('r1-pct').textContent = pnlP+'%';
  document.getElementById('r3').style.width = tradeP+'%';
  document.getElementById('r3-pct').textContent = tradeP+'%';

  const kiteOk = !!s.kite_connected;
  const wsOk = !!s.ws_connected;
  document.getElementById('kite-dot').className = 'conn-dot ' + (kiteOk?'conn-ok':'conn-bad');
  document.getElementById('kite-txt').textContent = kiteOk ? 'Connected' : 'Disconnected';
  document.getElementById('ws-dot').className = 'conn-dot ' + (wsOk?'conn-ok':'conn-bad');
  document.getElementById('ws-txt').textContent = wsOk ? 'Connected' : 'Disconnected';

  const pos = s.open_position;
  const posCard = document.getElementById('pos-card');
  if (pos && pos.symbol) {
    const pnlCls = parseFloat(pos.pnl) >= 0 ? 'green' : 'red';
    const trailBadge = pos.trail_lock ? ' <span class="pill pill-target">LOCKED</span>' : pos.trail_be ? ' <span class="pill pill-opp">BE</span>' : '';
    posCard.innerHTML = '<table class="pos-table"><thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>SL</th><th>Target</th><th>LTP</th><th>P&L</th></tr></thead><tbody><tr><td class="pos-symbol">'+pos.symbol+'</td><td>'+pos.qty+'</td><td>₹'+pos.entry+'</td><td>₹'+pos.sl+trailBadge+'</td><td>₹'+pos.target+'</td><td class="pos-symbol">₹'+pos.ltp+'</td><td class="'+pnlCls+'">'+fmt(pos.pnl)+'</td></tr></tbody></table>';
    const curPnl = parseFloat(pos.pnl || 0);
    const tick = eqData.labels.length;
    const now = new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'});
    if (!eqData.labels.includes(now)) { eqData.labels.push(now); eqData.values.push(pnl + curPnl); if (eqChart) eqChart.update(); }
  } else {
    posCard.innerHTML = '<div class="empty">No open position.</div>';
  }

  const trades = s.trade_history || [];
  const tw = document.getElementById('trade-table-wrap');
  if (trades.length > 0) {
    const rows = [...trades].reverse().map(t => {
      const pv  = parseFloat(t.pnl || 0);
      const bv  = parseFloat(t.brokerage || 0);
      const nv  = parseFloat(t.net_pnl !== undefined ? t.net_pnl : pv - bv);
      return '<tr class="trade-row"><td>'+t.entry_time+'</td><td><span class="pill '+(t.side==='BUY'?'pill-buy':'pill-sell')+'">'+t.side+'</span></td><td class="pos-symbol">'+t.symbol+'</td><td>₹'+t.entry+'</td><td>₹'+t.exit+'</td><td class="'+(pv>=0?'green':'red')+'">'+(pv>=0?'+':'')+fmt(pv)+'</td><td class="red">-'+fmt(bv)+'</td><td class="'+(nv>=0?'green':'red')+'">'+(nv>=0?'+':'')+fmt(nv)+'</td><td>'+reasonPill(t.exit_reason)+'</td></tr>';
    }).join('');
    tw.innerHTML = '<table class="trade-table"><thead><tr><th>Time</th><th>Side</th><th>Symbol</th><th>Entry</th><th>Exit</th><th>Gross P&L</th><th>Brokerage</th><th>Net P&L</th><th>Reason</th></tr></thead><tbody>'+rows+'</tbody></table>';
    if (!pnlVisible) tw.classList.add('hidden-pnl');

    const now2 = new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'});
    if (!eqData.labels.includes(now2)) { eqData.labels.push(now2); eqData.values.push(pnl); if (eqChart) eqChart.update(); }
  } else {
    tw.innerHTML = '<div class="empty" style="padding:14px">No trades yet today.</div>';
  }

  if (distChart) {
    distChart.data.datasets[0].data = [Math.max(wins,0), Math.max(losses,0)];
    distChart.update('none');
  }
  document.getElementById('dist-win-label').textContent = 'Wins ' + wins;
  document.getElementById('dist-loss-label').textContent = 'Losses ' + losses;
}

function connect() {
  const es = new EventSource('/stream');
  document.getElementById('sse-txt').textContent = 'Connecting...';
  es.onopen = () => { document.getElementById('sse-txt').textContent = 'Live'; };
  es.onmessage = e => { try { update(JSON.parse(e.data)); } catch(err){} };
  es.onerror = () => { document.getElementById('sse-txt').textContent = 'Reconnecting...'; es.close(); setTimeout(connect, 2000); };
}
connect();
</script>
</body>
</html>"""


def _state():
    s = get_state()
    return {
        **s,
        "mode": config.get("mode", "trading_mode", default="paper"),
        "manual_approval": config.get("mode", "manual_approval", default=False),
        "max_trades": config.get("risk", "max_trades_per_day", default=100),
        "loss_cap": config.get("risk", "daily_loss_cap", default=5900),
        "hard_cap": config.get("risk", "hard_per_trade_loss_cap", default=2500),
    }


@app.route("/")
def index():
    return render_template_string(
        HTML,
        strategy_label=f"VWAP · {config.get('strategy','timeframe_minutes',default=1)}min HA",
        hard_cap=f"{config.get('risk','hard_per_trade_loss_cap',default=2500):,}",
        daily_cap=f"{config.get('risk','daily_loss_cap',default=5900):,}",
    )


@app.route("/stream")
def stream():
    def gen():
        prev = None
        while True:
            try:
                payload = json.dumps(_state(), default=str)
                if payload != prev:
                    prev = payload
                    yield f"data: {payload}\n\n"
                else:
                    yield ": ping\n\n"
            except Exception:
                yield "data: {}\n\n"
            time.sleep(1)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/state")
def api_state():
    from flask import jsonify
    return jsonify(_state())


def run_dashboard():
    host = config.get("dashboard", "host", default="127.0.0.1")
    port = config.get("dashboard", "port", default=5555)
    print(f"\n  Dashboard running at: http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    run_dashboard()
