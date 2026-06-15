"""
Kill switch. Checks for STOP.txt in the bot's root directory.
If present, the bot squares off all positions and halts.

Usage: At any time, create a file named STOP.txt in the bot folder
to immediately halt the bot. Delete it to resume next session.
"""
from pathlib import Path
from utils.logger import log


KILL_FILE = Path(__file__).parent.parent / "STOP.txt"
HALT_FILE = Path(__file__).parent.parent / "HALTED.txt"


def is_kill_switch_active() -> bool:
    """Returns True if STOP.txt exists. Bot must square off and exit."""
    return KILL_FILE.exists()


def is_halted_for_day() -> bool:
    """Returns True if bot has halted itself due to risk limits being hit."""
    return HALT_FILE.exists()


def halt_for_day(reason: str):
    """Bot calls this on itself when daily loss cap or max trades hit."""
    HALT_FILE.write_text(f"Halted at: {reason}\n")
    log.warning(f"BOT HALTED FOR THE DAY: {reason}")


def clear_daily_halt():
    """Called at start of new trading day to clear yesterday's halt flag."""
    if HALT_FILE.exists():
        HALT_FILE.unlink()
        log.info("Cleared previous day's halt flag.")


def acknowledge_kill_switch():
    """Logs the kill switch detection."""
    log.critical("KILL SWITCH ACTIVATED — STOP.txt detected. Squaring off and exiting.")
