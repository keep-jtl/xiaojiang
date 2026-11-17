# -*- coding: utf-8 -*-
"""构建组合体检测试夹具（真实联网一次，产物可离线复现）。

数据性质严格区分：
- 真实数据：基金代码/名称/类型、真实历史净值、净值日期（来自 akshare/天天基金）；
- 测试数据：测试份额、测试成本、测试持仓组合（人为设定，非真实投资记录）。

产物（tests/fixtures/，均为明确路径）：
- test_portfolio.db：独立临时 SQLite 库（不写 data/xltc.db，不碰真实库）；
- fixture_snapshot.json：固定快照（含来源/获取时间/净值日期/版本），离线测试权威输入。
重复运行幂等：先删旧产物再重建（仅删本目录内明确文件）。
"""
import json
import os
import sqlite3
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app import converters as cv
from app import db as dbm
from app.sources import fund_list as fl
from app.sources import fund_nav as navsrc

FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures")
DB_FILE = os.path.join(FIXTURE_DIR, "test_portfolio.db")
SNAP_FILE = os.path.join(FIXTURE_DIR, "fixture_snapshot.json")
SNAPSHOT_VERSION = 1

# code -> (测试份额, 测试本金元, 买入日期)；明确标注：人为测试输入
TEST_HOLDINGS = {
    "005827": ("2000", "50000", "2025-09-01"),
    "110022": ("1500", "30000", "2025-09-01"),
    "000008": ("5000", "8000",  "2025-09-01"),
    "000961": ("3000", "5000",  "2025-09-01"),
    "000191": ("8000", "10000", "2025-09-01"),
    "270042": ("1000", "20000", "2025-09-01"),
}


def main():
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    # 幂等：仅删本目录内两个明确产物
    for p in (DB_FILE, SNAP_FILE):
        if os.path.exists(p):
            os.remove(p)

    # 1. 从真实名单核对代码/名称/类型
    fl.get_fund_list()
    by_code = {c: (n, t) for c, n, t in fl.get_fund_list()}
    funds = []
    for code in TEST_HOLDINGS:
        if code not in by_code:
            raise RuntimeError(f"测试基金 {code} 在真实名单中不存在，停止")
        name, ftype = by_code[code]
        funds.append({"code": code, "name": name, "fund_type": ftype})
    print("已核对", len(funds), "只真实基金")

    # 2. 建立独立临时库
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(dbm.SCHEMA_SQL)
    now = dbm.now_str()

    snapshot_funds = []
    for f in funds:
        code = f["code"]
        # 3. 真实联网拉历史净值
        rows = navsrc.fetch_nav_history(code)
        print(f"  {code} {f['name']} 真实净值 {len(rows)} 行")
        # fund_meta
        conn.execute(
            "INSERT INTO fund_meta (code,name,fund_type,manager,track_index,risk_level,"
            "source,fetched_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (code, f["name"], f["fund_type"], None, None, None,
             "akshare", now, now, now),
        )
        # fund_nav_daily（复用 UPSERT 逻辑）
        navsrc.save_nav_rows(conn, code, rows, source="akshare")
        # 4. 人为测试持仓（holding 状态）
        shares_str, cost_yuan, open_date = TEST_HOLDINGS[code]
        shares_scaled = cv.shares_to_scaled(shares_str)
        cost_cents = cv.yuan_to_fen(cost_yuan)
        conn.execute(
            "INSERT INTO holdings (uuid,fund_code,status,tag,cost_cents,shares_scaled,"
            "open_date,buy_reason,deleted,created_at,updated_at) "
            "VALUES (?,?, 'holding','normal', ?, ?, ?, NULL, 0, ?, ?)",
            (str(uuid.uuid5(uuid.NAMESPACE_URL, "test-holding-" + code)),
             code, cost_cents, shares_scaled, open_date, now, now),
        )
        latest = navsrc.latest_nav(conn, code)
        snapshot_funds.append({
            "code": code,
            "name": f["name"],
            "fund_type": f["fund_type"],
            "test_shares_scaled": shares_scaled,
            "test_cost_cents": cost_cents,
            "open_date": open_date,
            "nav_count": len(rows),
            "latest_nav_date": latest["nav_date"] if latest else None,
            "nav_rows": [
                {"date": r["nav_date"],
                 "nav": r["unit_nav_scaled"],
                 "pct": r["daily_pct_scaled"]}
                for r in rows
            ],
        })
    conn.commit()
    conn.close()

    # 5. 固定快照（离线测试权威输入）
    snapshot = {
        "snapshot_version": SNAPSHOT_VERSION,
        "data_source": "akshare fund_open_fund_info_em / fund_name_em（天天基金）",
        "built_at": now,
        "built_epoch": time.time(),
        "note": "基金与历史净值为真实市场数据；份额/成本/持仓为人为测试输入，不代表真实投资记录。",
        "funds": snapshot_funds,
    }
    with open(SNAP_FILE, "w", encoding="utf-8") as fp:
        json.dump(snapshot, fp, ensure_ascii=False)

    print("\n夹具构建完成：")
    print("  临时库:", DB_FILE, os.path.getsize(DB_FILE), "字节")
    print("  快照:", SNAP_FILE, os.path.getsize(SNAP_FILE), "字节")


if __name__ == "__main__":
    main()
