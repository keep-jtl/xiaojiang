# -*- coding: utf-8 -*-
"""市场事件服务（阶段一：官方源 → 去重 → 与真实持仓关联 → 排序 → 入库）

设计原则：
- 只读真实持仓（holdings.status='holding'），不写死基金名单
- 只展示有证据的关联；没有关联就不展示，绝不编造、不凑数
- 新闻是缓存数据：抓取失败保留旧数据，页面显示上次成功时间
- 阶段一只接 SEC EDGAR（美股公告/财报）与美联储 RSS（宏观）
- 中国数据源、基金重仓股自动拉取、媒体 RSS 留到后续阶段
"""
import datetime as dt
import hashlib
import json
import re
import time
import uuid

from . import db as dbm
from .sources import news_fed, news_sec

# ---------------------------------------------------------------------------
# 阶段一：指数成分股人工清单（有来源说明，后续阶段改为自动拉取）
# 只用于判断"这条新闻是否可能影响这只指数基金"；前 5 个视为直接关联，其余间接。
# ---------------------------------------------------------------------------
# 阶段一：前 10 大成分股人工清单（有来源说明；第二阶段改为自动拉取真实重仓股）。
# 指数成分股统一视为“直接关联”；宏观政策（美联储）视为“间接关联”。
NASDAQ100_TOP = ["NVDA", "AAPL", "MSFT", "AMZN", "AVGO",
                 "META", "GOOGL", "GOOG", "TSLA", "COST"]
SP500_TOP = ["NVDA", "AAPL", "MSFT", "AMZN", "AVGO",
             "META", "GOOGL", "GOOG", "TSLA", "JPM"]
NASDAQ_BIOTECH_TOP = ["AMGN", "GILD", "VRTX", "REGN", "MRNA",
                      "BIIB", "ILMN", "EXEL", "NBIX", "SRPT"]

SOURCE_NAMES = {
    "sec_edgar": "SEC EDGAR（美国证监会）",
    "fed_rss": "美联储新闻稿",
}

# 这个错误是“按设计跳过”，不是网络/HTTP/解析失败。
SEC_SKIP_MARKER = "XLTC_SEC_CONTACT_EMAIL"


def _source_status(row):
    """数据源状态：ok 成功 / skipped 按设计跳过 / error 实际失败 / waiting 未执行。"""
    err = (row["last_error"] or "").strip()
    if err:
        if SEC_SKIP_MARKER in err or "已跳过 SEC 抓取" in err:
            return "skipped"
        return "error"
    if row["last_success_at"]:
        return "ok"
    return "waiting"


def _overall_status(statuses):
    """整体状态：ok 全部成功 / partial 部分成功 / failed 全部失败 / waiting 尚未执行。"""
    if not statuses:
        return "waiting"
    ok = statuses.count("ok")
    err = statuses.count("error")
    if ok == len(statuses):
        return "ok"
    if ok > 0:
        return "partial"
    if err > 0:
        return "failed"
    return "waiting"


def _profile_rule(name, fund_type):
    """按基金名称/类型生成画像（阶段一规则；阶段二改为读真实重仓股）"""
    text = f"{name} {fund_type}"
    if "纳斯达克100" in text or "纳斯达克 100" in text:
        return {
            "market": "us", "asset_class": "qdii", "track_index": "纳斯达克100",
            "tickers": NASDAQ100_TOP, "direct_tickers": NASDAQ100_TOP,
            "sectors": ["美股科技", "大型成长"],
            "macro": ["monetary_policy"],
            "source_note": "纳斯达克100前10大成分股人工清单（阶段一，2026-10）",
        }
    if "标普500" in text or "标普 500" in text:
        return {
            "market": "us", "asset_class": "qdii", "track_index": "标普500",
            "tickers": SP500_TOP, "direct_tickers": SP500_TOP,
            "sectors": ["美股大盘", "大型蓝筹"],
            "macro": ["monetary_policy"],
            "source_note": "标普500前10大成分股人工清单（阶段一，2026-10）",
        }
    if "纳斯达克生物科技" in text:
        return {
            "market": "us", "asset_class": "qdii", "track_index": "纳斯达克生物科技",
            "tickers": NASDAQ_BIOTECH_TOP, "direct_tickers": NASDAQ_BIOTECH_TOP,
            "sectors": ["生物科技", "医药"],
            "macro": ["monetary_policy"],
            "source_note": "纳斯达克生物科技前10大成分股人工清单（阶段一，2026-10）",
        }
    if "债券" in text:
        return {
            "market": "cn", "asset_class": "bond", "track_index": "",
            "tickers": [], "direct_tickers": [],
            "sectors": ["利率", "信用债"],
            "macro": ["monetary_policy", "inflation"],
            "source_note": "基金类型推断（阶段一）",
        }
    return {
        "market": "cn", "asset_class": "mixed", "track_index": "",
        "tickers": [], "direct_tickers": [],
        "sectors": ["A股", "主动管理"],
        "macro": [],
        "source_note": "基金元数据（阶段一；重仓股待阶段二）",
    }


