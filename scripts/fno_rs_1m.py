#!/usr/bin/env python3
"""Daily 1-month relative-strength (RS) ranking of the F&O universe.

Separate from scripts/fno_rs.py (the weekly 3M job): this file only IMPORTS helper functions from it and never
changes its logic, its files (rs.json, rs_pine.txt, closes.json, corp_actions.json) or its results.

Writes:
  fno-rs/closes_1m.json  compact closing-price history (seeded from closes.json, then topped up daily)
  fno-rs/rs1m.json       full result per stock and per sector
  fno-rs/rs1m_pine.txt   line 1: "asOf|prevAsOf|SYMBOL:current>previous,..."   line 2: "Sector:current>previous;..."

Definition (same rules as the 3M RS, with a one-month look-back):
  1M return = close on the latest session / close on the last session on or before
              (latest date minus 1 calendar month) - 1
  RS        = percentile 1..99 of that return among eligible F&O stocks (ties share the average rank)
  "previous" = the same ranking as of the last session on/before (latest - 7 days), with its own 1M base date
  Sector RS = equal-weight average 1M return of the sector's ranked stocks, percentile 1..99 across sectors
  A stock is NOT ranked only when NSE's corporate-actions list shows a split / bonus / consolidation /
  demerger / arrangement / rights ex-date inside its 1M window, or when price data is missing.
  If the corporate-actions list cannot be fetched, the job stops and publishes nothing.
"""
import json, pathlib, sys
from datetime import datetime, timedelta, date

import fno_rs as base

DIR = pathlib.Path("fno-rs")
LOOKBACK_DAYS = 80      # 1 month + 1 week (previous reading) + holiday buffer


def compute(closes, raw, uni, sector_of):
    """Pure calculation. closes: {date: {sym: close}}, raw: NSE corporate-action rows."""
    wanted = set(sector_of)
    days = sorted(closes)
    latest = days[-1]
    ref_target = base.months_back(date.fromisoformat(latest), 1).isoformat()
    ref_days = [x for x in days if x <= ref_target]
    if not ref_days:
        raise RuntimeError(f"no session on/before {ref_target}; history too short")
    ref = ref_days[-1]

    prev_target = (date.fromisoformat(latest) - timedelta(days=7)).isoformat()
    prev_days = [x for x in days if x <= prev_target]
    prev_latest = prev_days[-1] if prev_days else None
    prev_ref = None
    if prev_latest:
        pr = [x for x in days if x <= base.months_back(date.fromisoformat(prev_latest), 1).isoformat()]
        prev_ref = pr[-1] if pr else None

    def rank(ref_d, latest_d):
        actions = {}
        for row in raw:
            sym = str(row.get("symbol", "")).strip()
            subj = str(row.get("subject") or row.get("purpose") or "")
            exd = base.parse_ex(str(row.get("exDate") or row.get("exdate") or ""))
            if sym in wanted and exd and ref_d <= exd.isoformat() <= latest_d and any(w in subj.lower() for w in base.ACTION_WORDS):
                actions.setdefault(sym, []).append(f"{exd.isoformat()}: {subj}")
        res, rets = {}, {}
        for s in sorted(wanted):
            c0, c1 = closes[ref_d].get(s), closes[latest_d].get(s)
            info = {"sector": sector_of[s], "ret1m": None, "rs": None, "flag": None}
            if c0 is None or c1 is None:
                info["flag"] = "no price data"
            else:
                ret = c1 / c0 - 1
                info["ret1m"] = round(ret * 100, 2)
                if s in actions:
                    info["flag"] = "; ".join(actions[s])
                else:
                    rets[s] = ret
            res[s] = info
        for s, p in base.percentiles(rets).items():
            res[s]["rs"] = p
        return res, rets

    res, rets = rank(ref, latest)
    sec_now = base.sector_rank(rets, uni["sectors"])
    sec_prev = {}
    if prev_latest and prev_ref:
        pres, prets = rank(prev_ref, prev_latest)
        sec_prev = base.sector_rank(prets, uni["sectors"])
        for s in res:
            res[s]["rs_prev"] = pres[s]["rs"]
    else:
        for s in res:
            res[s]["rs_prev"] = None
    return {"latest": latest, "ref": ref, "prev_latest": prev_latest, "prev_ref": prev_ref,
            "res": res, "rets": rets, "sec_now": sec_now, "sec_prev": sec_prev}


