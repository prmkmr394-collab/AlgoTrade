"""
Kite Connect authentication.

Daily workflow:
  1. Run this script every morning (~8:45 AM IST)
  2. It opens the Kite login URL in your browser
  3. You complete login + 2FA
  4. After successful login, copy the `request_token` from the redirect URL
  5. Paste it back into the terminal
  6. The script generates the access_token and saves it to data/access_token.txt
  7. The main bot reads this file to authenticate

The access_token is valid until 6:00 AM the next day.
"""
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from kiteconnect import KiteConnect

from utils.config_loader import config
from utils.logger import log


TOKEN_FILE = Path(__file__).parent.parent / "data" / "access_token.txt"
TOKEN_DATE_FILE = Path(__file__).parent.parent / "data" / "token_date.txt"


def get_kite_client() -> KiteConnect:
    """Returns an authenticated KiteConnect client. Raises if token invalid/missing."""
    api_key = config.get("broker", "api_key")
    if not api_key or api_key == "YOUR_KITE_API_KEY_HERE":
        raise ValueError("API key not configured. Edit config/config.yaml.")

    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            "Access token not found. Run: python utils/auth.py to generate."
        )

    # Check token is from today
    if TOKEN_DATE_FILE.exists():
        token_date = TOKEN_DATE_FILE.read_text().strip()
        today = datetime.now().strftime("%Y-%m-%d")
        if token_date != today:
            raise ValueError(
                f"Access token is stale (generated {token_date}, today is {today}). "
                "Re-run: python utils/auth.py"
            )

    access_token = TOKEN_FILE.read_text().strip()
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    # Sanity check — fetch profile to verify token works
    try:
        profile = kite.profile()
        log.info(f"Authenticated as: {profile['user_name']} ({profile['user_id']})")
    except Exception as e:
        raise ValueError(f"Token validation failed: {e}")

    return kite


def generate_access_token():
    """Interactive flow to generate today's access token."""
    api_key = config.get("broker", "api_key")
    api_secret = config.get("broker", "api_secret")

    if not api_key or api_key == "YOUR_KITE_API_KEY_HERE":
        print("ERROR: Edit config/config.yaml with your api_key and api_secret first.")
        sys.exit(1)

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()

    print("\n" + "=" * 70)
    print(" KITE CONNECT — DAILY LOGIN")
    print("=" * 70)
    print(f"\n1. Opening login URL in your browser...")
    print(f"   {login_url}")
    print("\n2. Complete login + 2FA in the browser.")
    print("3. After login, you'll be redirected to a URL containing 'request_token=...'")
    print("4. Copy the value of request_token from the URL.\n")

    try:
        webbrowser.open(login_url)
    except Exception:
        print(f"   (Browser auto-open failed. Open the URL manually.)")

    request_token = input("Paste request_token here: ").strip()
    if not request_token:
        print("ERROR: No request_token provided.")
        sys.exit(1)

    try:
        session = kite.generate_session(request_token, api_secret=api_secret)
        access_token = session["access_token"]
    except Exception as e:
        print(f"ERROR: Failed to generate session: {e}")
        sys.exit(1)

    # Save to file
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(access_token)
    TOKEN_DATE_FILE.write_text(datetime.now().strftime("%Y-%m-%d"))

    # Verify
    kite.set_access_token(access_token)
    profile = kite.profile()

    print("\n" + "=" * 70)
    print(f" SUCCESS — Authenticated as {profile['user_name']} ({profile['user_id']})")
    print(f" Access token saved to: {TOKEN_FILE}")
    print(f" Valid until: 6:00 AM tomorrow")
    print("=" * 70 + "\n")
    log.info(f"Access token generated for {profile['user_id']}")


if __name__ == "__main__":
    generate_access_token()
