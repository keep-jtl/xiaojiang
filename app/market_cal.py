# -*- coding: utf-8 -*-
"""统一交易日历与交易时段服务（2026-09-05 用户拍板，全项目唯一实现）

- 首页、盯盘提醒及以后所有页面共用这一份"某市场此刻是交易中/未开盘/已收盘/休市"
  的判断逻辑，禁止各页面另写一份（用户需求 8）
- 判断依据 = 各交易所实际开市日日历（本地缓存，离线可用）
- 口径红线（用户需求 1/5，2026-09-05 确认）：只以官方已公布的数据为准，
  绝不按往年惯例猜测（如 A股 2027 官方未公布 → 不内置、不预估；
  查询到未公布年份 → 显式 no_data 报错，绝不显示假状态）
- 界面四态（交易中/未开盘/已收盘/休市）+ 内部五态（多一个 noon_break 午间休市，
  界面并入"休市"+小字说明）；no_data=日历缺失显式报错
- 美股夏令时/冬令时（EDT/EST）自实现规则，与 zoneinfo 边界实测一致（2026-09-05）
- 数据源：cn/hk 内置官方已确认年份 + 联网自动更新（app/sources/calendar_source.py）；
  us 按纽交所规则本地生成（规则固定无调休变量，与 XNYS 逐日交叉验证一致）

模块内纯函数无副作用（便于单测）；SQLite 读写集中在 ensure/查询函数。
"""
import datetime

from dateutil.easter import easter  # 复活节（美股 Good Friday），akshare 依赖已装

from . import db as dbm

# ---------------------------------------------------------------- 市场时段常量
# 集中一处 = 全项目唯一时段定义（cn/hk 北京时间口径；us 美东口径）
OPEN_AT = {"cn": "09:30", "hk": "09:30", "us": "09:30"}
LUNCH = {"cn": ("11:30", "13:00"), "hk": ("12:00", "13:00"), "us": None}
CLOSE_AT = {"cn": "15:00", "hk": "16:10", "us": "16:00"}  # hk 16:00-16:10 收市竞价视为交易中


# ---------------------------------------------------------------- 美股夏令时
def dst_range(year):
    """返回该年 [EDT起始日, EST起始日)：3 月第二个周日 ~ 11 月第一个周日（不含）为 EDT"""
    def first_sun(y, m):
        d = datetime.date(y, m, 1)
        return d + datetime.timedelta(days=(6 - d.weekday()) % 7)
    return first_sun(year, 3) + datetime.timedelta(days=7), first_sun(year, 11)


def is_dst(d):
    """美东日期 d 是否夏令时（EDT）"""
    s, e = dst_range(d.year)
    return s <= d < e


def ny_from_beijing(now):
    """北京时间 → 美东时间（两步修正法：先按北京日期猜偏移，再按美东日期校正。
    与 zoneinfo('America/New_York') 在边界时刻实测一致，2026-09-05）"""
    off1 = datetime.timedelta(hours=-12 if is_dst(now.date()) else -13)
    ny = now + off1
    if is_dst(now.date()) == is_dst(ny.date()):
        return ny
    off2 = datetime.timedelta(hours=-12 if is_dst(ny.date()) else -13)
    return now + off2


def bj_from_ny(ny):
    """美东时间 → 北京时间（反向换算，用于界面显示"北京时间 HH:MM"）"""
    return ny + datetime.timedelta(hours=12 if is_dst(ny.date()) else 13)


