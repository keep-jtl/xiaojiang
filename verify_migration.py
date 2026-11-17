# -*- coding: utf-8 -*-
"""小财通·真实 GUI 验收核对脚本（只读，绝不修改数据库）。

用法：
  python verify_migration.py before   # 启动新代码前：确认备份、记录基线
  python verify_migration.py after    # 启动新代码后：与基线核对，确认数据安全

本脚本只做 SELECT，并把基线写到 data/verify_baseline.json；不执行迁移、不删除任何数据。
"""
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(PROJECT_ROOT, "data", "xltc.db")
BASELINE = os.path.join(PROJECT_ROOT, "data", "verify_baseline.json")
BACKUP_DIR = os.path.join(PROJECT_ROOT, "data", "backup")


def ro_connect(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_counts(conn):
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def schema_ver(conn):
    try:
        return conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0]
    except Exception:
        return None


def cmd_before():
    if not os.path.exists(REAL_DB):
        print("FAIL 找不到真实库", REAL_DB)
        return 1
    conn = ro_connect(REAL_DB)
    print("integrity_check:", conn.execute("PRAGMA integrity_check").fetchone()[0])
    counts = table_counts(conn)
    ver = schema_ver(conn)
    null_pub = conn.execute("SELECT COUNT(*) FROM news_items WHERE published_ts IS NULL").fetchone()[0]
    has_kind = "news_kind" in [r[1] for r in conn.execute("PRAGMA table_info(news_items)")]
    conn.close()

    # 确认至少有一个可用备份（优先最新的备份：按修改时间倒序）
    backups = [f for f in os.listdir(BACKUP_DIR) if f.endswith(".db")] if os.path.isdir(BACKUP_DIR) else []
    backups.sort(key=lambda b: os.path.getmtime(os.path.join(BACKUP_DIR, b)), reverse=True)
    good_backup = None
    for b in backups:
        c = ro_connect(os.path.join(BACKUP_DIR, b))
        ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        c.close()
        if ok:
            good_backup = b
            break

    print("迁移前 schema 版本:", ver, "| 新闻无发布时间:", null_pub, "| 已有分类字段:", has_kind)
    print("可用备份:", good_backup or "无")
    with open(BASELINE, "w", encoding="utf-8") as f:
        json.dump({"version": ver, "counts": counts, "null_pub": null_pub}, f, ensure_ascii=False, indent=2)
    print("基线已保存:", BASELINE)
    if not good_backup:
        print("FAIL 没有完整可用的备份，不要启动新代码！")
        return 1
    print("PASS 可以启动新代码进行 GUI 验收")
    return 0


def cmd_after():
    if not os.path.exists(BASELINE):
        print("FAIL 缺少基线，请先运行: python verify_migration.py before")
        return 1
    with open(BASELINE, encoding="utf-8") as f:
        base = json.load(f)
    conn = ro_connect(REAL_DB)
    results = []
    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    check("schema 版本=5", schema_ver(conn) == "5", schema_ver(conn))
    has_kind = "news_kind" in [r[1] for r in conn.execute("PRAGMA table_info(news_items)")]
    check("news_items 含 news_kind 字段", has_kind)
    # 分类取值合法
    bad_kinds = conn.execute(
        "SELECT COUNT(*) FROM news_items WHERE news_kind NOT IN ('realtime','report')").fetchone()[0]
    check("所有新闻分类取值合法", bad_kinds == 0, bad_kinds)
    # 非新闻表行数完全不变
    counts = table_counts(conn)
    diff = []
    for t, old in base["counts"].items():
        if t in ("news_items", "news_item_funds", "app_meta"):
            continue
        if counts.get(t) != old:
            diff.append((t, old, counts.get(t)))
    check("非新闻表行数全部不变", not diff, diff)
    # 新闻未被异常清空（迁移不会让新闻凭空消失；减少只可能来自按规则清理）
    check("新闻表仍有数据", counts.get("news_items", 0) > 0, counts.get("news_items"))
    print("integrity_check:", conn.execute("PRAGMA integrity_check").fetchone()[0])
    conn.close()

    p = 0
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if not ok else ""))
        p += ok
    print(f"验收核对通过 {p}/{len(results)}")
    return 0 if p == len(results) else 1


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("before", "after"):
        print(__doc__)
        sys.exit(1)
    sys.exit(cmd_before() if sys.argv[1] == "before" else cmd_after())
