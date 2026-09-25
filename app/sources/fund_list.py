# -*- coding: utf-8 -*-
"""全市场基金名单（搜索用）——akshare fund_name_em，进程内缓存一天

说明（2026-09-04）：
- 天天基金全市场名单约 1.6 万只，每次搜索都重新联网拉全量不现实；
  首次搜索时联网拉取一次，之后一整天直接走内存缓存，瞬时出结果。
- 与净值刷新（fund_nav.py）可能并发调用 akshare，若实测崩溃需并入同一把锁；
  本接口走 requests 抓 JSON 不经 V8 引擎，暂用独立锁，注释留痕待真测。

搜索增强（2026-09-25 用户确认方案 B；同日按用户反馈修订）：
- 关键词顺序无关：用户不需要知道官方名称的词序，输入主要关键词即可；
- 输入标准化：忽略多余空格与常见分隔符、英文字母不分大小写；
- 提示词全部命中（AND）才算模糊匹配，避免搜出大量无关基金；
- 相关度排序：代码精确 > 关键词含完整代码 > 名称精确 > 关键词含完整名称
  > 代码前缀 > 名称前缀 > 整段连续 > 关键词全命中（全命中里段数越少越靠前）；
- **不使用词库、不硬编码任何基金/公司/主题名**：靠"文本覆盖判定"实现，
  因此对股票型、指数型、ETF、ETF联接、LOF、QDII 等各类名称同样通用；
- **纯数字不拆分**：基金代码是整体标识（如 000001），不是可以拆开的词；
  否则会被切成「0000」+「01」，把毫不相关的基金也匹配进来（2026-09-25 修复）；
- 过短输入（单字）只保留原有的连续子串匹配，不做进一步模糊，避免结果泛滥。
"""
import re
import threading
import time

_LIST_LOCK = threading.Lock()
_CACHE = None       # [(code, name, fund_type), ...]
_CACHE_AT = 0.0     # 缓存时间戳
CACHE_TTL = 86400   # 秒：一天

# 输入标准化：空格与常见中英文分隔符一律视为切分点（只处理"用户输入"，不动官方名称）
_SEPS = re.compile(r"[\s\-_·、,，。.:：;；/\\|｜+＋()（）\[\]【】<>《》\"'“”‘’]+")

# 模糊匹配时允许的最小片段长度：2 字符起。
# 设为 2 是防止把输入切得太碎（如单字）导致满屏无关结果。
_MIN_SEG = 2

# 相关度分值（越大越靠前）
_SC_CODE_EXACT = 1000        # 输入整体就是完整代码（159819）
_SC_TOKEN_CODE_EXACT = 950   # 多关键词之一就是完整代码（159819 人工智能ETF易方达）
_SC_NAME_EXACT = 900         # 输入整体就是完整名称
_SC_TOKEN_NAME_EXACT = 850   # 多关键词之一就是完整名称
_SC_CODE_PREFIX = 800        # 代码前缀（1598 → 159819）
_SC_NAME_PREFIX = 700        # 名称以输入开头
_SC_SUBSTRING = 600          # 输入整段连续出现在名称或代码中
_SC_SEGMENTS = 400           # 关键词全部命中（顺序无关、可间隔）


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


def _can_cut(seg, i):
    """能否在 seg 的位置 i 处切开（左边 seg[:i]、右边 seg[i:]）。

    数字串内部不允许切：基金代码是整体标识（000001），不是可以拆开的词。
    否则「000001」会被切成「0000」+「01」，这两个碎片恰好都出现在别的基金
    代码里，于是搜 000001 会带出一堆毫不相关的基金（2026-09-25 修复）。
    中文之间、中文与英文之间仍可自由切分——那才是"关键词顺序不同"要处理的场景。
    """
    if i <= 0:
        return True
    return not (seg[i - 1].isdigit() and seg[i].isdigit())


