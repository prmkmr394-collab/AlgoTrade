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
        "total_brokerage": 0.0,
        "net_pnl": 0.0,
    },
    "open_position": None,                  # dict with strike/qty/entry/sl/target/current_pnl
    "last_signal": None,                    # dict with type/time/price/vwap
    "trade_history": [],                    # list of completed trades today
    "errors": [],                           # last 20 errors
    "kite_connected": False,
    "ws_connected": False,
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
    """
    Write state to disk atomically.
    On Windows, bot_state.json may be briefly locked by the dashboard process.
    Retry up to 10 times with a short sleep before giving up.
    """
    import time as _time
    STATE_FILE.parent.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)
    for attempt in range(10):
        try:
            tmp.replace(STATE_FILE)
            return
        except PermissionError:
            _time.sleep(0.05)   # 50ms — dashboard read is very brief
    # Last resort: direct write without atomic rename
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        pass   # Never crash the bot over a dashboard write failure


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
    with _lock:
        state = _load()
        state["open_position"] = position
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
        brok = trade.get("brokerage", 0.0)
        state["today"]["total_brokerage"] = round(
            state["today"].get("total_brokerage", 0.0) + brok, 2)
        state["today"]["net_pnl"] = round(
            state["today"]["realized_pnl"] - state["today"]["total_brokerage"], 2)
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
    """
    Reset state for a new trading day.
    If bot is restarted on the SAME calendar day (mid-session restart for a bug fix),
    preserve existing trade history, P&L, and brokerage.
    Only wipe state when it is actually a new calendar date.
    """
    with _lock:
        existing = _load()

        existing_date = None
        if existing.get("started_at"):
            try:
                existing_date = datetime.fromisoformat(
                    existing["started_at"]).date()
            except Exception:
                existing_date = None

        today = datetime.now().date()

        if existing_date == today:
            # Same-day restart — preserve everything, just mark as running
            existing["status"] = "running"
            existing["halt_reason"] = None
            existing["last_heartbeat"] = datetime.now().isoformat()
            _save(existing)
            return

        # New calendar day — full reset
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
