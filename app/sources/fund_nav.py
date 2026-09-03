# -*- coding: utf-8 -*-
"""场外基金净值数据源适配层（akshare → 天天基金公开接口）

数据源抽象层约定：只在本文件接触第三方库与网络；
返回/写入一律为缩放整数（converters 换算），业务层不接触原始浮点。

接口说明（akshare 1.18.94，2026-09-03 真测）：
    ak.fund_open_fund_info_em(symbol=代码, indicator="单位净值走势")
    → DataFrame[净值日期, 单位净值, 日增长率]
    - 单位净值 4 位小数；日增长率为百分数（0.13 表示 +0.13%），历史部分日期可能缺失
    - 参数名是新版 symbol（旧版 fund 已改名），勿改回
"""
import datetime

import akshare as ak

from .. import converters as cv
from .. import db as dbm


def fetch_nav_history(code):
    """联网拉取基金历史净值（全量，akshare 接口一次返回全部历史）

    返回：[{nav_date, unit_nav_scaled, daily_pct_scaled}]（未入库，由调用方写入）
    失败时抛出异常（由上层决定降级/提示）。
    """
    df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    rows = []
    for _, r in df.iterrows():
        date = str(r["净值日期"])[:10]           # Timestamp → 'YYYY-MM-DD'
        nav = r["单位净值"]
        pct = r["日增长率"]
        if _is_missing(nav):
            continue                             # 无净值的行跳过（不应出现）
        try:
            nav_scaled = cv.nav_to_scaled(nav)
        except ValueError as e:
            # 入库校验点：净值本身异常 → 整行跳过（净值缺失无法算账）
            print(f"[fund_nav] 净值异常跳过：{code} {date} {e}")
            continue
        pct_scaled = None
        if not _is_missing(pct):
            try:
                pct_scaled = cv.pct_to_scaled(pct)
            except ValueError as e:
                # 涨跌幅异常 → 只丢涨跌幅，净值照存（涨跌幅只用于展示/提醒，不参与算账）
                print(f"[fund_nav] 涨跌幅异常丢弃：{code} {date} {e}")
        rows.append({"nav_date": date, "unit_nav_scaled": nav_scaled, "daily_pct_scaled": pct_scaled})
    return rows


def _is_missing(x):
    """判断 pandas/numpy 缺失值（NaN/NaT）"""
    try:
        import pandas as pd
        return bool(pd.isna(x))
    except Exception:
        return x is None


def save_nav_rows(conn, code, rows, source="akshare"):
    """净值行写入缓存表（UPSERT：同日重复抓取以最新为准，天然支持多源重试）"""
    if not rows:
        return 0
    now = dbm.now_str()
    sql = (
        "INSERT INTO fund_nav_daily (fund_code, nav_date, unit_nav_scaled, accum_nav_scaled,"
        " daily_pct_scaled, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(fund_code, nav_date) DO UPDATE SET "
        " unit_nav_scaled = excluded.unit_nav_scaled,"
        " accum_nav_scaled = excluded.accum_nav_scaled,"
        " daily_pct_scaled = excluded.daily_pct_scaled,"
        " source = excluded.source, fetched_at = excluded.fetched_at"
    )
    conn.executemany(
        sql,
        [(code, r["nav_date"], r["unit_nav_scaled"], None, r["daily_pct_scaled"], source, now) for r in rows],
    )
    conn.commit()
    return len(rows)


def refresh_fund_nav(code):
    """拉取并入库一只基金的历史净值；返回本次写入行数"""
    rows = fetch_nav_history(code)
    conn = dbm.connect()
    try:
        n = save_nav_rows(conn, code, rows)
    finally:
        conn.close()
    return n


def latest_nav(conn, code):
    """最近一条净值 → dict（nav_date/unit_nav_scaled/daily_pct_scaled）或 None"""
    row = conn.execute(
        "SELECT nav_date, unit_nav_scaled, daily_pct_scaled FROM fund_nav_daily "
        "WHERE fund_code = ? ORDER BY nav_date DESC LIMIT 1",
        (code,),
    ).fetchone()
    return dict(row) if row else None


def market_value_cents(shares_scaled, nav_scaled):
    """市值（分）= 份额 × 单位净值（Decimal 精确计算）"""
    s = cv.shares_from_scaled(shares_scaled)     # 份额（元精度 4 位）
    n = cv.nav_from_scaled(nav_scaled)           # 净值（元精度 4 位）
    return int((s * n * cv.SCALE_MONEY).to_integral_value(rounding="ROUND_HALF_UP"))


def profit_cents(shares_scaled, nav_scaled, cost_cents):
    """持仓收益（分）= 市值 − 本金"""
    return market_value_cents(shares_scaled, nav_scaled) - int(cost_cents)


def today_profit_cents(shares_scaled, today_nav_scaled, prev_nav_scaled):
    """今日收益（分）= 份额 ×（今日净值 − 昨日净值）"""
    s = cv.shares_from_scaled(shares_scaled)
    d = cv.nav_from_scaled(today_nav_scaled) - cv.nav_from_scaled(prev_nav_scaled)
    return int((s * d * cv.SCALE_MONEY).to_integral_value(rounding="ROUND_HALF_UP"))
