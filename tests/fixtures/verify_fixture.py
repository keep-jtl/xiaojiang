# -*- coding: utf-8 -*-
"""读回验证夹具：库表计数、外键完整性、快照一致性，并确认真实/副本库未受影响。"""
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from app import db as dbm

FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures")
DB_FILE = os.path.join(FIXTURE_DIR, "test_portfolio.db")
SNAP_FILE = os.path.join(FIXTURE_DIR, "fixture_snapshot.json")


def main():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")

    # 1. 表计数
    fm = conn.execute("SELECT COUNT(*) FROM fund_meta").fetchone()[0]
    hd = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
    nv = conn.execute("SELECT COUNT(*) FROM fund_nav_daily").fetchone()[0]
    print("临时库 fund_meta:", fm, "holdings:", hd, "fund_nav_daily:", nv)
    assert fm == 6 and hd == 6, "应有 6 只基金与 6 笔持仓"

    # 2. 外键完整性
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert not fk, f"外键完整性问题: {fk}"
    print("外键完整性: 无问题")

    # 3. 持仓状态与字段（全 holding，成本/份额非空，清仓字段空）
    rows = conn.execute(
        "SELECT status, cost_cents, shares_scaled, closed_at, sell_cents, profit_cents "
        "FROM holdings"
    ).fetchall()
    for st, cost, shares, closed_at, sell, profit in rows:
        assert st == "holding" and cost is not None and shares is not None
        assert closed_at is None and sell is None and profit is None
    print("持仓状态: 全部 holding 且字段自洽")

    # 4. 每只基金净值与快照一致
    with open(SNAP_FILE, "r", encoding="utf-8") as f:
        snap = json.load(f)
    assert snap["snapshot_version"] == 1
    assert snap["data_source"] and snap["built_at"]
    assert len(snap["funds"]) == 6
    for sf in snap["funds"]:
        db_n = conn.execute(
            "SELECT COUNT(*) FROM fund_nav_daily WHERE fund_code=?", (sf["code"],)
        ).fetchone()[0]
        assert db_n == sf["nav_count"] == len(sf["nav_rows"]), \
            f"{sf['code']} 净值数量不一致"
        assert sf["latest_nav_date"] and sf["test_cost_cents"] and sf["test_shares_scaled"]
    print("快照: 6 只基金，净值数量与库一致，元信息齐全")
    conn.close()

    # 5. 确认副本真实库 data/xltc.db 未被夹具写入（fund_meta 仍 0）
    rconn = sqlite3.connect(dbm.DB_PATH)
    rfm = rconn.execute("SELECT COUNT(*) FROM fund_meta").fetchone()[0]
    rhd = rconn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
    rconn.close()
    print("副本 data/xltc.db fund_meta:", rfm, "holdings:", rhd, "（夹具未写入）")
    assert rfm == 0 and rhd == 0, "夹具不应写副本真实库"

    print("\n夹具验证全部通过。")


if __name__ == "__main__":
    main()
