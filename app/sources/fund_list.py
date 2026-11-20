# -*- coding: utf-8 -*-
"""全市场基金名单（搜索用）——akshare fund_name_em，内存 + 磁盘两级缓存

说明（2026-09-04；2026-11-16 增加持久化磁盘缓存）：
- 天天基金全市场名单约 2.8 万只，每次搜索都重新联网拉全量不现实；
  首次搜索时联网拉取一次，之后 24 小时内直接走缓存，瞬时出结果。
- 内存缓存：进程内，程序运行期间最快；程序重启后内存为空。
- 磁盘缓存（data/fund_list_cache.json）：联网成功后原子落盘，重启后若磁盘
  缓存仍在 24 小时有效期内直接复用，不重新下载；网络失败时即使磁盘缓存
  已过期也允许回退使用，并通过 cache_info() 标注 stale 与更新时间。
- 与净值刷新（fund_nav.py）可能并发调用 akshare，若实测崩溃需并入同一把锁；
  本接口走 requests 抓 JSON 不经 V8 引擎，暂用独立锁，注释留痕待真测。

搜索规则（2026-11-09 第一负责人验收后修订）：
- 归一化：NFKC（全角→半角）、小写、连接/联接同义、去掉空格与常见分隔符；
- 查询先按“整段”匹配，整段失败再按关键词 AND（顺序无关）兜底；
- 分段匹配：每段至少 2 字符、数字串不拆、顺序保持；允许末尾单字符 A/C/E
  作为份额类别（如“易方达 联接 A”）；
- 相关度排序：代码精确 > 关键词含完整代码 > 名称精确 > 关键词含完整名称
  > 代码前缀 > 名称前缀 > 整段连续 > 分段命中 > 关键词全命中；
- 同分时比较共同前缀长度、名称长度、代码，结果稳定可复现；
- 短查询（≤3 字且非纯数字）最多返回 100 条，普通查询 20 条；
- 不做拼音匹配、不硬编码任何基金/公司/主题名。
"""
import json
import os
import re
import threading
import time
import unicodedata

from .. import db as dbm

_LIST_LOCK = threading.Lock()
_CACHE = None       # [(code, name, fund_type), ...]
_CACHE_AT = 0.0     # 缓存时间戳（epoch 秒）
_CACHE_STALE = False  # 当前内存缓存是否来自过期磁盘（联网失败回退）
CACHE_TTL = 86400   # 秒：一天

# 磁盘缓存：放在应用数据目录 data/ 下（与 xltc.db 同目录），不新建数据库表
_DISK_CACHE_NAME = "fund_list_cache.json"
_DISK_VERSION = 1

# 输入标准化：空格与常见中英文分隔符一律视为切分点（只处理"用户输入"，不动官方名称）
_SEPS = re.compile(r"[\s\-_·、,，。.:：;；/\\|｜+＋()（）\[\]【】<>《》\"'“”‘’%]+")

# 常见同义/异体写法（官方名称使用“联接”）
_SYNONYMS = (
    ("连接", "联接"),
    ("連接", "联接"),
    ("聯接", "联接"),
    ("聯", "联"),
)

# 通用词：用户输入里出现时不参与匹配（官方名称一般不含这些词）
_GENERIC_WORDS = ("证券投资基金", "基金")

# 模糊匹配时允许的最小片段长度：2 字符起。
# 设为 2 是防止把输入切得太碎（如单字）导致满屏无关结果。
_MIN_SEG = 2

# 份额类别单字：允许作为独立片段（A/C/E）
_SHARE_CLASSES = ("a", "c", "e")

