#!/usr/bin/env python3
"""Weekly 3-month relative-strength (RS) ranking of the F&O universe.

Reads fno-rs/universe.json, downloads NSE's daily full bhavcopy (closing prices),
keeps a compact price history in fno-rs/closes.json, and writes:
  fno-rs/rs.json       full result per stock (return, percentile, sector rank, flags)
  fno-rs/rs_pine.txt   one line, "SYMBOL:percentile,..." plus the data date, for the Pine script

Definition (plain, no assumptions hidden):
  3M return = close on the latest session / close on the last session on or before
              (latest date minus 3 calendar months) - 1
  RS        = percentile 1..99 of that return among eligible F&O stocks
              (lowest return = 1, highest = 99, ties share the average rank)
  NSE closes are unadjusted, so a stock is NOT ranked ("n/a") only when NSE's own corporate-actions
  list shows a split / bonus / consolidation / demerger / arrangement / rights ex-date inside the
  window, or when price data is missing. Price moves alone never cause an exclusion.
  If the corporate-actions list cannot be fetched, the job stops and publishes nothing.
This script does not touch the 52W tracker's files (delivery/).
"""
import json, os, pathlib, sys, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone, date

IST = timezone(timedelta(hours=5, minutes=30))
BASE = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_"
DIR = pathlib.Path("fno-rs")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "105"))   # ~3 months + buffer for holidays
ACTION_WORDS = ("split", "sub-division", "subdivision", "bonus", "consolidation", "demerger",
                "arrangement", "rights", "capital reduction")
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
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as r:
                if r.status == 200:
                    return r.read().decode("utf-8", "replace")
                last = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__ + ": " + str(e)
        time.sleep(3 * (i + 1))
    print(f"  failed: {last}")
    return None


def parse_closes(text, wanted):
    """{symbol: close} for EQ series rows of wanted symbols."""
    lines = text.splitlines()
    head = [h.strip().upper() for h in lines[0].split(",")]
    if "SYMBOL" not in head or "CLOSE_PRICE" not in head or "SERIES" not in head:
        return None
    si, ei, ci = head.index("SYMBOL"), head.index("SERIES"), head.index("CLOSE_PRICE")
    out = {}
    for ln in lines[1:]:
        p = [x.strip() for x in ln.split(",")]
        if len(p) <= ci or p[ei] != "EQ" or p[si] not in wanted:
            continue
        try:
            out[p[si]] = float(p[ci])
        except ValueError:
            pass
    return out


def fetch_corp_actions(frm, to):
    """NSE corporate actions (equities) with ex-date in [frm, to]. Raises on any failure."""
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    hdr = dict(HEADERS); hdr["Accept"] = "application/json,text/plain,*/*"
    for warm in ("https://www.nseindia.com/", "https://www.nseindia.com/companies-listing/corporate-filings-actions"):
        try:
            op.open(urllib.request.Request(warm, headers=hdr), timeout=30).read()
        except Exception as e:  # noqa: BLE001
            print("  warm-up:", type(e).__name__, e)
    url = ("https://www.nseindia.com/api/corporates-corporateActions?index=equities"
           f"&from_date={frm.strftime('%d-%m-%Y')}&to_date={to.strftime('%d-%m-%Y')}")
    hdr["Referer"] = "https://www.nseindia.com/companies-listing/corporate-filings-actions"
    last = ""
    for i in range(4):
        try:
            body = op.open(urllib.request.Request(url, headers=hdr), timeout=60).read().decode("utf-8", "replace")
            data = json.loads(body)
            if isinstance(data, dict):
                data = data.get("data", [])
            if not isinstance(data, list):
                raise ValueError("unexpected shape")
            return data
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__ + ": " + str(e)
            time.sleep(4 * (i + 1))
    raise RuntimeError("corporate actions fetch failed: " + last)


def parse_ex(v):
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v.strip(), fmt).date()
        except Exception:  # noqa: BLE001
            pass
    return None


def months_back(d, m):
    y, mo = d.year, d.month - m
    while mo <= 0:
        mo += 12; y -= 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1])
    return date(y, mo, day)


def percentiles(returns):
    """1..99 percentile, ties share average rank. returns: {sym: value}."""
    items = sorted(returns.items(), key=lambda kv: kv[1])
    n = len(items)
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0                       # 0-based
        pct = 50.0 if n == 1 else 1 + 98 * avg_rank / (n - 1)
        for k in range(i, j + 1):
            out[items[k][0]] = int(round(pct))
        i = j + 1
    return out