# ---------------------------------------------------------------- 时段引擎（纯函数）
def session(market, now_bj, is_open, early_close=None):
    """纯函数：判断某市场此刻状态。输入输出见 docstring 顶部。

    参数：
      market: 'cn'|'hk'|'us'
      now_bj: datetime，引擎约定的"现在"（北京时间；不依赖系统时区设置）
      is_open: bool，该市场本地日期是否交易日（调用方查表；查不到由调用方先判 no_data）
      early_close: 'HH:MM'|None，该日提前收盘时刻（半天交易日）
    返回：
      (state, market_date, open_at, close_at)
      state: 'trading'|'preopen'|'closed'|'holiday'|'noon_break'
      market_date: 'YYYY-MM-DD' 判定依据的市场本地日期（us=美东日期）
      open_at/close_at: 'HH:MM' 市场本地口径（us=美东，展示层再换算北京时间）
    """
    if market in ("cn", "hk"):
        md = now_bj.date()
        t = now_bj.time()
    else:
        ny = ny_from_beijing(now_bj)
        md = ny.date()
        t = ny.time()
    if not is_open:
        return "holiday", md.isoformat(), OPEN_AT[market], early_close or CLOSE_AT[market]
    tt = t.strftime("%H:%M")
    close = early_close or CLOSE_AT[market]
    if tt < OPEN_AT[market]:
        return "preopen", md.isoformat(), OPEN_AT[market], close
    lunch = LUNCH[market]
    # 半天交易日（早收盘 ≤ 午休开始，如港股半天 12:00 收盘）→ 无午休（Pro 2026-09-05 实测修复点）
    if lunch and early_close and early_close <= lunch[0]:
        lunch = None
    if lunch and lunch[0] <= tt < lunch[1]:
        return "noon_break", md.isoformat(), OPEN_AT[market], close
    if tt < close:
        return "trading", md.isoformat(), OPEN_AT[market], close
    return "closed", md.isoformat(), OPEN_AT[market], close


def is_trading_day(conn, market, local_date):
    """查询 (market, local_date) 是否交易日；None 行=日历缺失返回 None（区分 0）
    调用方必须区分：None=日历缺失（no_data），0=确认为休市日"""
    row = conn.execute(
        "SELECT is_open, early_close FROM market_calendar WHERE market=? AND trade_date=?",
        (market, local_date.isoformat()),
    ).fetchone()
    if row is None:
        return None
    return bool(row["is_open"]), row["early_close"]


def next_trade_date(conn, market, after_date):
    """给定日期之后（不含当天）的第一个交易日（已确认日历内）；无则 None"""
    row = conn.execute(
        "SELECT trade_date FROM market_calendar WHERE market=? AND trade_date>? AND is_open=1 "
        "ORDER BY trade_date LIMIT 1",
        (market, after_date.isoformat()),
    ).fetchone()
    return datetime.date.fromisoformat(row["trade_date"]) if row else None


# ---------------------------------------------------------------- 内置已确认日历（2026-09-05 实测）
# 只存"官方已确认"年份（用户需求：绝不按往年规律猜测）：
#   cn：2026 全量（新浪官方源实测 + 与国务院/交易所 2026 安排核对一致）；
#       2027 官方 2026 年 11~12 月才公布 → 未确认不内置，公布后联网自动更新
#   hk：2026/2027（香港政府 1823 ICS 官方 + 港交所通告 CE_SEHK_CT_077_2026 交叉一致）
#   us：不预置常量——统一走下方 _us_holidays_rule 本地规则生成（NYSE 规则固定无
#       调休变量，已与 exchange_calendars XNYS 逐日比对完全一致，不属于"猜测"；
#       联网时每年对照 NYSE 官方表核对），任意年份均可离线生成，无需联网
# 集合为"非周末休市日"（MM-DD），周末休市由展开函数按 weekday 判断。
BUILTIN_HOLIDAYS = {
    ("cn", 2026): {"01-01", "01-02", "02-16", "02-17", "02-18", "02-19", "02-20", "02-23",
                   "04-06", "05-01", "05-04", "05-05", "06-19", "09-25",
                   "10-01", "10-02", "10-05", "10-06", "10-07"},
    ("hk", 2026): {"01-01", "02-17", "02-18", "02-19", "04-03", "04-06", "04-07",
                   "05-01", "05-25", "06-19", "07-01", "10-01", "10-19", "12-25"},
    ("hk", 2027): {"01-01", "02-08", "02-09", "03-26", "03-29", "04-05", "05-13",
                   "06-09", "07-01", "09-16", "10-01", "10-08", "12-27"},
}
# 半天交易日（提前收盘）——cn 无半天；hk 官方公告口径（1823 ICS，半天 12:00 收盘）；
# us 黑五半天由规则生成（感恩节后一天 13:00 美东），不在此手写，防双源漂移
BUILTIN_EARLY_CLOSE = {
    ("hk", 2026): {"02-16": "12:00", "12-24": "12:00", "12-31": "12:00"},
    ("hk", 2027): {"02-05": "12:00", "12-24": "12:00", "12-31": "12:00"},
}