def ensure_profiles(conn):
    """从当前真实持仓生成/更新基金画像，返回 profile 列表"""
    rows = conn.execute(
        "SELECT h.fund_code, m.name, m.fund_type "
        "FROM holdings h LEFT JOIN fund_meta m ON m.code = h.fund_code "
        "WHERE h.deleted = 0 AND h.status = 'holding' ORDER BY h.id"
    ).fetchall()
    now = dbm.now_str()
    profiles = []
    for r in rows:
        code = r["fund_code"]
        name = r["name"] or code
        rule = _profile_rule(name, r["fund_type"] or "")
        keywords = {
            "tickers": rule["tickers"],
            "direct_tickers": rule["direct_tickers"],
            "sectors": rule["sectors"],
            "macro": rule["macro"],
        }
        conn.execute(
            "INSERT INTO fund_profiles "
            "(fund_code, market, asset_class, track_index, keywords, source_note, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(fund_code) DO UPDATE SET "
            " market=excluded.market, asset_class=excluded.asset_class, "
            " track_index=excluded.track_index, keywords=excluded.keywords, "
            " source_note=excluded.source_note, updated_at=excluded.updated_at",
            (code, rule["market"], rule["asset_class"], rule["track_index"],
             json.dumps(keywords, ensure_ascii=False), rule["source_note"], now),
        )
        profiles.append({
            "fund_code": code, "name": name,
            "market": rule["market"], "asset_class": rule["asset_class"],
            "track_index": rule["track_index"], "keywords": keywords,
            "source_note": rule["source_note"],
        })
    return profiles


def _norm_title(title):
    t = (title or "").lower()
    return re.sub(r"[^\w一-鿿]+", "", t)


def _content_hash(item):
    day = (item.get("published_at") or "")[:10]
    return hashlib.sha256((_norm_title(item.get("title")) + "|" + day).encode("utf-8")).hexdigest()


def _insert_item(conn, item):
    """插入一条新闻；同源同 ID 或同指纹已存在时返回 None"""
    same = conn.execute(
        "SELECT id FROM news_items WHERE source = ? AND source_id = ?",
        (item["source"], item["source_id"]),
    ).fetchone()
    if same:
        return None

    h = _content_hash(item)
    dup = conn.execute(
        "SELECT id FROM news_items WHERE content_hash = ? LIMIT 1", (h,)
    ).fetchone()
    if dup:
        conn.execute(
            "UPDATE news_items SET duplicate_count = duplicate_count + 1 WHERE id = ?",
            (dup["id"],),
        )
        return None

    now = dbm.now_str()
    cur = conn.execute(
        "INSERT INTO news_items "
        "(uuid, source, source_id, title, summary, url, published_at, published_ts, "
        " fetched_at, content_hash, duplicate_count, importance, raw_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (str(uuid.uuid4()), item["source"], item["source_id"], item["title"],
         item.get("summary"), item.get("url"), item.get("published_at"),
         item.get("published_ts"), now, h, item.get("importance", 0),
         item.get("raw_json"), now),
    )
    return cur.lastrowid


def _match(item, profiles):
    """返回 [(fund_code, relation_type, score, evidence_dict)]"""
    out = []
    source = item.get("source")

    if source == "sec_edgar":
        ticker = str(item.get("ticker") or "").upper()
        for p in profiles:
            kw = p["keywords"]
            if ticker not in kw.get("tickers", []):
                continue
            direct = ticker in kw.get("direct_tickers", [])
            relation = "direct" if direct else "indirect"
            base = 0.9 if direct else 0.55
            evidence = {
                "via": "index_constituent",
                "ticker": ticker,
                "company": item.get("company"),
                "index": p.get("track_index"),
                "list": p.get("source_note"),
                "form": item.get("form"),
            }
            out.append((p["fund_code"], relation,
                        round(base * float(item.get("importance") or 0), 4), evidence))

    elif source == "fed_rss":
        cat = item.get("category")
        for p in profiles:
            score = 0.0
            relation = "indirect"
            if p["market"] == "us" and cat in ("monetary_policy", "regulation", "speech"):
                score = {"monetary_policy": 0.55, "regulation": 0.4, "speech": 0.35}.get(cat, 0.2)
            elif p["asset_class"] == "bond" and cat == "monetary_policy":
                score = 0.25  # 全球利率对国内债基是弱关联
            if score <= 0:
                continue
            evidence = {"via": "macro", "category": cat, "source": "美联储"}
            out.append((p["fund_code"], relation,
                        round(score * float(item.get("importance") or 0), 4), evidence))
    return out


