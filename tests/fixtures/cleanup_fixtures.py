# -*- coding: utf-8 -*-
"""清理组合体检测试夹具：只删除明确命名的产物，不做任何通配符/模糊匹配。"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures")

# 逐一列出允许删除的明确文件（含 SQLite 可能产生的 wal/shm）
EXPLICIT_FILES = [
    "test_portfolio.db",
    "test_portfolio.db-wal",
    "test_portfolio.db-shm",
    "fixture_snapshot.json",
]


def main():
    removed = []
    for name in EXPLICIT_FILES:
        p = os.path.join(FIXTURE_DIR, name)
        if os.path.exists(p):
            os.remove(p)
            removed.append(name)
    print("已删除:", removed if removed else "无产物可清理")
    print("保留脚本目录:", FIXTURE_DIR)


if __name__ == "__main__":
    main()
