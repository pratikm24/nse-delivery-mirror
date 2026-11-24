#!/usr/bin/env python3
"""Build the ready-to-paste Pine script (fno-rs/FnO_RS.pine) from fno-rs/rs_pine.txt and the template.
Symbols are written in TradingView's form (hyphen and ampersand become underscore)."""
import json, pathlib, re
from datetime import datetime, timedelta, timezone

DIR = pathlib.Path("fno-rs")
IST = timezone(timedelta(hours=5, minutes=30))


def tv(sym):
    return sym.replace("-", "_").replace("&", "_")


def lit(text, width=700):
    """Pine string literal chunks joined with '+' (continuation lines)."""
    chunks = [text[i:i + width] for i in range(0, len(text), width)] or [""]
    return " +\n".join('     "' + c.replace('"', "'") + '"' for c in chunks)


def main():
    l1, l2 = (DIR / "rs_pine.txt").read_text().strip().split("\n")
    asof, prev, data = l1.split("|", 2)
    rs_data = ",".join(tv(p.split(":")[0]) + ":" + p.split(":", 1)[1] for p in data.split(","))
    uni = json.loads((DIR / "universe.json").read_text())["sectors"]
    rs = json.loads((DIR / "rs.json").read_text())
    ranked = {s for s, v in rs["stocks"].items() if v["rs"] is not None}
    sec_data = ";".join(f"{name}:{rs['sectors'][name]['rs']}>{rs['sectors'][name]['rs_prev'] if rs['sectors'][name]['rs_prev'] is not None else '-'}:{rs['sectors'][name]['stocks_used']}"
                        for name in sorted(rs["sectors"], key=lambda n: -rs["sectors"][n]["rs"]))
    sec_members = ";".join(f"{name}=," + ",".join(tv(s) for s in lst) + "," for name, lst in uni.items())
    r1, r2 = (DIR / "rs1m_pine.txt").read_text().strip().split("\n")
    asof1m, prev1m, data1m = r1.split("|", 2)
    rs1m_data = ",".join(tv(p.split(":")[0]) + ":" + p.split(":", 1)[1] for p in data1m.split(","))
    out = (pathlib.Path("scripts/pine_template.pine").read_text()
           .replace("__ASOF1M__", asof1m).replace("__PREV1M__", prev1m if prev1m != "-" else "n/a")
           .replace("__RS1M_DATA__", lit(rs1m_data)).replace("__SEC1M_DATA__", lit(r2, 900))
           .replace("__ASOF__", asof).replace("__PREV__", prev if prev != "-" else "n/a")
           .replace("__GENERATED__", datetime.now(IST).strftime("%Y-%m-%d"))
           .replace("__NROWS__", str(len(rs["sectors"]) + 2))
           .replace("__RS_DATA__", lit(rs_data)).replace("__SEC_DATA__", lit(sec_data, 900))
           .replace("__SEC_MEMBERS__", lit(sec_members, 700)))
    (DIR / "FnO_RS.pine").write_text(out)
    print(f"wrote fno-rs/FnO_RS.pine ({len(out)} chars), as of {asof}")


if __name__ == "__main__":
    main()
