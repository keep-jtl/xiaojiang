# -*- coding: utf-8 -*-
"""美联储新闻稿 RSS 适配器（官方）

https://www.federalreserve.gov/feeds/press_all.xml
- 官方免费 RSS，带真实发布时间
- 银行执法类（enforcement）噪音大，阶段一直接过滤
- 只保留最近 7 天
"""
import datetime as dt
import email.utils
import urllib.request
import xml.etree.ElementTree as ET

FEED_URL = "https://www.federalreserve.gov/feeds/press_all.xml"
# 美联储 RSS 是公开订阅源；实测无需联系邮箱，用普通应用 UA 即可。
USER_AGENT = "XiaoLiCaiTong/0.1 (personal local app)"
LOOKBACK_DAYS = 7
MAX_ITEMS = 30

CATEGORY_LABEL = {
    "monetary_policy": "货币政策",
    "regulation": "监管政策",
    "speech": "官员讲话",
    "other": "其他新闻",
}


def _fetch_xml():
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _classify(title, link):
    """标题/链接 → (category, importance)；执法类返回 None（过滤）"""
    t = (title or "").lower()
    u = (link or "").lower()
    if "enforcement" in t or "enforcement" in u:
        return None, 0.0
    if any(k in t or k in u for k in (
        "monetary policy", "fomc", "federal funds", "interest rate",
        "rate cut", "rate hike", "inflation", "discount rate",
    )):
        return "monetary_policy", 1.0
    if "speech" in t or "remarks" in t or "testimony" in t:
        return "speech", 0.7
    if any(k in t or k in u for k in ("supervision", "regulatory", "regulation", "capital")):
        return "regulation", 0.6
    return "other", 0.4


def fetch(days=LOOKBACK_DAYS):
    """返回 (items, errors)"""
    try:
        root = ET.fromstring(_fetch_xml())
    except Exception as e:
        return [], [f"美联储 RSS 获取失败：{e}"]

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    items = []
    for node in root.findall(".//item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        pub = (node.findtext("pubDate") or "").strip()
        desc = (node.findtext("description") or "").strip()
        if not title or not pub:
            continue
        try:
            d = email.utils.parsedate_to_datetime(pub)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
        except Exception:
            continue
        if d < cutoff:
            continue
        cat, importance = _classify(title, link)
        if cat is None:
            continue
        local = d.astimezone()
        items.append({
            "source": "fed_rss",
            "source_id": link or title,
            "title": title,
            "summary": desc,
            "url": link,
            "published_at": local.strftime("%Y-%m-%d %H:%M:%S"),
            "published_ts": int(local.timestamp()),
            "importance": importance,
            "category": cat,
            "raw_json": None,
        })
        if len(items) >= MAX_ITEMS:
            break
    return items, []