# 相关度分值（越大越靠前）
_SC_CODE_EXACT = 1000        # 输入整体就是完整代码（159819）
_SC_TOKEN_CODE_EXACT = 950   # 多关键词之一就是完整代码（159819 人工智能ETF易方达）
_SC_NAME_EXACT = 900         # 输入整体就是完整名称
_SC_TOKEN_NAME_EXACT = 850   # 多关键词之一就是完整名称
_SC_CODE_PREFIX = 800        # 代码前缀（1598 → 159819）
_SC_NAME_PREFIX = 700        # 名称以输入开头
_SC_SUBSTRING = 600          # 输入整段连续出现在名称或代码中
_SC_SEGMENTS = 400           # 整段可由名称中的连续片段拼出
_SC_TOKEN_SEGMENTS = 390     # 关键词全部命中（顺序无关、可间隔）


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


def _disk_cache_path():
    """磁盘缓存文件路径（data/fund_list_cache.json）。"""
    return os.path.join(dbm.DATA_DIR, _DISK_CACHE_NAME)


def _load_disk_cache():
    """读取磁盘缓存，返回 (items, fetched_at)；不存在/损坏/版本不符返回 None。"""
    path = _disk_cache_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("version") != _DISK_VERSION:
            return None
        items = [tuple(x) for x in payload["items"]]
        fetched_at = float(payload["fetched_at"])
        if not items:
            return None
        return items, fetched_at
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_disk_cache(items, fetched_at):
    """临时文件 + 原子替换落盘，避免异常退出造成缓存损坏；失败只返回 False。"""
    try:
        os.makedirs(dbm.DATA_DIR, exist_ok=True)
        payload = {
            "version": _DISK_VERSION,
            "fetched_at": fetched_at,
            "items": [list(x) for x in items],
        }
        tmp = _disk_cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, _disk_cache_path())
        return True
    except OSError:
        return False


def get_fund_list():
    """全量名单（内存 + 磁盘两级缓存；彻底失败时抛出由调用方降级提示）。

    分层：内存新鲜 → 直接返回；否则进锁双检 → 磁盘新鲜则复用（重启不重新
    下载）→ 磁盘缺失/过期则联网，成功更新内存与磁盘，联网失败回退过期磁盘
    缓存并标记 stale，连磁盘都没有才抛异常。
    """
    global _CACHE, _CACHE_AT, _CACHE_STALE
    now = time.time()
    if _CACHE is not None and not _CACHE_STALE and now - _CACHE_AT < CACHE_TTL:
        return _CACHE
    with _LIST_LOCK:
        now = time.time()
        # 双检：等待锁期间别的调用可能已拉好
        if _CACHE is not None and not _CACHE_STALE and now - _CACHE_AT < CACHE_TTL:
            return _CACHE
        disk = _load_disk_cache()
        # 磁盘缓存仍在有效期内：直接复用，不联网（覆盖重启场景）
        if disk is not None and now - disk[1] < CACHE_TTL:
            _CACHE, _CACHE_AT, _CACHE_STALE = disk[0], disk[1], False
            return _CACHE
        # 磁盘缺失或过期：尝试联网拉取
        try:
            items = _fetch_all()
        except Exception:
            # 联网失败：回退过期磁盘缓存（即使 stale），没有则抛出让调用方降级
            if disk is not None:
                _CACHE, _CACHE_AT, _CACHE_STALE = disk[0], disk[1], True
                return _CACHE
            raise
        ts = time.time()
        _CACHE, _CACHE_AT, _CACHE_STALE = items, ts, False
        _write_disk_cache(items, ts)   # 落盘失败不阻断内存使用
        return _CACHE


def cache_info():
    """当前名单缓存元信息：{available, fetched_at, stale}。

    stale=True 表示联网失败、正在使用已过期的磁盘缓存，界面应显示更新时间。
    """
    return {
        "available": _CACHE is not None,
        "fetched_at": _CACHE_AT,
        "stale": _CACHE_STALE,
    }


def normalize_text(value):
    """统一大小写/全半角/常见异体写法；不改官方展示名称。"""
    if value is None:
        return ""
    t = unicodedata.normalize("NFKC", str(value)).lower()
    for old, new in _SYNONYMS:
        t = t.replace(old, new)
    return t


