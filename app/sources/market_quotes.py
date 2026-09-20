# -*- coding: utf-8 -*-
"""行情适配层——首页市场速览/盯盘提醒共用（2026-09-05 实测定源）

实测结论（diag_quotes.py，2026-09-05 两轮+跨源核对）：
- 腾讯 qt.gtimg.cn = 主源：8 指数全通，字段统一（f1 名称 f3 最新 f4 昨收
  f30 时间 f32 涨跌幅）；时间口径 A股=北京本地、港股=香港本地（与北京同区）、
  美股=美东时间 → 入库前统一换算成北京时间
- 新浪 hq.sinajs.cn = 备用：A股指数与美股（gb_ 前缀）可用；
  港股 rt_hk 字段结构不同、解析不稳定（2026-09-05 实测记录）→ 不做备用
- 东财（akshare 或 push2 直连）：本机受系统代理 127.0.0.1:7890（Clash 类）
  间歇性 RemoteDisconnected（实测两轮全挂）→ 弃用；基金净值源不受影响
- 所有请求显式绕系统代理（ProxyHandler({})），避免代理抖动误判数据源

数据源抽象层约定：本文件是"市场行情"的唯一适配器，换源/加源只改这里；
入库数值走 converters 缩放整数（点位/昨收 ×10000、涨跌幅 ×10000 带符号）。
"""
import datetime
import urllib.request

from .. import converters as cv
from .. import db as dbm
from .. import market_cal as mcal

# 内部码 → 数据源代码映射（market_meta.code 是内部标识，此处为唯一转换点）
TENCENT_MAP = {
    "sh000300": "sh000300", "sh000001": "sh000001", "sh000905": "sh000905",
    "sz399006": "sz399006", "hkHSI": "hkHSI", "hkHSTECH": "hkHSTECH",
    "usSPX": "usINX", "usNDX": "usNDX", "usDJI": "usDJI",
}
SINA_MAP = {
    "sh000300": "sh000300", "sh000001": "sh000001", "sh000905": "sh000905",
    "sz399006": "sz399006",
    "usSPX": "gb_inx", "usNDX": "gb_ndx", "usDJI": "gb_$dji",  # hk 不做备用（见模块注释）
}
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕系统代理


def _http_get(url, headers=None, timeout=12):
    h = dict(UA)
    if headers:
        h.update(headers)
    return OPENER.open(urllib.request.Request(url, headers=h), timeout=timeout).read()


def _tx_time_to_bj(f30):
    """腾讯 f30 → 北京时间 'YYYY-MM-DD HH:MM:SS'
    A股/港股 = 本地时间（原文即可，与北京同区）；
    美股 = 美东时间 → 换算北京时间（2026-09-05 实测：美股 f30 为 '2026-09-04 16:33:06' 横线格式）"""
    if not f30:
        return None
    f30 = f30.strip()
    try:
        if len(f30) == 14 and f30.isdigit():  # A股 '20260904161415'
            return f"{f30[0:4]}-{f30[4:6]}-{f30[6:8]} {f30[8:10]}:{f30[10:12]}:{f30[12:14]}"
        if "/" in f30:  # 港股 '2026/09/04 18:31:01'（本地）
            return f"{f30[0:4]}-{f30[5:7]}-{f30[8:10]} {f30[11:]}"
        dt = datetime.datetime.strptime(f30, "%Y-%m-%d %H:%M:%S")  # 美股（美东）
        return mcal.bj_from_ny(dt).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def fetch_tencent(codes):
    """腾讯主源批量拉取。返回 {内部码: {"price","prev","pct","qtime"}}（字符串原文，缩放入库时做）"""
    out = {}
    q = ",".join(TENCENT_MAP[c] for c in codes if c in TENCENT_MAP)
    if not q:
        return out
    try:
        raw = _http_get(f"https://qt.gtimg.cn/q={q}")
        text = raw.decode("gbk", errors="replace")
    except Exception as e:
        print(f"[market_quotes] 腾讯批量失败: {e}")
        return out
    rev = {v: k for k, v in TENCENT_MAP.items()}
    for line in text.strip().splitlines():
        if not line.startswith("v_") or '="' not in line:
            continue  # 首行 v_pv_none_match 等非行情行
        qcode = line[2:].split("=")[0]
        code = rev.get(qcode)
        if code is None:
            continue
        f = line.split('="', 1)[1].split('"', 1)[0].split("~")
        try:
            if len(f) < 33 or not f[3] or not f[32]:
                continue  # 该码数据异常 → 交给备用源/保留旧值
            price, prev, pct = f[3], f[4], f[32]
            out[code] = {"price": price, "prev": prev, "pct": pct, "qtime": _tx_time_to_bj(f[30])}
        except (IndexError, ValueError):
            continue
    return out


