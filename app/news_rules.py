# -*- coding: utf-8 -*-
"""市场事件分类与保留期限的唯一规则入口。

- realtime：普通动态/政策/一般公告/美联储/非财报 8-K/默认 6-K → 7 天
- report：10-K/10-Q、8-K 2.02、明确财报 6-K → 14 天
- 时间口径统一为 published_at（入库时对应 published_ts），不使用 fetched_at
"""
import json
import re

REALTIME_DAYS = 7
REPORT_DAYS = 14

# 6-K 只有描述中出现“期间 + financial statements”这种明确组合才算财报；
# 仅出现 report / 6-K / REPORT OF FOREIGN PRIVATE ISSUER 等泛化文字不算。
_SIX_K_REPORT_RE = re.compile(
    r"\b(interim|annual|quarterly)\b[^.]{0,80}\bfinancial statements\b",
    re.IGNORECASE,
)


def classify_news_kind(source, form="", items="", doc_description=""):
    """返回 realtime / report；无法可靠判断时一律 realtime。"""
    source = str(source or "").strip().lower()
    form = str(form or "").strip().upper()
    if source == "fed_rss":
        return "realtime"
    if form in ("10-K", "10-Q"):
        return "report"
    if form == "8-K":
        codes = set(re.findall(r"\b\d+\.\d{2}\b", str(items or "")))
        return "report" if "2.02" in codes else "realtime"
    if form == "6-K":
        desc = re.sub(r"\s+", " ", str(doc_description or "")).strip()
        return "report" if _SIX_K_REPORT_RE.search(desc) else "realtime"
    return "realtime"


def kind_for_item(item):
    """从新闻适配器返回的 item 判断分类；兼容 raw_json 中的字段。"""
    if not isinstance(item, dict):
        return "realtime"
    raw = {}
    try:
        raw = json.loads(item.get("raw_json") or "{}")
    except Exception:
        raw = {}
    return classify_news_kind(
        item.get("source"),
        item.get("form") or raw.get("form", ""),
        item.get("items") or raw.get("items", ""),
        item.get("doc_description") or raw.get("docDescription")
        or raw.get("doc_description", ""),
    )


def kind_from_raw(source, raw_json):
    """旧数据回填/读取兜底：从 raw_json 判断；缺失证据时 realtime。"""
    raw = {}
    try:
        raw = json.loads(raw_json or "{}") if isinstance(raw_json, str) else (raw_json or {})
    except Exception:
        raw = {}
    return classify_news_kind(
        source,
        raw.get("form", ""),
        raw.get("items", ""),
        raw.get("docDescription") or raw.get("doc_description", ""),
    )
