# -*- coding: utf-8 -*-
"""美国劳工统计局（BLS）官方 RSS 适配器。

https://www.bls.gov/feed/bls_latest.rss
- 官方免费 RSS，带真实发布时间
- BLS 对默认 Python/Chrome UA 返回 403；实测应用 UA 可正常返回 200
- 只保留最近 7 天；HTML 摘要清理为纯文本后再入库/翻译
"""
import datetime as dt
import email.utils
import html
import re
import urllib.request
import xml.etree.ElementTree as ET

FEED_URL = "https://www.bls.gov/feed/bls_latest.rss"
USER_AGENT = "XiaoLiCaiTong/0.1 (Android personal app)"
LOOKBACK_DAYS = 7
MAX_ITEMS = 30
MAX_SUMMARY_CHARS = 1200


def _fetch_xml():
    req = urllib.request.Request(
        FEED_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _plain(text):
    s = html.unescape(str(text or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > MAX_SUMMARY_CHARS:
        s = s[:MAX_SUMMARY_CHARS].rstrip() + "…"
    return s


def fetch(days=LOOKBACK_DAYS):
    """返回 (items, errors)"""
    try:
        root = ET.fromstring(_fetch_xml())
    except Exception as e:
        return [], [f"BLS RSS 获取失败：{e}"]

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    items = []
    for node in root.findall(".//item"):
        title = _plain(node.findtext("title"))
        link = (node.findtext("link") or "").strip()
        pub = (node.findtext("pubDate") or "").strip()
        desc = _plain(node.findtext("description"))
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
        local = d.astimezone()
        items.append({
            "source": "bls_rss",
            "source_id": (link or title) + "#" + str(int(local.timestamp())),
            "title": title,
            "summary": desc,
            "url": link,
            "published_at": local.strftime("%Y-%m-%d %H:%M:%S"),
            "published_ts": int(local.timestamp()),
            "importance": 0.8,
            "category": "economic_data",
            "raw_json": None,
        })
        if len(items) >= MAX_ITEMS:
            break
    return items, []
