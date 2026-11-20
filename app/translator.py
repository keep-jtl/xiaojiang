# -*- coding: utf-8 -*-
"""英文市场事件自动翻译（MyMemory 公开接口）。

只做一件事：把英文标题/摘要译为简体中文。失败一律返回空串，由调用方
保留英文原文；不伪造译文，不把接口限流提示当译文。
"""
import html
import json
import re
import urllib.parse
import urllib.request

API = "https://api.mymemory.translated.net/get"
FALLBACK_API = "https://clients5.google.com/translate_a/t"
TARGET = "zh-CN"
MAX_CHARS = 480
_CJK = re.compile(r"[\u3400-\u9fff]")


def looks_english(text):
    s = str(text or "").strip()
    return bool(s) and bool(re.search(r"[A-Za-z]", s)) and not _CJK.search(s)


def _clip(text, max_chars=MAX_CHARS):
    s = str(text or "").strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    pos = cut.rfind(" ")
    if pos > max_chars * 0.6:
        cut = cut[:pos]
    return cut.rstrip(".,;:，。；： ")


def _clean(text):
    s = html.unescape(str(text or ""))
    return re.sub(r"\s+", " ", s).strip()


def _valid(out, q):
    out = _clean(out)
    if not out or re.search(r"MYMEMORY WARNING|QUERY LENGTH LIMIT", out, re.I):
        return ""
    if out.lower() == q.lower():
        return ""
    return out


def _translate_mymemory(q, timeout=8):
    params = urllib.parse.urlencode({
        "q": q,
        "langpair": "en|" + TARGET,
    })
    req = urllib.request.Request(
        API + "?" + params,
        headers={"User-Agent": "XiaoLiCaiTong/0.1 (Windows personal app)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return ""
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return ""
    data = payload.get("responseData") or {}
    return _valid(data.get("translatedText"), q)


def _translate_clients5(q, timeout=8):
    """备用公开翻译接口；MyMemory 429 时仍能返回中文。

    返回形如 ["中文"]，只取字符串片段，不把接口错误写进译文。
    """
    params = urllib.parse.urlencode({
        "client": "dict-chrome-ex",
        "sl": "en",
        "tl": TARGET,
        "q": q,
    })
    req = urllib.request.Request(
        FALLBACK_API + "?" + params,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return ""
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return ""
    parts = []
    def walk(x):
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            for k in ("translatedText", "text", "trans"):
                if k in x:
                    walk(x[k])
    walk(payload)
    return _valid(" ".join(parts), q)


def translate_en_zh(text, timeout=8):
    """英文 → 简体中文；非英文、网络失败、接口异常均返回 ''。"""
    q = _clip(text)
    if not q or not looks_english(q):
        return ""
    for fn in (_translate_mymemory, _translate_clients5):
        out = fn(q, timeout)
        if out:
            return out
    return ""
