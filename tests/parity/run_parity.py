# -*- coding: utf-8 -*-
"""双端一致性：同一夹具快照，Python build_report 与 JS buildReport 逐项对照。

运行：.venv\\Scripts\\python.exe tests\\parity\\run_parity.py
路径全部基于本文件位置动态解析，可在项目内独立复现。
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(PROJECT))
from app.portfolio_analysis import build_report  # noqa: E402

snap = json.loads((PROJECT / "tests" / "fixtures" / "fixture_snapshot.json")
                  .read_text(encoding="utf-8"))

positions = [{
    "code": f["code"], "name": f["name"], "fund_type": f["fund_type"],
    "shares_scaled": f["test_shares_scaled"], "cost_cents": f["test_cost_cents"],
    "nav_rows": [{"date": r["date"], "nav": r["nav"], "pct": r["pct"]}
                 for r in f["nav_rows"]],
} for f in snap["funds"]]
py = build_report(positions)

raw = subprocess.run(["node", str(HERE / "export_js_report.mjs")],
                     capture_output=True, text=True, encoding="utf-8", check=True)
js = json.loads(raw.stdout)

checks = []
def ck(name, a, b):
    checks.append((name, a == b, a, b))

keys = ["fund_count", "total_value_cents", "total_cost_cents", "total_profit_cents",
        "total_profit_pct_scaled", "largest_holding_code",
        "largest_holding_weight_pct_scaled", "top3_concentration_pct_scaled",
        "nav_date_uniform", "nav_dates"]
for k in keys:
    ck("summary." + k, py["summary"][k], js["summary"][k])

py_h = {m["code"]: m for m in py["holdings_structure"]}
js_h = {m["code"]: m for m in js["holdings_structure"]}
ck("持仓基金代码集合", set(py_h), set(js_h))
for code in py_h:
    for k in ["name", "fund_type", "value_cents", "weight_pct_scaled",
              "profit_cents", "profit_pct_scaled", "latest_nav_date"]:
        ck("structure[%s].%s" % (code, k), py_h[code][k], js_h[code][k])

ck("concentration.top3_codes",
   py["concentration"]["top3_codes"], js["concentration"]["top3_codes"])
ck("concentration.top3_concentration",
   py["concentration"]["top3_concentration_pct_scaled"],
   js["concentration"]["top3_concentration_pct_scaled"])
ck("concentration.max_single",
   py["concentration"]["max_single_weight_pct_scaled"],
   js["concentration"]["max_single_weight_pct_scaled"])
py_cw = {m["code"]: m["weight_pct_scaled"] for m in py["concentration"]["items"]}
js_cw = {m["code"]: m["weight_pct_scaled"] for m in js["concentration"]["items"]}
ck("concentration 各只权重", py_cw, js_cw)

ck("style_overlap.available",
   py["style_overlap"]["available"], js["style_overlap"]["available"])
ck("style_overlap.message",
   py["style_overlap"]["message"], js["style_overlap"]["message"])

pl = py["impact"]["latest"]; jl = js["impact"]["latest"]
ck("latest.nav_date_uniform", pl["nav_date_uniform"], jl["nav_date_uniform"])
ck("latest.total_cents", pl["total_cents"], jl["total_cents"])
ck("latest.label", pl["label"], jl["label"])
py_contrib = {m["code"]: m["contribution_cents"] for m in pl["items"]}
js_contrib = {m["code"]: m["contribution_cents"] for m in jl["items"]}
ck("latest 各只贡献", py_contrib, js_contrib)

pc = {m["code"]: (m["profit_cents"], m["profit_pct_scaled"])
      for m in py["impact"]["cumulative"]["items"]}
jc = {m["code"]: (m["profit_cents"], m["profit_pct_scaled"])
      for m in js["impact"]["cumulative"]["items"]}
ck("cumulative 各只盈亏/比例", pc, jc)
ck("cumulative.total_cents",
   py["impact"]["cumulative"]["total_cents"],
   js["impact"]["cumulative"]["total_cents"])

po = py["watchlist"]["objective"]; jo = js["watchlist"]["objective"]
ck("watchlist.max_single",
   po["max_single_weight_pct_scaled"], jo["max_single_weight_pct_scaled"])
ck("watchlist.top3",
   po["top3_concentration_pct_scaled"], jo["top3_concentration_pct_scaled"])
ck("watchlist.latest_nav_dates",
   po["latest_nav_dates"], jo["latest_nav_dates"])
ck("watchlist.no_rating",
   py["watchlist"]["no_rating"], js["watchlist"]["no_rating"])

fails = 0
for name, ok, a, b in checks:
    if not ok:
        fails += 1
        print("FAIL", name, "| py =", a, "| js =", b)
print("\n对照项 %d，失败 %d" % (len(checks), fails))
sys.exit(1 if fails else 0)
