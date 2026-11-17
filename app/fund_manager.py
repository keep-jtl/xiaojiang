# -*- coding: utf-8 -*-
"""基金经理公开资料读取（天天基金 pingzhongdata）。

用途：研究工具 → 基金经理。只读取**公开页面里的现任经理信息**，
不猜测、不编造；接口失败时返回错误，并保留 fund_meta.manager 的旧值。

数据字段（源站页面变量 Data_currentFundManager）：
- name 现任经理姓名
- workTime 从业年限
- fundSize 管理规模（源站口径，注明“源站”）
- star 经理评分星级（源站口径）
"""
import json
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://fund.eastmoney.com/"}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOCK = threading.Lock()
_CACHE = {}
_TTL = 6 * 3600

_MANAGER_RE = re.compile(r"Data_currentFundManager\s*=\s*")


def _extract_array(text, start):
    """从 start 位置开始的 JSON 数组，按括号深度匹配到对应的右括号。

    源站变量里嵌套了 categories/data 等数组，不能用非贪婪正则截到第一个 `]`。
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _fetch_one(code):
    url = "https://fund.eastmoney.com/pingzhongdata/%s.js" % code
    try:
        raw = OPENER.open(urllib.request.Request(url, headers=UA), timeout=20).read()
        text = raw.decode("utf-8", errors="replace")
        m = _MANAGER_RE.search(text)
        if not m:
            return {"ok": False, "error": "源站页面未返回现任经理变量"}
        start = text.find("[", m.end())
        if start < 0:
            return {"ok": False, "error": "源站经理变量不是数组"}
        payload = _extract_array(text, start)
        if not payload:
            return {"ok": False, "error": "源站经理变量括号不完整"}
        data = json.loads(payload)
        managers = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            managers.append({
                "name": str(item.get("name")),
                "work_time": str(item.get("workTime") or ""),
                "fund_size": str(item.get("fundSize") or ""),
                "star": item.get("star"),
            })
        if not managers:
            return {"ok": False, "error": "源站未提供现任经理姓名"}
        return {"ok": True, "managers": managers, "url": url}
    except Exception as exc:
        return {"ok": False, "error": "读取失败：%s" % exc}


def get_managers(codes):
    """批量读取；返回 {code: {...}}。命中 6 小时内存缓存。"""
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    result = {}
    missing = []
    for code in codes:
        with _LOCK:
            hit = _CACHE.get(code)
            if hit and time.time() - hit[0] < _TTL:
                result[code] = hit[1]
            else:
                missing.append(code)
    if missing:
        with ThreadPoolExecutor(max_workers=min(5, len(missing))) as pool:
            fetched = list(pool.map(_fetch_one, missing))
        for code, data in zip(missing, fetched):
            with _LOCK:
                _CACHE[code] = (time.time(), data)
            result[code] = data
    return result