def _store_items(conn, items, profiles):
    new_count = matched_count = 0
    for item in items:
        nid = _insert_item(conn, item)
        if nid is None:
            continue
        new_count += 1
        matches = _match(item, profiles)
        if not matches:
            continue
        matched_count += 1
        for fund_code, relation, score, evidence in matches:
            conn.execute(
                "INSERT OR IGNORE INTO news_item_funds "
                "(news_id, fund_code, relation_type, score, evidence, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (nid, fund_code, relation, score,
                 json.dumps(evidence, ensure_ascii=False), dbm.now_str()),
            )
    return new_count, matched_count


def _mark_attempt(conn, source_key):
    now = dbm.now_str()
    conn.execute(
        "UPDATE news_sources SET last_attempt_at = ?, updated_at = ? WHERE source_key = ?",
        (now, now, source_key),
    )


def _mark_result(conn, source_key, errors):
    now = dbm.now_str()
    if errors:
        conn.execute(
            "UPDATE news_sources SET last_error = ?, updated_at = ? WHERE source_key = ?",
            ("；".join(errors)[:500], now, source_key),
        )
    else:
        conn.execute(
            "UPDATE news_sources SET last_success_at = ?, last_error = NULL, updated_at = ? "
            "WHERE source_key = ?",
            (now, now, source_key),
        )


def refresh_all(conn, notify=None):
    """抓取两个官方源并入库；返回 {new, matched, errors, profiles}"""
    profiles = ensure_profiles(conn)
    conn.commit()  # 画像先落盘，绝不把写锁带进后面的网络请求
    errors = []
    new_total = matched_total = 0

    # 1) SEC：只查当前美股基金画像里的成分股
    tickers = sorted({
        t for p in profiles if p["market"] == "us"
        for t in p["keywords"].get("tickers", [])
    })
    if tickers:
        _mark_attempt(conn, "sec_edgar")
        conn.commit()  # 先提交，网络请求期间不持有写锁
        try:
            items, errs = news_sec.fetch(tickers)
        except Exception as e:
            items, errs = [], [f"SEC 抓取异常：{e}"]
        errors.extend(errs)
        n, m = _store_items(conn, items, profiles)
        new_total += n
        matched_total += m
        _mark_result(conn, "sec_edgar", errs)
        conn.commit()

    # 2) 美联储 RSS
    _mark_attempt(conn, "fed_rss")
    conn.commit()  # 网络请求前先提交
    try:
        items, errs = news_fed.fetch()
    except Exception as e:
        items, errs = [], [f"美联储抓取异常：{e}"]
    errors.extend(errs)
    n, m = _store_items(conn, items, profiles)
    new_total += n
    matched_total += m
    _mark_result(conn, "fed_rss", errs)
    conn.commit()

    # 3) 只保留 30 天，避免缓存无限增长
    cutoff = int(time.time()) - 30 * 86400
    conn.execute("DELETE FROM news_items WHERE published_ts IS NOT NULL AND published_ts < ?", (cutoff,))
    conn.commit()

    if notify:
        notify({"type": "newsUpdated", "ok": not errors,
                "new": new_total, "matched": matched_total,
                "at": dbm.now_str()})
    return {"new": new_total, "matched": matched_total,
            "errors": errors, "profiles": len(profiles)}


def _parse_evidence(s):
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {}