def render(out, uni, wanted):
    res, rets, sec_now, sec_prev = out["res"], out["rets"], out["sec_now"], out["sec_prev"]
    js = {"data_date": out["latest"], "ref_date": out["ref"], "prev_data_date": out["prev_latest"],
          "prev_ref_date": out["prev_ref"], "ranked": len(rets), "universe": len(wanted),
          "sectors": {k: {"avg_ret1m": v["avg_ret3m"], "rs": v["rs"], "stocks_used": v["stocks_used"],
                          "rs_prev": (sec_prev.get(k) or {}).get("rs")} for k, v in sec_now.items()},
          "generated": datetime.now(base.IST).isoformat(timespec="seconds"), "stocks": res}
    parts = []
    for s in sorted(rets):
        pv = res[s]["rs_prev"]
        parts.append(f"{s}:{res[s]['rs']}>{pv if pv is not None else '-'}")
    sec_parts = [f"{k}:{v['rs']}>{(sec_prev.get(k) or {}).get('rs', '-')}"
                 for k, v in sorted(sec_now.items(), key=lambda kv: -kv[1]["rs"])]
    txt = f"{out['latest']}|{out['prev_latest'] or '-'}|{','.join(parts)}\n{';'.join(sec_parts)}\n"
    return js, txt


def main():
    uni = json.loads((DIR / "universe.json").read_text())
    sector_of = {s: sec for sec, lst in uni["sectors"].items() for s in lst}
    wanted = set(sector_of)

    cpath = DIR / "closes_1m.json"
    if cpath.exists():
        closes = json.loads(cpath.read_text())
    elif (DIR / "closes.json").exists():
        closes = json.loads((DIR / "closes.json").read_text())      # seed only; that file is not modified
    else:
        closes = {}
    today = datetime.now(base.IST).date()
    start = today - timedelta(days=LOOKBACK_DAYS)
    d = start
    while d <= today:
        key = d.isoformat()
        if d.weekday() < 5 and key not in closes:
            text = base.fetch(base.BASE + d.strftime("%d%m%Y") + ".csv")
            if text:
                got = base.parse_closes(text, wanted)
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

    if len(closes) < 25:
        print(f"ERROR: only {len(closes)} sessions available; refusing to publish.")
        sys.exit(1)
    latest = sorted(closes)[-1]
    # earliest base date needed for the corporate-actions window (previous week's own 1M base)
    probe = compute_dates(closes)
    raw = base.fetch_corp_actions(date.fromisoformat(probe), date.fromisoformat(latest))
    if not raw:
        raise RuntimeError("corporate actions list came back empty; refusing to publish")

    out = compute(closes, raw, uni, sector_of)
    js, txt = render(out, uni, wanted)
    (DIR / "rs1m.json").write_text(json.dumps(js, indent=1))
    (DIR / "rs1m_pine.txt").write_text(txt)
    flagged = [s for s, v in out["res"].items() if v["flag"]]
    print(f"1M data {out['latest']} (base {out['ref']}); previous {out['prev_latest']} (base {out['prev_ref']}); "
          f"ranked {len(out['rets'])}/{len(wanted)}; not ranked: {flagged}")


def compute_dates(closes):
    days = sorted(closes)
    latest = date.fromisoformat(days[-1])
    prev_latest = [x for x in days if x <= (latest - timedelta(days=7)).isoformat()][-1]
    pr = [x for x in days if x <= base.months_back(date.fromisoformat(prev_latest), 1).isoformat()]
    ref = [x for x in days if x <= base.months_back(latest, 1).isoformat()]
    return min((pr or ref)[-1], ref[-1])


if __name__ == "__main__":
    main()
