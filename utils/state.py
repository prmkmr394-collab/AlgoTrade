"""
Shared state. The bot writes its current state here; the dashboard reads it.
Backed by a JSON file so dashboard works even if bot is in a different process.
"""
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional


STATE_FILE = Path(__file__).parent.parent / "data" / "bot_state.json"
_lock = threading.Lock()


_default_state = {
    "started_at": None,
    "last_heartbeat": None,
    "status": "idle",                       # idle | running | halted | error
    "halt_reason": None,
    "today": {
        "trades_count": 0,
        "wins": 0,
        "losses": 0,
        "realized_pnl": 0.0,
    },
    "open_position": None,                  # dict with strike/qty/entry/sl/target/current_pnl
    "last_signal": None,                    # dict with type/time/price/vwap
    "trade_history": [],                    # list of completed trades today
    "errors": [],                           # last 20 errors
    "kite_connected": False,
    "ws_connected": False,
    "indicator": {},                        # {bias, ha_close, vwap, distance_pct} — for dashboard
    "risk_gates": {},                       # {gate_name: True/False/'na'} — for dashboard
}


def _load() -> dict:
    if not STATE_FILE.exists():
        STATE_FILE.parent.mkdir(exist_ok=True)
        return dict(_default_state)
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dict(_default_state)


def _save(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    # Write to temp file first
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception:
        return  # If we can't even write temp, skip this save silently

    # Try to rename, with retries on Windows lock errors
    import time as _time
    for attempt in range(5):
        try:
            tmp.replace(STATE_FILE)
            return
        except (PermissionError, OSError):
            _time.sleep(0.1 * (attempt + 1))
    # Final fallback: try direct write (no atomic rename)
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
        try:
            tmp.unlink()
        except Exception:
            pass
    except Exception:
        pass  # Give up gracefully — losing one state save isn't fatal


def get_state() -> dict:
    with _lock:
        return _load()


def update_state(**kwargs):
    """Update top-level keys of state."""
    with _lock:
        state = _load()
        state.update(kwargs)
        state["last_heartbeat"] = datetime.now().isoformat()
        _save(state)


def update_today(**kwargs):
    """Update today's metrics."""
    with _lock:
        state = _load()
        state["today"].update(kwargs)
        state["last_heartbeat"] = datetime.now().isoformat()
        _save(state)


def set_open_position(position: Optional[dict]):
    """Set or clear the open position. Pass None to clear."""
    with _lock:
        state = _load()
        state["open_position"] = position
        state["last_heartbeat"] = datetime.now().isoformat()
        _save(state)


def set_indicator(bias: str = "NEUTRAL", ha_close: Optional[float] = None,
                  vwap: Optional[float] = None, distance_pct: Optional[float] = None):
    """Update the HA vs VWAP indicator data shown on the dashboard."""
    with _lock:
        state = _load()
        state["indicator"] = {
            "bias": bias,
            "ha_close": ha_close,
            "vwap": vwap,
            "distance_pct": distance_pct,
        }
        state["last_heartbeat"] = datetime.now().isoformat()
        _save(state)


def set_risk_gates(gates: dict):
    """Update the 9 risk-gate statuses for the dashboard."""
    with _lock:
        state = _load()
        state["risk_gates"] = gates
        state["last_heartbeat"] = datetime.now().isoformat()
        _save(state)


def add_completed_trade(trade: dict):
    with _lock:
        state = _load()
        state["trade_history"].append(trade)
        state["today"]["trades_count"] += 1
        if trade.get("pnl", 0) > 0:
            state["today"]["wins"] += 1
        else:
            state["today"]["losses"] += 1
        state["today"]["realized_pnl"] += trade.get("pnl", 0)
        state["last_heartbeat"] = datetime.now().isoformat()
        _save(state)


def add_error(error_msg: str):
    with _lock:
        state = _load()
        state["errors"].append({
            "time": datetime.now().isoformat(),
            "msg": str(error_msg)[:500],
        })
        state["errors"] = state["errors"][-20:]  # keep last 20
        _save(state)


def reset_for_new_day():
    """Reset state ONLY if it's a new calendar day. Otherwise preserve existing state."""
    with _lock:
        existing = _load()
        existing_date = None
        if existing.get("started_at"):
            try:
                existing_date = datetime.fromisoformat(existing["started_at"]).date()
            except Exception:
                existing_date = None

        today = datetime.now().date()

        if existing_date == today:
            # Same day restart — preserve trade history, just update status + heartbeat
            existing["status"] = "running"
            existing["last_heartbeat"] = datetime.now().isoformat()
            _save(existing)
            return

        # New day — fresh state
        fresh = dict(_default_state)
        fresh["started_at"] = datetime.now().isoformat()
        fresh["last_heartbeat"] = datetime.now().isoformat()
        fresh["status"] = "running"
        _save(fresh)


def heartbeat():
    """Called every few seconds to confirm bot is alive."""
    with _lock:
        state = _load()
        state["last_heartbeat"] = datetime.now().isoformat()
        _save(state)