def get_feed(conn, days=7):
    """给界面读取的新闻列表 + 真实统计 + 逐源/整体状态。

    sources[].status: ok 成功 / skipped 按设计跳过 / error 实际失败 / waiting 未执行；
    overall_status: ok / partial / failed / waiting；
    last_attempt_at / last_success_at / latest_item_at 三类时间严格分开。
    """
    cutoff = int(time.time()) - int(days) * 86400
    rows = conn.execute(
        "SELECT * FROM news_items WHERE published_ts IS NOT NULL AND published_ts >= ? "
        "ORDER BY published_ts DESC, importance DESC LIMIT 500",
        (cutoff,),
    ).fetchall()

    items = []
    today = dbm.today_str()
    for r in rows:
        ms = conn.execute(
            "SELECT nif.fund_code, nif.relation_type, nif.score, nif.evidence, m.name "
            "FROM news_item_funds nif LEFT JOIN fund_meta m ON m.code = nif.fund_code "
            "WHERE nif.news_id = ? ORDER BY nif.score DESC",
            (r["id"],),
        ).fetchall()
        funds = [{
            "code": m["fund_code"], "name": m["name"] or m["fund_code"],
            "relation": m["relation_type"], "score": m["score"],
            "evidence": _parse_evidence(m["evidence"]),
        } for m in ms]
        score = max([f["score"] for f in funds], default=0.0)
        relation = "direct" if any(f["relation"] == "direct" for f in funds) else (
            "indirect" if funds else "none")
        raw = {}
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            raw = {}
        if r["source"] == "sec_edgar":
            form = raw.get("form", "")
            if form in ("10-K", "10-Q"):
                cat = "report"
            elif form == "6-K":
                cat = "announce"
            else:
                cat = "event"
        else:
            cat = "policy"
        items.append({
            "id": r["id"],
            "source": r["source"],
            "source_name": SOURCE_NAMES.get(r["source"], r["source"]),
            "title": r["title"],
            "summary": r["summary"] or "",
            "url": r["url"] or "",
            "published_at": r["published_at"],
            "published_ts": r["published_ts"],
            "importance": r["importance"],
            "duplicate_count": r["duplicate_count"],
            "category": cat,
            "score": round(score, 4),
            "relation": relation,
            "funds": funds,
        })

    today_items = [i for i in items if (i["published_at"] or "").startswith(today)]
    related = [i for i in items if i["funds"]]
    today_related = [i for i in today_items if i["funds"]]
    important = [i for i in today_related if i["score"] >= 0.7]
    important_7d = [i for i in related if i["score"] >= 0.7]

    # 逐源统计近 N 天条数和最新文章时间（最新文章时间取缓存内全部条目，用于和“检查时间”区分）
    stat_rows = conn.execute(
        "SELECT source,"
        " SUM(CASE WHEN published_ts IS NOT NULL AND published_ts >= ? THEN 1 ELSE 0 END) AS c,"
        " MAX(published_at) AS latest_item_at"
        " FROM news_items GROUP BY source",
        (cutoff,),
    ).fetchall()
    item_stats = {
        r["source"]: {
            "item_count_7d": int(r["c"] or 0),
            "latest_item_at": r["latest_item_at"],
        }
        for r in stat_rows
    }

    sources = []
    for s in conn.execute("SELECT * FROM news_sources ORDER BY source_key").fetchall():
        stat = item_stats.get(s["source_key"], {})
        sources.append({
            "key": s["source_key"], "name": s["name"],
            "last_attempt_at": s["last_attempt_at"],
            "last_success_at": s["last_success_at"],
            "last_error": s["last_error"],
            "status": _source_status(s),
            "item_count_7d": stat.get("item_count_7d", 0),
            "latest_item_at": stat.get("latest_item_at"),
        })
    last_success = max([s["last_success_at"] for s in sources if s["last_success_at"]] or [None])
    last_attempt = max([s["last_attempt_at"] for s in sources if s["last_attempt_at"]] or [None])
    latest_item_at = max([s["latest_item_at"] for s in sources if s["latest_item_at"]] or [None])
    overall_status = _overall_status([s["status"] for s in sources])
    # stale 保留兼容：只有“没有任何成功结果”才是 stale；部分来源未更新由 overall_status 表达。
    stale = overall_status in ("failed", "waiting")

    return {
        "items": items,
        "summary": {
            "total": len(today_items),
            "related": len(today_related),
            "important": len(important),
            "total_7d": len(items),
            "related_7d": len(related),
            "important_7d": len(important_7d),
        },
        "sources": sources,
        "last_success_at": last_success,
        "last_attempt_at": last_attempt,
        "latest_item_at": latest_item_at,
        "overall_status": overall_status,
        "partial": overall_status == "partial",
        "stale": stale,
        "today": today,
        "window_days": int(days),
    }
