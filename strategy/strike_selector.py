"""
Strike selection + expiry resolution.

BankNifty version.
"""

from datetime import datetime, date
from typing import Optional, List, Dict
import pytz

from utils.config_loader import config
from utils.logger import log

IST = pytz.timezone("Asia/Kolkata")


def round_to_nearest(value: float, step: int) -> int:
    return int(round(value / step) * step)


def select_strike(signal_side: str, spot_price: float) -> int:
    """
    BUY  -> ITM CE
    SELL -> ITM PE
    """

    offset = config.get(
        "strategy",
        "itm_offset_points",
        default=100
    )

    step = config.get(
        "strategy",
        "strike_step",
        default=100
    )

    if signal_side == "BUY":
        target = spot_price - offset

    elif signal_side == "SELL":
        target = spot_price + offset

    else:
        raise ValueError(
            f"Invalid signal side: {signal_side}"
        )

    return round_to_nearest(target, step)


def select_expiry(
    instruments: List[Dict],
    expiry_type: str = "next_week",
    reference_date: Optional[date] = None,
) -> Optional[date]:

    if reference_date is None:
        reference_date = datetime.now(IST).date()

    expiries = sorted(
        {
            inst["expiry"]
            for inst in instruments
            if inst.get("expiry")
            and inst["expiry"] >= reference_date
        }
    )

    if not expiries:
        log.error(
            "No future expiries found."
        )
        return None

    if expiry_type == "current_week":
        return expiries[0]

    elif expiry_type == "next_week":

        if len(expiries) < 2:
            log.warning(
                "Only one expiry found. "
                "Using current week."
            )
            return expiries[0]

        return expiries[1]

    raise ValueError(
        f"Invalid expiry_type: {expiry_type}"
    )


def find_option_instrument(
    instruments: List[Dict],
    strike: int,
    option_type: str,
    expiry: date,
) -> Optional[Dict]:

    for inst in instruments:

        if (
            inst.get("name") == "BANKNIFTY"
            and inst.get("strike") == strike
            and inst.get("instrument_type") == option_type
            and inst.get("expiry") == expiry
        ):
            return inst

    return None


def select_option_contract(
    kite,
    signal_side: str,
    spot_price: float,
) -> Optional[Dict]:

    try:
        instruments = kite.instruments("NFO")

    except Exception as e:
        log.error(
            f"Failed to fetch instruments: {e}"
        )
        return None

    banknifty_options = [
        i
        for i in instruments
        if i.get("name") == "BANKNIFTY"
        and i.get("segment") == "NFO-OPT"
        and i.get("instrument_type") in (
            "CE",
            "PE",
        )
    ]

    if not banknifty_options:
        log.error(
            "No BANKNIFTY options found."
        )
        return None

    expiry_type = config.get(
        "strategy",
        "expiry_type",
        default="next_week",
    )

    expiry = select_expiry(
        banknifty_options,
        expiry_type=expiry_type,
    )

    if not expiry:
        return None

    strike = select_strike(
        signal_side,
        spot_price,
    )

    option_type = (
        "CE"
        if signal_side == "BUY"
        else "PE"
    )

    inst = find_option_instrument(
        banknifty_options,
        strike,
        option_type,
        expiry,
    )

    if inst is None:
        log.error(
            f"Option not found: "
            f"BANKNIFTY {expiry} "
            f"{strike} {option_type}"
        )
        return None

    log.info(
        f"Selected option: "
        f"{inst['tradingsymbol']} "
        f"(token={inst['instrument_token']}, "
        f"strike={strike}, "
        f"expiry={expiry})"
    )

    return inst


def find_futures_token(
    kite,
    expiry_type: str = "current_week",
) -> Optional[int]:

    try:
        instruments = kite.instruments("NFO")

    except Exception as e:
        log.error(
            f"Failed to fetch instruments: {e}"
        )
        return None

    today = datetime.now(IST).date()

    banknifty_futs = sorted(
        [
            i
            for i in instruments
            if i.get("name") == "BANKNIFTY"
            and i.get("segment") == "NFO-FUT"
            and i.get("expiry")
            and i["expiry"] >= today
        ],
        key=lambda x: x["expiry"]
    )

    if not banknifty_futs:
        log.error(
            "No BANKNIFTY futures found."
        )
        return None

    nearest = banknifty_futs[0]

    log.info(
        f"Using BANKNIFTY futures: "
        f"{nearest['tradingsymbol']} "
        f"(token={nearest['instrument_token']}, "
        f"expiry={nearest['expiry']})"
    )

    return nearest["instrument_token"]


def find_banknifty_index_token(
    kite,
) -> Optional[int]:

    try:
        instruments = kite.instruments("NSE")

    except Exception as e:
        log.error(
            f"Failed to fetch NSE instruments: {e}"
        )
        return None

    candidates = {
        "NIFTY BANK",
        "BANKNIFTY",
        "NIFTYBANK",
    }

    for inst in instruments:

        tradingsymbol = str(
            inst.get(
                "tradingsymbol",
                ""
            )
        ).upper()

        if tradingsymbol in candidates:

            log.info(
                f"Using BANKNIFTY index: "
                f"{inst['tradingsymbol']} "
                f"(token={inst['instrument_token']})"
            )

            return inst["instrument_token"]

    log.error(
        "BANKNIFTY index token not found."
    )

    return None


# ==================================================
# Compatibility wrappers
# Existing code still imports NIFTY functions
# ==================================================

def find_nifty_index_token(
    kite,
) -> Optional[int]:
    return find_banknifty_index_token(kite)