#!/usr/bin/env python3
"""Download NSE's full bhavcopy (which carries delivery data) and store it in this repo.

GitHub's runners are not blocked by NSE, which is the whole point: Apps Script cannot
reach nsearchives.nseindia.com, but it can read raw.githubusercontent.com.

Writes delivery/YYYY-MM-DD.csv and refreshes delivery/index.json, which the Apps
Script side reads to discover what is available. Missing files (weekends, holidays,
or a session not published yet) are not an error.
"""
import json, os, pathlib, sys, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
BASE = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_"
OUT = pathlib.Path("delivery")
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "3"))   # today plus a couple of catch-up days
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "text/csv,application/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/all-reports",
}


def fetch(url, attempts=3, timeout=60):
    last = ""
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status == 200:
                    return r.read().decode("utf-8", "replace")
                last = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code == 404:
                return None                      # no session that day: not an error
        except Exception as e:                   # noqa: BLE001 - report whatever happened
            last = type(e).__name__ + ": " + str(e)
        time.sleep(3 * (i + 1))
    print(f"  failed: {last}")
    return None


def valid(text):
    """A real bhavcopy, not an error page."""
    if not text:
        return False
    head = text.splitlines()[0].upper() if text.splitlines() else ""
    return "SYMBOL" in head and "DELIV_PER" in head and len(text.splitlines()) > 50


def main():
    OUT.mkdir(exist_ok=True)
    today = datetime.now(IST).date()
    written = []
    for back in range(BACKFILL_DAYS):
        day = today - timedelta(days=back)
        if day.weekday() >= 5:                   # Saturday, Sunday
            continue
        target = OUT / f"{day.isoformat()}.csv"
        if target.exists():
            continue
        url = BASE + day.strftime("%d%m%Y") + ".csv"
        print(f"{day}: {url}")
        text = fetch(url)
        if not valid(text):
            print("  nothing usable (holiday, not published yet, or refused)")
            continue
        target.write_text(text, encoding="utf-8")
        written.append(day.isoformat())
        print(f"  saved {target} ({len(text.splitlines()) - 1} rows)")

    sessions = sorted(p.stem for p in OUT.glob("*.csv"))
    (OUT / "index.json").write_text(json.dumps({
        "updated": datetime.now(IST).isoformat(timespec="seconds"),
        "sessions": sessions,
    }, indent=1), encoding="utf-8")

    print(f"\n{len(written)} new session(s): {', '.join(written) or 'none'}")
    print(f"{len(sessions)} session(s) held in total")
    # Exit 0 even when nothing was written: holidays must not show as failures.
    return 0


if __name__ == "__main__":
    sys.exit(main())
