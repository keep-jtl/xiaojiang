# -*- coding: utf-8 -*-
"""SEC EDGAR 适配器（阶段一：美股公司公告/财报）

官方数据：https://data.sec.gov/submissions/CIK##########.json
- 只读公开 JSON，不抓网页、不绕验证
- SEC 要求 User-Agent 标明应用与联系方式；长期使用请把示例邮箱换成自己的
- 同一公司的提交记录在进程内缓存 2 小时，避免每 30 分钟重复下载大文件
"""
import datetime as dt
import json
import os
import re
import time
import urllib.request

# SEC 要求 User-Agent 可识别应用并带联系方式。联系邮箱不写死在源码里：
# 从 Windows 用户环境变量 XLTC_SEC_CONTACT_EMAIL 读取；未配置/格式无效时跳过 SEC 抓取。
APP_NAME = "XiaoLiCaiTong/0.1"
CONTACT_ENV = "XLTC_SEC_CONTACT_EMAIL"
MISSING_CONTACT_MSG = (
    "未配置有效的 SEC 联系邮箱（Windows 用户环境变量 XLTC_SEC_CONTACT_EMAIL），"
    "已跳过 SEC 抓取；旧新闻保留。"
)
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


def contact_email():
    """读取环境变量里的 SEC 联系邮箱；未配置或格式明显无效 → None"""
    value = (os.environ.get(CONTACT_ENV) or "").strip()
    if not value:
        return None
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        return None
    return value


def user_agent():
    """有效邮箱 → 完整 UA；否则 None（调用方据此跳过 SEC，不发送假联系方式）"""
    email = contact_email()
    if not email:
        return None
    return f"{APP_NAME} (personal local app; contact: {email})"

# 只保留有信息量的表单；Form 4（内部人交易）等低价值表单默认过滤
FORMS = {"10-K", "10-Q", "8-K", "6-K"}
FORM_LABEL = {"10-K": "年报", "10-Q": "季报", "8-K": "重大事件公告", "6-K": "境外公司公告"}
FORM_IMPORTANCE = {"10-K": 1.0, "10-Q": 0.9, "8-K": 0.85, "6-K": 0.75}
# 8-K 的 item 代码含义（只取第一个做标题补充）
ITEM_LABEL = {
    "2.02": "公布业绩",
    "1.01": "签订重大协议",
    "5.02": "高管变动",
    "8.01": "其他重大事件",
    "9.01": "财务报表附件",
}

LOOKBACK_DAYS = 7
MAX_PER_COMPANY = 6
_CACHE_SECONDS = 2 * 3600
_TICKER_CACHE = {"ts": 0.0, "map": None}
_SUB_CACHE = {}  # cik -> (ts, data)


def _get_json(url, ua):
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def load_ticker_map(ua, force=False):
    """ticker → {cik, title}；进程内缓存 24 小时"""
    now = time.time()
    if not force and _TICKER_CACHE["map"] and now - _TICKER_CACHE["ts"] < 24 * 3600:
        return _TICKER_CACHE["map"]
    data = _get_json(TICKER_URL, ua)
    m = {}
    for row in data.values():
        ticker = str(row.get("ticker") or "").upper().strip()
        if ticker:
            m[ticker] = {"cik": int(row["cik_str"]), "title": row.get("title") or ticker}
    _TICKER_CACHE.update(ts=now, map=m)
    return m


def _submissions(cik, ua):
    now = time.time()
    cached = _SUB_CACHE.get(cik)
    if cached and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    data = _get_json(SUBMISSIONS_URL.format(cik=cik), ua)
    _SUB_CACHE[cik] = (now, data)
    return data


def _local_time(s):
    """SEC acceptanceDateTime（UTC ISO）→ 本地时间字符串 + epoch 秒"""
    if not s:
        return None, None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        local = d.astimezone()
        return local.strftime("%Y-%m-%d %H:%M:%S"), int(local.timestamp())
    except Exception:
        return None, None