def nth_of_month(y, m, n, wd):
    """第 n 个星期 wd（wd: 0=周一 … 6=周日）"""
    d = datetime.date(y, m, 1)
    while d.weekday() != wd:
        d += datetime.timedelta(days=1)
    return d + datetime.timedelta(weeks=n - 1)


def last_of_month(y, m, wd):
    """该月最后一个星期 wd"""
    d = datetime.date(y, m + 1, 1) - datetime.timedelta(days=1)
    while d.weekday() != wd:
        d -= datetime.timedelta(days=1)
    return d


def _us_holidays_rule(year):
    """NYSE 规则生成该年休市日（含观察日、Good Friday），返回非周末休市日 MM-DD 集合。
    规则：1/1 元旦、1 月第三个周一 MLK、2 月第三个周一 Presidents、复活节前周五
    Good Friday、5 月最后一个周一 Memorial、6/19 六月节、7/4 独立日、9 月第一个周一
    Labor、11 月第四个周四 Thanksgiving、12/25 圣诞；
    固定假日（元旦/六月节/独立日/圣诞）逢周六→周五休、逢周日→周一休（观察日）；
    移动假日恒为周一/周四/周五，无观察日问题。"""
    def obs(x):                   # 观察日：周六提前到周五、周日顺延到周一
        if x.weekday() == 5:
            return x - datetime.timedelta(days=1)
        if x.weekday() == 6:
            return x + datetime.timedelta(days=1)
        return x

    fixed = [datetime.date(year, 1, 1), datetime.date(year, 6, 19),
             datetime.date(year, 7, 4), datetime.date(year, 12, 25)]
    movable = [nth_of_month(year, 1, 3, 0), nth_of_month(year, 2, 3, 0),
               easter(year) - datetime.timedelta(days=2), last_of_month(year, 5, 0),
               nth_of_month(year, 9, 1, 0), nth_of_month(year, 11, 4, 3)]
    return {obs(d).strftime("%m-%d") for d in fixed + movable}


def _expand_year(market, year):
    """某市场某年展开为全量行 [{"trade_date", "is_open", "early_close"}]
    us 永远走 NYSE 规则生成（任何年份都可离线算）；
    cn/hk 查内置官方常量；**官方未确认的年份返回 []（一行不生成）**——
    库内查不到行=日历缺失，引擎显式 no_data，绝不把"工作日"当交易日（用户口径红线）。"""
    if market == "us":
        holidays = _us_holidays_rule(year)
        black_friday = nth_of_month(year, 11, 4, 3) + datetime.timedelta(days=1)
        early = {black_friday.strftime("%m-%d"): "13:00"}
    elif (market, year) not in BUILTIN_HOLIDAYS and (market, year) not in BUILTIN_EARLY_CLOSE:
        return []  # 官方未确认年份：不猜测
    else:
        holidays = BUILTIN_HOLIDAYS.get((market, year), set())
        early = BUILTIN_EARLY_CLOSE.get((market, year), {})
    rows = []
    d = datetime.date(year, 1, 1)
    end = datetime.date(year, 12, 31)
    while d <= end:
        md = d.strftime("%m-%d")
        if d.weekday() >= 5 or md in holidays:
            rows.append({"trade_date": d.isoformat(), "is_open": 0, "early_close": None})
        elif md in early:
            rows.append({"trade_date": d.isoformat(), "is_open": 1, "early_close": early[md]})
        else:
            rows.append({"trade_date": d.isoformat(), "is_open": 1, "early_close": None})
        d += datetime.timedelta(days=1)
    return rows


def _year_complete(conn, market, year):
    """该市场该年是否已整年入库（365/366 行全在）"""
    import calendar
    expect = 366 if calendar.isleap(year) else 365
    cnt = conn.execute(
        "SELECT COUNT(*) c FROM market_calendar WHERE market=? AND trade_date BETWEEN ? AND ?",
        (market, f"{year}-01-01", f"{year}-12-31"),
    ).fetchone()["c"]
    return cnt == expect