def _min_pieces(seg, text):
    """seg 最少能由 text 的几段拼出来（每段都是 text 的子串、顺序保持、每段≥2 字符）。

    返回 0 表示拼不出来。段数越少 = 输入与官方名称越接近、越连续，相关度越高。
    例：「易方达人工智能ETF」=「易方达」+「人工智能ETF」两段，
        官方名「人工智能ETF易方达」正好由这两段拼出 → 2 段命中。
    不依赖词库、不认识任何专有名词，对任意基金名通用。
    """
    if not seg or not text:
        return 0
    if seg in text:              # 整段就是子串：一段够了，最快路径
        return 1
    n = len(seg)
    if n < _MIN_SEG * 2:         # 长度不够切出两个有效片段
        return 0
    if seg[0] not in text:       # 剪枝：首字符不出现则必然不可能
        return 0
    inf = n + 1
    best = [inf] * (n + 1)       # best[j]：拼出 seg[:j] 所需的最少段数
    best[0] = 0
    for j in range(_MIN_SEG, n + 1):
        for i in range(0, j - _MIN_SEG + 1):
            if best[i] < inf and _can_cut(seg, i) and seg[i:j] in text:
                if best[i] + 1 < best[j]:
                    best[j] = best[i] + 1
    return 0 if best[n] >= inf else best[n]


def _score(code, name_lower, tokens, whole):
    """相关度打分；返回 (分值, 段数)，段数仅"关键词全命中"档有意义。

    code/名称已小写，tokens=关键词列表，whole=去掉分隔符后的整段形态。
    返回 (0, 0) 表示不匹配。
    """
    if code == whole:                                  # ① 输入整体就是完整代码
        return _SC_CODE_EXACT, 0
    for t in tokens:                                   # ② 某个关键词就是完整代码
        if t == code:
            return _SC_TOKEN_CODE_EXACT, 0
    if name_lower == whole:                            # ③ 输入整体就是完整名称
        return _SC_NAME_EXACT, 0
    for t in tokens:                                   # ④ 某个关键词就是完整名称
        if t == name_lower:
            return _SC_TOKEN_NAME_EXACT, 0
    if whole.isdigit() and code.startswith(whole):     # ⑤ 代码前缀（代码必为数字）
        return _SC_CODE_PREFIX, 0
    if name_lower.startswith(whole):                   # ⑥ 名称以输入开头
        return _SC_NAME_PREFIX, 0
    if whole in name_lower or whole in code:           # ⑦ 整段连续出现
        return _SC_SUBSTRING, 0
    if whole[0] not in name_lower and whole[0] not in code:
        return 0, 0                                    # 剪枝：首字符不出现必然不可能
    total = 0
    for t in tokens:                                   # ⑧ 关键词全部命中（AND，顺序无关）
        p = _min_pieces(t, name_lower) or _min_pieces(t, code)
        if not p:
            return 0, 0
        total += p
    return _SC_SEGMENTS, total


def search(keyword, limit=20):
    """搜索全市场基金：代码 / 名称 / 关键词（顺序无关），按相关度返回前 limit 条

    返回 [{"code","name","fund_type"}]，结构不变（界面无需改动）。
    """
    k = (keyword or "").strip()
    if not k:
        return []
    tokens = [t for t in _SEPS.sub(" ", k).lower().split(" ") if t]
    if not tokens:
        return []
    whole = "".join(tokens)        # 去掉用户输入的分隔符后的整段形态

    hits = []
    for code, name, ftype in get_fund_list():
        score, pieces = _score(code, name.lower(), tokens, whole)
        if score:
            hits.append((score, pieces, len(name), code, name, ftype))
    # 相关度降序 → 段数少者（更连续）在前 → 名称短者（更接近精确匹配）在前；
    # 三者都相同则保持名单原顺序（sort 稳定，不额外引入随机性）
    hits.sort(key=lambda x: (-x[0], x[1], x[2]))
    return [{"code": c, "name": n, "fund_type": t}
            for _, _, _, c, n, t in hits[:limit]]