def main():
    uni = json.loads((DIR / "universe.json").read_text())
    sector_of = {s: sec for sec, lst in uni["sectors"].items() for s in lst}
    wanted = set(sector_of)

    cpath = DIR / "closes.json"
    closes = json.loads(cpath.read_text()) if cpath.exists() else {}
    today = datetime.now(IST).date()
    start = today - timedelta(days=LOOKBACK_DAYS)
    d = start
    while d <= today:
        key = d.isoformat()
        if d.weekday() < 5 and key not in closes:
            text = fetch(BASE + d.strftime("%d%m%Y") + ".csv")
            if text:
                got = parse_closes(text, wanted)
                if got and len(got) > 0.5 * len(wanted):
                    closes[key] = got
                    print(f"{key}: {len(got)} symbols")
                else:
                    print(f"{key}: unusable file")
            else:
                print(f"{key}: no session / not available")
        d += timedelta(days=1)
    closes = {k: v for k, v in closes.items() if k >= start.isoformat()}
    DIR.mkdir(exist_ok=True)
    cpath.write_text(json.dumps(closes, separators=(",", ":")))

    days = sorted(closes)
    if len(days) < 40:
        print(f"ERROR: only {len(days)} sessions available; refusing to publish.")
        sys.exit(1)
    latest = days[-1]
    ref_target = months_back(date.fromisoformat(latest), 3).isoformat()
    ref_days = [x for x in days if x <= ref_target]
    if not ref_days:
        print(f"ERROR: no session on/before {ref_target}; history too short.")
        sys.exit(1)
    ref = ref_days[-1]
    window = [x for x in days if ref <= x <= latest]

    raw = fetch_corp_actions(date.fromisoformat(ref), date.fromisoformat(latest))
    if not raw:
        raise RuntimeError("corporate actions list came back empty; refusing to publish")
    print("  sample row keys:", sorted(raw[0].keys()) if isinstance(raw[0], dict) else type(raw[0]))
    allm = [(r.get("symbol"), r.get("exDate"), r.get("subject")) for r in raw
            if any(w in str(r.get("subject", "")).lower() for w in ACTION_WORDS)]
    print(f"  keyword matches across ALL NSE stocks: {len(allm)}; first 5: {allm[:5]}")
    actions, audit = {}, []
    for row in raw:
        sym = str(row.get("symbol", "")).strip()
        subj = str(row.get("subject") or row.get("purpose") or "")
        exd = parse_ex(str(row.get("exDate") or row.get("exdate") or ""))
        if sym in wanted and exd and ref <= exd.isoformat() <= latest and any(w in subj.lower() for w in ACTION_WORDS):
            actions.setdefault(sym, []).append(f"{exd.isoformat()}: {subj}")
            audit.append({"symbol": sym, "ex_date": exd.isoformat(), "subject": subj})
    print(f"corporate actions fetched: {len(raw)} rows; relevant to universe in window: {len(audit)}")
    (DIR / "corp_actions.json").write_text(json.dumps(audit, indent=1))

    res, rets = {}, {}
    for s in sorted(wanted):
        c0, c1 = closes[ref].get(s), closes[latest].get(s)
        info = {"sector": sector_of[s], "ret3m": None, "rs": None, "sector_rs": None, "flag": None}
        if c0 is None or c1 is None:
            info["flag"] = "no price data"
        else:
            ret = c1 / c0 - 1
            info["ret3m"] = round(ret * 100, 2)
            if s in actions:
                info["flag"] = "; ".join(actions[s])
            else:
                rets[s] = ret
        res[s] = info

    pct = percentiles(rets)
    for s, p in pct.items():
        res[s]["rs"] = p
    for sec in uni["sectors"]:
        grp = {s: rets[s] for s in uni["sectors"][sec] if s in rets}
        for s, p in percentiles(grp).items():
            res[s]["sector_rs"] = p

    out = {"data_date": latest, "ref_date": ref, "ranked": len(rets), "universe": len(wanted),
           "generated": datetime.now(IST).isoformat(timespec="seconds"), "stocks": res}
    (DIR / "rs.json").write_text(json.dumps(out, indent=1))
    pine = ",".join(f"{s}:{res[s]['rs']}" for s in sorted(rets))
    (DIR / "rs_pine.txt").write_text(f"{latest}|{pine}\n")
    flagged = [s for s, v in res.items() if v["flag"]]
    print(f"data {latest}, base {ref}, ranked {len(rets)}/{len(wanted)}; not ranked: {flagged}")


if __name__ == "__main__":
    main()