def _upsert_rows(conn, market, rows, source):
    now = dbm.now_str()
    for r in rows:
        conn.execute(
            "INSERT INTO market_calendar (market, trade_date, is_open, early_close, source, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(market, trade_date) DO UPDATE SET is_open=excluded.is_open, "
            "early_close=excluded.early_close, source=excluded.source, fetched_at=excluded.fetched_at",
            (market, r["trade_date"], r["is_open"], r["early_close"], source, now),
        )


# ---------------------------------------------------------------- 统一入口
def ensure_calendar(conn, markets=("cn", "hk", "us"), allow_network=True):
    """幂等单函数：保证三市场日历覆盖"当年 + 官方可确认的下一年"。

    - cn/hk：库内无该市场 → 导入内置官方已确认年份（cn 2026 / hk 2026-27）；
      官方惯例在 11 月后公布次年 → 每市场每年 11 月 1 日起才联网检查次年
      （app_meta.calendar_check_{m} 当日占位防抖，与净值刷新同机制），拉不到就
      静默失败、旧数据原样保留 —— 绝不回退成"工作日即交易日"，绝不预猜未公布年份
    - us：NYSE 规则可离线生成任意年份 → 直接从缺口年份补到"当年+次年"，
      不联网、永远覆盖完整
    返回 {"imported": [市场...], "updated": {市场: 说明}, "detail": str}
    """
    today = datetime.date.today()
    result = {"imported": [], "updated": {}, "detail": ""}
    for m in markets:
        mx = conn.execute("SELECT MAX(trade_date) d FROM market_calendar WHERE market=?",
                          (m,)).fetchone()["d"]
        db_max_year = int(mx[:4]) if mx else 0
        if m == "us":
            # 规则生成（幂等）：逐"当年+次年"检查整年是否完整，缺失的年份本地补齐，
            # 不联网；已在库的年份绝不重复写（跨年后也只会补当年）
            filled = []
            for y in range(today.year, today.year + 2):
                if not _year_complete(conn, m, y):
                    _upsert_rows(conn, m, _expand_year(m, y), "rule_nyse")
                    filled.append(y)
            result["updated"][m] = f"rule:{filled}" if filled else "ok"
        else:
            if db_max_year == 0:  # 首次启动：导入内置官方已确认年份
                years = sorted({k[1] for k in BUILTIN_HOLIDAYS if k[0] == m})
                for y in years:
                    _upsert_rows(conn, m, _expand_year(m, y), "builtin")
                result["imported"].append(m)
                db_max_year = max(years)
                result["detail"] += f"{m}:内置{years};"
            # 联网补次年（官方惯例 11 月后公布）：
            #   当年数据缺失（库内最大年 < 今年）→ 任何时候都拉（首次启动/长期离线后）
            #   当年齐全、只差次年            → 11 月进入官方公布期后才拉
            in_publish_season = today >= datetime.date(today.year, 11, 1)
            missing_current = db_max_year < today.year
            if db_max_year < today.year + 1 and allow_network \
                    and (missing_current or in_publish_season):
                result["updated"][m] = _network_update(conn, m)
            else:
                result["updated"][m] = "ok_noop"
    conn.commit()
    return result


def _network_update(conn, market):
    """联网拉官方最新日历（cn=新浪 / hk=1823 ICS）；每天最多试一次（占位防抖）；
    失败返回原因，旧数据原样保留。"""
    today = dbm.today_str()
    if dbm.get_app_meta(conn, f"calendar_check_{market}") == today:
        return "skipped_today"
    dbm.set_app_meta(conn, f"calendar_check_{market}", today)  # 先占位，失败次日再试
    try:
        mx = conn.execute("SELECT MAX(trade_date) d FROM market_calendar WHERE market=?",
                          (market,)).fetchone()["d"]
        start_year = (int(mx[:4]) + 1) if mx else datetime.date.today().year
        from .sources.calendar_source import fetch_official
        rows = fetch_official(market, start_year)
        if not rows:
            return "source_empty"  # 官方尚未公布新年度（如 2026-11 前查 2027）
        _upsert_rows(conn, market, rows, "sina" if market == "cn" else "ics1823")
        return f"fetched:{len(rows)}"
    except Exception as e:
        print(f"[market_cal] {market} 日历更新失败（旧数据保留）: {e}")
        return f"fail:{type(e).__name__}"