def normalize_tokens(keyword):
    """用户输入 → 关键词列表（去分隔符/通用词，保留整段拼接能力）。"""
    k = normalize_text(keyword)
    raw = [t for t in _SEPS.sub(" ", k).split(" ") if t]
    tokens = []
    for t in raw:
        for g in _GENERIC_WORDS:
            t = t.replace(g, "")
        if t:
            tokens.append(t)
    return tokens or raw


def compact(value):
    """归一化后去掉分隔符，用于整段/分段匹配。"""
    return _SEPS.sub("", normalize_text(value))


def common_prefix_len(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


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
    份额类别 A/C/E 允许作为单字符片段（如“易方达 联接 A”）。
    """
    if not seg or not text:
        return 0
    if seg in text:              # 整段就是子串：一段够了，最快路径
        return 1
    if len(seg) == 1 and seg in _SHARE_CLASSES and seg in text:
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


def _score(code, name_norm, name_compact, tokens, whole):
    """相关度打分；返回 (分值, 段数, 共同前缀长度)。(0, 0, 0) 表示不匹配。"""
    prefix = common_prefix_len(whole, name_compact)
    if code == whole:                                  # ① 输入整体就是完整代码
        return _SC_CODE_EXACT, 0, prefix
    for t in tokens:                                   # ② 某个关键词就是完整代码
        if t == code:
            return _SC_TOKEN_CODE_EXACT, 0, prefix
    if name_compact == whole:                          # ③ 输入整体就是完整名称
        return _SC_NAME_EXACT, 0, prefix
    for t in tokens:                                   # ④ 某个关键词就是完整名称
        if t == name_compact:
            return _SC_TOKEN_NAME_EXACT, 0, prefix
    if whole.isdigit() and code.startswith(whole):     # ⑤ 代码前缀（代码必为数字）
        return _SC_CODE_PREFIX, 0, prefix
    if name_compact.startswith(whole):                 # ⑥ 名称以输入开头
        return _SC_NAME_PREFIX, 0, prefix
    if whole in name_compact or whole in code:         # ⑦ 整段连续出现
        return _SC_SUBSTRING, 0, prefix
    if whole[0] not in name_compact and whole[0] not in code:
        return 0, 0, 0                                 # 剪枝：首字符不出现必然不可能
    pieces = _min_pieces(whole, name_compact) or _min_pieces(whole, code)
    if pieces:                                         # ⑧ 整段可由连续片段拼出
        return _SC_SEGMENTS, pieces, prefix
    total = 0
    for t in tokens:                                   # ⑨ 关键词全部命中（AND，顺序无关）
        p = _min_pieces(t, name_compact) or _min_pieces(t, code)
        if not p:
            return 0, 0, 0
        total += p
    return _SC_TOKEN_SEGMENTS, total, prefix


def search(keyword, limit=20):
    """搜索全市场基金：代码 / 名称 / 关键词（顺序无关），按相关度返回前 limit 条

    返回 [{"code","name","fund_type"}]，结构不变（界面无需改动）。
    短查询（≤3 字且非纯数字）自动放宽到最多 100 条，避免“易方达”这类
    宽泛关键词把目标基金挡在 20 条之外。
    """
    tokens = normalize_tokens(keyword)
    if not tokens:
        return []
    whole = "".join(tokens)
    if len(whole) <= 3 and not whole.isdigit():
        limit = max(int(limit or 0), 100)

    hits = []
    for code, name, ftype in get_fund_list():
        name_norm = normalize_text(name)
        name_compact = _SEPS.sub("", name_norm)
        score, pieces, prefix = _score(code, name_norm, name_compact, tokens, whole)
        if score:
            hits.append((score, pieces, prefix, len(name), code, name, ftype))
    # 相关度降序 → 段数少者（更连续）在前 → 共同前缀长者在前
    # → 名称短者（更接近精确匹配）在前 → 代码升序（稳定、可复现）
    hits.sort(key=lambda x: (-x[0], x[1], -x[2], x[3], x[4]))
    return [{"code": c, "name": n, "fund_type": t}
            for _, _, _, _, c, n, t in hits[:limit]]
