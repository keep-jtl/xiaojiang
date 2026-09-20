# -*- coding: utf-8 -*-
"""交易日历联网更新源（2026-09-05 实测通过，与内置常量逐日比对一致）

- cn：新浪财经交易日历 klc_td_sh.txt（交易所口径、官方公布才更新；2026-09-05 实测
  覆盖至 2026 全量，2027 官方公布前该源不含 2027 —— 与用户"不猜未公布年份"口径天然一致）
- hk：香港政府 1823 热线官方 ICS（www.1823.gov.hk，含全部公众假期事件；
  半天交易日不出现在假期事件里，由本模块按港交所通告年表维护）

统一契约 fetch_official(market, start_year)：
  返回 start_year 起已公布的每个年份的「全量行」（含休市日与早收盘），供
  market_cal.ensure_calendar 入库。两源均直连并显式绕系统代理
  （本机 127.0.0.1:7890 Clash 代理会让 requests 间歇性失败，2026-09-05 实测）。

注：本模块只在官方公布期（每年 11 月后）被 ensure_calendar 调用，平时不联网。
"""
import datetime
import re
import urllib.request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕系统代理（见 docstring）

# 港股半天交易日（12:00 收盘）年表 —— 来源 = 港交所通告（CE_SEHK_CT_077_2026 等），
# ICS 只含全天假期。新增年份的半天日需按港交所当年通告人工补录（联网只更新休市日）。
HK_EARLY_CLOSE = {
    2026: {"02-16", "12-24", "12-31"},
    2027: {"02-05", "12-24", "12-31"},
}


def http_get(url, timeout=25):
    """直连抓取（绕系统代理），自动尝试 utf-8/gbk"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with OPENER.open(req, timeout=timeout) as r:
        raw = r.read()
        for enc in ("utf-8", "gbk"):
            try:
                return r.status, raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return r.status, raw.decode("utf-8", errors="replace")


def _expand_trade_days(market, trade_days, years):
    """交易日集合 → 该市场整年全量行 [{"trade_date","is_open","early_close"}]
    周末=休市；不在交易日集合的工作日=官方休市（春节/国庆等）。
    hk 按 HK_EARLY_CLOSE 年表补半天日。"""
    rows = []
    for y in years:
        early = HK_EARLY_CLOSE.get(y, set()) if market == "hk" else set()
        d = datetime.date(y, 1, 1)
        while d <= datetime.date(y, 12, 31):
            md = d.strftime("%m-%d")
            if d.weekday() >= 5 or d.isoformat() not in trade_days:
                rows.append({"trade_date": d.isoformat(), "is_open": 0, "early_close": None})
            elif md in early:
                rows.append({"trade_date": d.isoformat(), "is_open": 1, "early_close": "12:00"})
            else:
                rows.append({"trade_date": d.isoformat(), "is_open": 1, "early_close": None})
            d += datetime.timedelta(days=1)
    return rows


def fetch_cn(start_year):
    """新浪交易日历：JS 混淆文本 → py_mini_racer 解码（照 akshare 同源实现）
    返回 start_year 起所有已公布年份的全量行；官方未公布的年份自然无数据。"""
    from akshare.stock.cons import hk_js_decode  # 解码函数（akshare 已装，纯本地）
    import py_mini_racer
    _, txt = http_get("https://finance.sina.com.cn/realstock/company/klc_td_sh.txt")
    payload = txt.split("=")[1].split(";")[0].replace('"', "")
    js = py_mini_racer.MiniRacer()
    js.eval(hk_js_decode)
    dates = js.call("d", payload)  # ['1990-12-19T00:00:00.000Z', ...]
    trade_days = {str(x)[:10] for x in dates}
    years = sorted({int(x[:4]) for x in trade_days if int(x[:4]) >= start_year})
    return _expand_trade_days("cn", trade_days, years)


def fetch_hk(start_year):
    """香港政府 1823 官方 ICS：公众假期事件 → 非周末的=休市日 → 展开全量行"""
    _, txt = http_get("https://www.1823.gov.hk/common/ical/tc.ics")
    evs = set()
    for block in txt.split("BEGIN:VEVENT")[1:]:
        m1 = re.search(r"DTSTART;VALUE=DATE:(\d{8})", block)
        if m1:
            s = m1.group(1)
            evs.add(f"{s[:4]}-{s[4:6]}-{s[6:]}")
    years = sorted({int(x[:4]) for x in evs if int(x[:4]) >= start_year})
    # ICS 给的休市日就是"公众假期且落在工作日"的日子，其余工作日=交易日
    trade_days = set()
    for y in years:
        d = datetime.date(y, 1, 1)
        while d <= datetime.date(y, 12, 31):
            if d.weekday() < 5 and d.isoformat() not in evs:
                trade_days.add(d.isoformat())
            d += datetime.timedelta(days=1)
    return _expand_trade_days("hk", trade_days, years)


def fetch_official(market, start_year):
    """统一入口：cn=新浪官方 / hk=1823 官方；返回全量行（可能为空=官方尚未公布新年度）"""
    if market == "cn":
        return fetch_cn(start_year)
    if market == "hk":
        return fetch_hk(start_year)
    raise ValueError(f"未知市场 {market}（us 无需联网，规则本地生成）")
