"""CLI: print whether the saved Canva profile is logged in.

    python check_session.py
    python check_session.py --cookies   # also list canva.com cookies (no values)

The cookie listing is the fastest way to diagnose the Linux "logged out every
time Playwright opens" problem: if it prints 0 cookies, the profile cannot be
decrypted (different password store) or the login never reached Canva.
"""

from __future__ import annotations

import argparse
from datetime import datetime

from canva_bot import inspect_session


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the Canva browser session.")
    parser.add_argument(
        "--cookies",
        action="store_true",
        help="list canva.com cookie names/expiry from the profile (no values)",
    )
    args = parser.parse_args()

    # One browser launch: /projects is an authenticated page, so the login
    # check and the cookie report always describe the same session.
    logged_in, report = inspect_session()

    if args.cookies:
        print(f"canva cookies visible to the bot: {len(report)}")
        for cookie in report:
            expires = cookie["expires"]
            if not expires or expires < 0:
                expiry = "session cookie"
            else:
                expiry = datetime.fromtimestamp(expires).strftime("%Y-%m-%d")
            print(
                f"  {cookie['name']}  domain={cookie['domain']}  "
                f"expires={expiry}  httpOnly={cookie['httpOnly']}  "
                f"secure={cookie['secure']}"
            )
        if not report:
            print(
                "  (none — either not logged in, or the cookie encryption does "
                "not match between login Chrome and Playwright)"
            )

    print("logged in" if logged_in else "not logged in")
    return 0 if logged_in else 1


if __name__ == "__main__":
    raise SystemExit(main())
