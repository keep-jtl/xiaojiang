# -*- coding: utf-8 -*-
"""portfolio_analysis 单元测试：固定快照 + 手工复算 + 边界，离线可重复。"""
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import portfolio_analysis as pa

SNAP = os.path.join(ROOT, "tests", "fixtures", "fixture_snapshot.json")


def load_positions():
    with open(SNAP, "r", encoding="utf-8") as f:
        snap = json.load(f)
    positions = []
    for sf in snap["funds"]:
        positions.append({
            "code": sf["code"], "name": sf["name"], "fund_type": sf["fund_type"],
            "shares_scaled": sf["test_shares_scaled"],
            "cost_cents": sf["test_cost_cents"],
            "nav_rows": [{"date": r["date"], "nav": r["nav"], "pct": r["pct"]}
                         for r in sf["nav_rows"]],
        })
    return positions


def main():
    positions = load_positions()
    report = pa.build_report(positions)
    assert not report["empty"]

    # 1. 基本结构
    s = report["summary"]
    assert s["fund_count"] == 6
    print("1. 基金数:", s["fund_count"],
          "总市值(分):", s["total_value_cents"],
          "总盈亏(分):", s["total_profit_cents"])

    # 2. 权重之和应=100%（×10000=1000000）
    wsum = sum(x["weight_pct_scaled"] for x in report["holdings_structure"])
    print("2. 权重合计(×10000):", wsum, "（应≈1000000）")
    assert abs(wsum - 1000000) <= 1, "权重合计应≈100%"

    # 3. 手工独立复算最大持仓的市值/权重/浮盈亏
    top = report["holdings_structure"][0]
    pos = next(p for p in positions if p["code"] == top["code"])
    shares = Decimal(pos["shares_scaled"]) / Decimal("10000")
    nav = Decimal(pos["nav_rows"][-1]["nav"]) / Decimal("10000")
    manual_value = int((shares * nav * 100).to_integral_value(rounding=ROUND_HALF_UP))
    manual_profit = manual_value - pos["cost_cents"]
    print("3. 手工复算", top["code"], "市值:", manual_value,
          "模块:", top["value_cents"], "盈亏:", manual_profit,
          "模块:", top["profit_cents"])
    assert manual_value == top["value_cents"]
    assert manual_profit == top["profit_cents"]

    # 4. 排序：市值降序
    vals = [x["value_cents"] for x in report["holdings_structure"]]
    assert vals == sorted(vals, reverse=True), "应按市值降序"
    print("4. 持仓按市值降序 OK；最大持仓:", top["code"],
          "权重(×10000):", top["weight_pct_scaled"])

    # 5. 净值日期口径（含 QDII，预期不统一）
    print("5. 最新净值日期:", s["nav_dates"], "统一:", s["nav_date_uniform"])
    latest = report["impact"]["latest"]
    if not s["nav_date_uniform"]:
        assert latest["total_cents"] is None, "日期不统一不得给组合合计"
        assert latest["label"] == "按各基金最新已公布净值计算"
        print("   日期不统一 → 不冒充今日合计，单只仍带日期与贡献")
    else:
        assert latest["total_cents"] is not None

    # 6. 风格重复诚实占位、不评级
    assert report["style_overlap"]["available"] is False
    assert report["watchlist"]["no_rating"] is True
    print("6. 风格重复数据不足占位；值得关注不做评级 OK")

    # 7. 最大回撤口径标注
    for m in report["recent_risk"]:
        assert m["max_drawdown_basis"] == "unit_nav"
        assert m["max_drawdown_pct_scaled"] <= 0
    print("7. 最大回撤均≤0 且标注 unit_nav 口径 OK")

    # 8. 边界：空持仓
    empty = pa.build_report([])
    assert empty["empty"] is True
    print("8. 空持仓返回 empty 说明 OK")

    # 9. 观测不足：构造仅 5 条净值
    short = [{"code": "x", "name": "x", "fund_type": "",
              "shares_scaled": 10000, "cost_cents": 1000,
              "nav_rows": [{"date": f"2026-01-0{i}", "nav": 10000 + i,
                            "pct": 0} for i in range(1, 6)]}]
    r = pa.build_report(short)
    rm = r["recent_risk"][0]
    assert rm["return_20_pct_scaled"] is None
    assert rm["volatility_20_pct_scaled"] is None
    assert rm["insufficiencies"], "应给数据不足说明"
    print("9. 观测不足 → 近20日/波动率不计算并说明 OK")

    # 10. 可 JSON 序列化
    json.dumps(report)
    print("10. 报告可 JSON 序列化 OK")

    print("\nportfolio_analysis 全部测试通过。")


if __name__ == "__main__":
    main()
