# -*- coding: utf-8 -*-
"""全市场基金名单（搜索用）——akshare fund_name_em，进程内缓存一天

说明（2026-09-04）：
- 天天基金全市场名单约 1.6 万只，每次搜索都重新联网拉全量不现实；
  首次搜索时联网拉取一次，之后一整天直接走内存缓存，瞬时出结果。
- 与净值刷新（fund_nav.py）可能并发调用 akshare，若实测崩溃需并入同一把锁；
  本接口走 requests 抓 JSON 不经 V8 引擎，暂用独立锁，注释留痕待真测。
"""
import threading
import time

_LIST_LOCK = threading.Lock()
_CACHE = None       # [(code, name, fund_type), ...]
_CACHE_AT = 0.0     # 缓存时间戳
CACHE_TTL = 86400   # 秒：一天


def _fetch_all():
    """联网拉全量名单（仅缓存未命中时调用）"""
    import akshare as ak
    df = ak.fund_name_em()
    out = []
    for _, r in df.iterrows():
        out.append((
            str(r["基金代码"]).strip(),
            str(r["基金简称"]).strip(),
            str(r.get("基金类型", "")).strip(),
        ))
    return out


def get_fund_list():
    """全量名单（内存缓存；失败抛出由调用方降级提示）"""
    global _CACHE, _CACHE_AT
    now = time.time()
    if _CACHE is not None and now - _CACHE_AT < CACHE_TTL:
        return _CACHE
    with _LIST_LOCK:
        # 双检：可能等待锁期间别的调用已拉好
        if _CACHE is not None and now - _CACHE_AT < CACHE_TTL:
            return _CACHE
        items = _fetch_all()
        _CACHE = items
        _CACHE_AT = time.time()
        return _CACHE


def search(keyword, limit=20):
    """按代码（前缀）/名称（子串）过滤名单，最多 limit 条"""
    k = (keyword or "").strip()
    if not k:
        return []
    kl = k.lower()
    out = []
    for code, name, ftype in get_fund_list():
        if kl in code or kl in name.lower():
            out.append({"code": code, "name": name, "fund_type": ftype})
            if len(out) >= limit:
                break
    return out