def fetch_company(ticker, days=LOOKBACK_DAYS, ua=None):
    """取一家公司最近 N 天的 10-K/10-Q/8-K/6-K；返回 (items, error)"""
    ua = ua or user_agent()
    if not ua:
        return [], MISSING_CONTACT_MSG
    ticker = str(ticker or "").upper().strip()
    if not ticker:
        return [], "空 ticker"
    try:
        info = load_ticker_map(ua).get(ticker)
    except Exception as e:
        return [], f"SEC ticker 表获取失败：{e}"
    if not info:
        return [], f"SEC 未找到 {ticker} 的 CIK"

    cik, company = info["cik"], info["title"]
    try:
        data = _submissions(cik, ua)
    except Exception as e:
        return [], f"{ticker} 提交记录获取失败：{e}"

    recent = (data.get("filings") or {}).get("recent") or {}
    n = len(recent.get("form") or [])
    if not n:
        return [], f"{ticker} 暂无提交记录"

    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    items = []
    for i in range(n):
        form = recent["form"][i]
        if form not in FORMS:
            continue
        filing_date = (recent.get("filingDate") or [None] * n)[i]
        if not filing_date or filing_date < cutoff:
            continue
        acc = (recent.get("accessionNumber") or [None] * n)[i]
        if not acc:
            continue

        pub_at, pub_ts = _local_time((recent.get("acceptanceDateTime") or [None] * n)[i])
        if not pub_at:
            pub_at = f"{filing_date} 00:00:00"
            try:
                pub_ts = int(dt.datetime.strptime(filing_date, "%Y-%m-%d").timestamp())
            except Exception:
                pub_ts = None

        doc = (recent.get("primaryDocument") or [None] * n)[i]
        desc = (recent.get("primaryDocDescription") or [None] * n)[i] or ""
        acc_nodash = str(acc).replace("-", "")
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"
               if doc else f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/")

        items_code = (recent.get("items") or [None] * n)[i] or ""
        first_item = str(items_code).split(",")[0].strip()
        extra = ITEM_LABEL.get(first_item, "")
        title = f"{company}（{ticker}）{FORM_LABEL.get(form, form)}"
        if extra:
            title += f"：{extra}"

        importance = FORM_IMPORTANCE.get(form, 0.6)
        if form == "8-K":
            if first_item == "2.02":
                importance = 1.0
            elif first_item == "5.02":
                importance = 0.95
            elif first_item == "1.01":
                importance = 0.9

        items.append({
            "source": "sec_edgar",
            "source_id": f"{cik}-{acc}",
            "title": title,
            "summary": f"{FORM_LABEL.get(form, form)} · 报告期 {(recent.get('reportDate') or [None] * n)[i] or '—'}"
                       + (f" · {desc}" if desc and desc.upper() != form else ""),
            "url": url,
            "published_at": pub_at,
            "published_ts": pub_ts,
            "importance": round(importance, 2),
            "ticker": ticker,
            "company": company,
            "form": form,
            "raw_json": json.dumps({
                "cik": cik, "ticker": ticker, "company": company, "form": form,
                "filingDate": filing_date, "reportDate": (recent.get("reportDate") or [None] * n)[i],
                "accessionNumber": acc, "items": items_code,
            }, ensure_ascii=False),
        })
        if len(items) >= MAX_PER_COMPANY:
            break
    return items, None


def fetch(tickers, days=LOOKBACK_DAYS):
    """tickers: 去重后的 ticker 列表；返回 (items, errors)

    并发抓取（最多 4 个线程），把 20 家公司的首次下载从约 2.5 分钟降到约 40 秒；
    实际请求速率仍远低于 SEC 的 10 请求/秒上限。
    """
    ua = user_agent()
    if not ua:
        return [], [MISSING_CONTACT_MSG]
    unique, seen = [], set()
    for t in tickers or []:
        t = str(t or "").upper().strip()
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    if not unique:
        return [], []
    try:
        load_ticker_map(ua)  # 先加载一次，避免多线程重复下载
    except Exception as e:
        return [], [f"SEC ticker 表获取失败：{e}"]

    from concurrent.futures import ThreadPoolExecutor, as_completed

    items, errors = [], []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch_company, t, days, ua): t for t in unique}
        for fut in as_completed(futs):
            try:
                got, err = fut.result()
            except Exception as e:
                got, err = [], f"{futs[fut]} 抓取异常：{e}"
            items.extend(got)
            if err:
                errors.append(err)
    return items, errors