def fetch_sina(codes):
    """新浪备用源（A股指数 + 美股 gb_ 前缀；两种字段结构不同，按前缀分流）。

    A股指数：名称,今开,昨收,最新,最高,最低,...（无时间字段 → qtime=None）
    美股 gb_：名称,最新,涨跌幅%,时间(北京时间),涨跌额,昨收,...（qtime 直接用）
    注：实测道指新浪返回空串（gb_$dji）→ 该码无备用，腾讯失败时由界面显示"暂不可用"。
    """
    from decimal import Decimal, InvalidOperation
    out = {}
    q = ",".join(SINA_MAP[c] for c in codes if c in SINA_MAP)
    if not q:
        return out
    try:
        raw = _http_get(f"https://hq.sinajs.cn/list={q}")
        text = raw.decode("gbk", errors="replace")
    except Exception as e:
        print(f"[market_quotes] 新浪备用失败: {e}")
        return out
    rev = {v: k for k, v in SINA_MAP.items()}
    for line in text.strip().splitlines():
        if "hq_str_" not in line or '="' not in line:
            continue
        qcode = line.split("hq_str_", 1)[1].split("=")[0]
        code = rev.get(qcode)
        if code is None:
            continue
        body = line.split('="', 1)[1].rstrip('";')
        if not body:
            continue  # 该码源无数据（如道指空串）
        f = body.split(",")
        try:
            if qcode.startswith("gb_"):   # 美股
                if len(f) < 6:
                    continue
                price, pct, qtime, prev = f[1], f[2], f[3], f[5]
            else:                          # A股指数
                if len(f) < 5:
                    continue
                price, prev, pct, qtime = f[3], f[2], None, None
            if not price or not prev or Decimal(prev) == 0:
                continue
            price = format(Decimal(price).quantize(Decimal("0.01")), "f")  # 源给 4 位 → 截 2 位
            if pct is None:
                pct = format((Decimal(price) / Decimal(prev) - 1) * 100, ".2f")
            else:
                pct = format(Decimal(pct).quantize(Decimal("0.01")), "f")
            out[code] = {"price": price, "prev": format(Decimal(prev).quantize(Decimal("0.01")), "f"),
                         "pct": pct, "qtime": qtime}
        except (IndexError, ValueError, InvalidOperation):
            continue
    return out


def refresh_market_quotes(conn, codes):
    """刷新一批行情并入库（腾讯主源 → 新浪备用 → UPSERT）。

    返回 {"ok": [...], "stale": {code: 原因}}——stale 代码本次没拿到可用值，
    库内旧行原样保留（绝不覆盖成空），由界面显示"暂不可用/最近一次"。
    """
    now = dbm.now_str()
    vals = fetch_tencent(codes)
    missing = [c for c in codes if c not in vals]
    if missing:
        for c, v in fetch_sina(missing).items():
            vals[c] = v
    ok, stale = [], {}
    for c in codes:
        v = vals.get(c)
        if not v:
            stale[c] = "网络失败" if c in missing else "数据异常"
            continue
        try:  # 源给 2 位小数，×10000 容差足够；异常值拒收不覆盖
            price_s = cv.point_to_scaled(v["price"])
            prev_s = cv.point_to_scaled(v["prev"])
            pct_s = cv.pct_to_scaled(v["pct"])
        except ValueError as e:
            print(f"[market_quotes] {c} 数值异常拒收: {e}")
            stale[c] = "数据异常"
            continue
        src = "tencent" if c not in missing else "sina"
        conn.execute(
            "INSERT INTO market_quotes (mkt_code, price_scaled, change_pct_scaled, prev_close_scaled,"
            " quote_time, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(mkt_code) DO UPDATE SET price_scaled=excluded.price_scaled,"
            " change_pct_scaled=excluded.change_pct_scaled, prev_close_scaled=excluded.prev_close_scaled,"
            " quote_time=excluded.quote_time, source=excluded.source, fetched_at=excluded.fetched_at",
            (c, price_s, pct_s, prev_s, v["qtime"], src, now),
        )
        ok.append(c)
    conn.commit()
    return {"ok": ok, "stale": stale}
