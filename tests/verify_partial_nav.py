# -*- coding: utf-8 -*-
"""验证部分净值缺失场景（GPT #204 边界要求一）。

构造：6 只真实持仓，其中 4 只保留真实净值、2 只把净值清空。
断言：
1. 完整组合总市值/总收益率/完整组合权重/前三大集中度 一律 None（不可确定）
2. 已知部分小计 = 4 只合计，且带明确标签
3. 缺失项不被当作 0；权重不重新归一化（不出现 100%）
4. 缺失只影响组合层，单只基金自身指标仍完整可用
5. 数据截止日期与距今天数客观给出
"""
import json
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import portfolio_analysis as pa

SNAP = os.path.join(ROOT, "tests", "fixtures", "fixture_snapshot.json")

with open(SNAP, encoding="utf-8") as f:
    snap = json.load(f)

funds = snap["funds"]
MISSING = ["000961", "270042"]   # 天弘沪深300联接 + 广发纳指100 QDII 视为缺净值

positions = []
for fd in funds:
    rows = [] if fd["code"] in MISSING else fd["nav_rows"]
    positions.append({
        "code": fd["code"],
        "name": fd["name"],
        "fund_type": fd["fund_type"],
        "shares_scaled": fd["test_shares_scaled"],
        "cost_cents": fd["test_cost_cents"],
        "nav_rows": rows,
    })

rep = pa.build_report(positions)
s = rep["summary"]

print("=== 部分缺失（6 只中 2 只无净值）===")
print("fund_count =", s["fund_count"])
print("complete =", s["complete"])
print("valued_count / missing_nav_count =", s["valued_count"], "/", s["missing_nav_count"])
print("missing_nav_codes =", s["missing_nav_codes"])
print("total_value_cents =", s["total_value_cents"], "(应为 None)")
print("total_profit_cents =", s["total_profit_cents"], "(应为 None)")
print("total_profit_pct_scaled =", s["total_profit_pct_scaled"], "(应为 None)")
print("known_value_cents =", s["known_value_cents"])
print("known_cost_cents =", s["known_cost_cents"])
print("known_profit_cents =", s["known_profit_cents"])
print("known_note =", s["known_note"])
print("data_as_of =", s["data_as_of"], "age =", s["data_age_days"])

weights = [(h["code"], h["weight_pct_scaled"]) for h in rep["holdings_structure"]]
print("weights =", weights)
print("concentration.items =", rep["concentration"]["items"])
print("top3_concentration =", s["top3_concentration_pct_scaled"])
print("impact.latest.total_cents =", rep["impact"]["latest"]["total_cents"])
print("impact.latest.known_total_cents =", rep["impact"]["latest"]["known_total_cents"])
print("impact.cumulative.total_cents =", rep["impact"]["cumulative"]["total_cents"])
print("watchlist.max_single =", rep["watchlist"]["objective"]["max_single_weight_pct_scaled"])
print("watchlist.top3 =", rep["watchlist"]["objective"]["top3_concentration_pct_scaled"])
print("watchlist.maxdd =", rep["watchlist"]["objective"]["max_drawdown_pct_scaled"])

fails = []
if rep["empty"]:
    fails.append("不应为空")
if s["complete"] is not False:
    fails.append("complete 应为 False")
if s["missing_nav_count"] != 2:
    fails.append("缺失数量应为 2")
if s["total_value_cents"] is not None:
    fails.append("部分缺失时总市值必须 None")
if s["total_profit_cents"] is not None:
    fails.append("部分缺失时总盈亏必须 None")
if s["total_profit_pct_scaled"] is not None:
    fails.append("部分缺失时总收益率必须 None")
if s["known_value_cents"] is None:
    fails.append("已知部分小计不应为 None")
if not s["known_note"]:
    fails.append("已知部分必须有明确标签")
if any(h["weight_pct_scaled"] is not None for h in rep["holdings_structure"]):
    fails.append("部分缺失时不应给出任何完整组合权重")
if rep["concentration"]["items"]:
    fails.append("部分缺失时不应渲染集中度条形")
if s["top3_concentration_pct_scaled"] is not None:
    fails.append("部分缺失时前三大集中度必须 None")
if rep["impact"]["latest"]["total_cents"] is not None:
    fails.append("部分缺失时最新变动合计必须 None")
if rep["impact"]["cumulative"]["total_cents"] is not None:
    fails.append("部分缺失时累计盈亏合计必须 None")
if rep["watchlist"]["objective"]["max_single_weight_pct_scaled"] is not None:
    fails.append("部分缺失时最大持仓占比必须 None")
# 单只指标仍可用
for h in rep["holdings_structure"]:
    if h["has_nav"] and h["value_cents"] is None:
        fails.append(f"{h['code']} 有净值但市值缺失")
for r in rep["recent_risk"]:
    if r["nav_count"] > 0 and r["max_drawdown_pct_scaled"] is None:
        fails.append(f"{r['code']} 有净值但回撤缺失")

# 已知部分小计必须等于 4 只有净值基金市值之和（独立复算对照）
manual = 0
for m in rep["holdings_structure"]:
    if m["has_nav"]:
        manual += m["value_cents"]
if manual != s["known_value_cents"]:
    fails.append(f"已知部分小计不等于明细合计 {manual} vs {s['known_value_cents']}")

# 不得出现「部分权重合计 = 100%」这种归一化假象（因为全部为 None）
sum_w = sum(h["weight_pct_scaled"] or 0 for h in rep["holdings_structure"])
if sum_w != 0:
    fails.append(f"不完整组合各只权重应全空，实际合计 {sum_w}")

json.dumps(rep)  # 必须可序列化

print()
if fails:
    print("FAIL:")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("PASS：部分净值缺失场景 15 项断言全部通过（完整口径不可确定、已知部分有标签、缺失不当 0）")
