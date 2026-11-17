# -*- coding: utf-8 -*-
"""生成整机验收各场景的独立数据库（全部输出到 tests/acceptance/）。

场景：
  scn_normal.db  正常6只（含完整历史，QDII 11-13、其余 11-16，覆盖不同净值日期）
  scn_empty.db   空持仓（空库结构，0 持仓/0 净值）
  scn_nonav.db   6 只 holding + fund_meta，但无任何净值（净值缺失）
  scn_stale.db   仅保留 2026-08-31 及之前净值（数据过期，最新约 2.5 个月前）
本脚本只在验收目录内写文件，不碰 data/xltc.db，不碰真实库。
"""
import shutil
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
FIXTURE = PROJECT / "tests" / "fixtures" / "test_portfolio.db"
EMPTY_DB = PROJECT / "data" / "xltc.db"
OUT = HERE


def make(name, fn):
    target = OUT / name
    shutil.copy2(FIXTURE, target)
    conn = sqlite3.connect(target)
    try:
        fn(conn)
        conn.commit()
    finally:
        conn.close()
    print("已生成", name, target.stat().st_size, "字节")


def normal(conn):
    pass  # 原样复制


def nonav(conn):
    conn.execute("DELETE FROM fund_nav_daily")


def stale(conn):
    conn.execute("DELETE FROM fund_nav_daily WHERE nav_date > '2026-08-31'")


def partial(conn):
    """部分缺失：6 只持仓中 2 只（000961 天弘、270042 广发QDII）清空净值。
    用于验证「不得把已知部分合计冒充完整组合总资产」。
    """
    conn.execute("DELETE FROM fund_nav_daily WHERE fund_code IN ('000961','270042')")


make("scn_normal.db", normal)
make("scn_nonav.db", nonav)
make("scn_stale.db", stale)
make("scn_partial.db", partial)

# 空库：复制当前空库（保持表结构，0 数据）
empty_target = OUT / "scn_empty.db"
shutil.copy2(EMPTY_DB, empty_target)
print("已生成 scn_empty.db", empty_target.stat().st_size, "字节")

# 复核各场景关键计数
for db in ["scn_normal.db", "scn_empty.db", "scn_nonav.db", "scn_stale.db",
           "scn_partial.db"]:
    c = sqlite3.connect(OUT / db)
    h = c.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
    n = c.execute("SELECT COUNT(*) FROM fund_nav_daily").fetchone()[0]
    mx = c.execute("SELECT MAX(nav_date) FROM fund_nav_daily").fetchone()[0]
    print(db, "holdings =", h, "nav =", n, "最新日期 =", mx)
    c.close()
