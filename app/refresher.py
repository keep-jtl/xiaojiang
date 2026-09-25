# -*- coding: utf-8 -*-
"""统一后台刷新器（2026-09-25 用户确认：方案 A1「日期+时段」标记 + B1 标准库 threading）

为什么要有它
------------
以前每个页面各写各的定时器：开着哪一页、哪一页才刷新，节奏不一致还可能重复联网。
现在改为 **Python 侧一个常驻后台线程统一负责**，界面只接收"刷新完成"事件后重取数据。

工作方式
--------
- 启动后立即 tick 一次；之后每 TICK_SECONDS 秒（正式 600 秒 = 10 分钟）唤醒一次；
- 一个 tick 依次跑三个任务，**三者互相独立，任一失败都不影响其他**：
    ① 交易日历：幂等；联网自带"每个市场每天最多一次"防抖（market_cal 内已有）
    ② 基金净值：按基金逐个判断是否需要联网（判定规则见下）
    ③ 市场行情：只在有市场处于"交易中"时才联网（休市时行情是快照，不会变）
- _TICK_LOCK 保证上一轮没跑完时不会叠加下一轮。

净值刷新的"完成判定"（2026-09-25 用户逐条确认）
----------------------------------------------
核心：**按基金逐个判断，不按全局**。判定标准是"这次联网有没有让本地最新净值日期前进"，
而 **不是** "净值日期是否等于今天"—— QDII 等基金净值天然滞后（2026-09-25 实测：
005827 最新 09-24，QDII 000041 最新 09-23），要求"等于今天"会让它永远完不成，
整晚反复联网。所以只看"有没有变新"：滞后 1 天、2 天都无所谓，哪天出哪天算完成。

时段 slot：一天至少分两段，标记形如 2026-09-25@day / 2026-09-25@evening
    day     00:00 ~ 20:29   白天：当天净值还没公布，目的是补上"最近可能漏掉的"
    evening 20:30 ~ 24:00   晚间：等当天净值公布。20:30 只是"允许开始查"，
                            不代表净值已经公布（各基金公布时间不同，QDII 更晚）

每只基金各自记录 {slot, last_try, tries}（app_meta.nav_refresh_state，JSON）：
    slot      这只基金 **已经完成** 的最后一个时段（不是"当前所在时段"；
              只有"确认无需再试"或"确实拿到更新"才写，光是进入 evening 不算完成）
    last_try  上次联网尝试时刻。既控制 30 分钟最小间隔，也决定 tries 属于哪个时段
    tries     该时段内已尝试次数。时段一换就自动归零（由 last_try 反推，见 _tries_of），
              所以昨天 evening 试满 6 次不会导致今天 day 无法尝试

逐只判定顺序（互不影响，一只出错不拖累其他只）：
    1. slot == 当前时段            → 跳过（本时段已完成）
    2. 本时段已试满 NAV_MAX_TRIES   → 跳过（下个时段重新开始，不会永久锁死）
    3. 距上次尝试不足 NAV_MIN_INTERVAL → 跳过（退避，防止整晚反复联网）
    4. 本地净值日期已是今天          → 跳过并记为完成（源上不会有更新的了；
                                     对 QDII 通常不成立，不成立也没关系）
    5. 联网拉取：
       day     成功即算完成（白天本来就没有当天净值可等，拿到"源上现有最新"就够）
       evening 净值日期前进才算完成；没前进 → 不完成，下轮再来
       失败    → 不完成、库里旧净值原样保留、不弹窗不打扰；下轮按规则再试

已知技术债（TODO，2026-09-25 记录，本轮不处理）
--------------------------------------------
`sources.fund_nav.refresh_fund_nav()` 每次都拉**整段历史净值**（005827 约 1900 条 /
QDII 000041 约 4400 条），一轮就是几百 KB。上面的 30 分钟间隔与 6 次上限就是为了
压制这个成本。将来可研究是否只取最新净值或做增量更新，届时再单独评估。
"""
import datetime
import json
import threading
import time

from . import db as dbm
from . import market_cal as mcal
from .sources import fund_nav as src

# ---------- 可调参数（2026-09-25 用户确认的取值） ----------
TICK_SECONDS = 600                    # 正式运行：每 10 分钟一个 tick（验收时可临时改小）
NAV_MIN_INTERVAL = 30 * 60            # 同一只基金两次联网的最小间隔：30 分钟
NAV_MAX_TRIES = 6                     # 每个时段每只基金最多联网尝试次数
EVENING_START = datetime.time(20, 30)  # 晚间时段起点（只是"允许开始查"，不是"净值已公布"）

STATE_KEY = "nav_refresh_state"       # app_meta 里的按基金状态（JSON）
VERBOSE = True                        # 打印每个 tick 的判定过程（方便用户看它怎么工作）

# 净值刷新全局锁：akshare 底层 JS 引擎（py_mini_racer/V8）线程不安全，并发调用会崩溃
# （2026-09-03 实测复现）。所有联网拉净值的入口必须经此锁串行执行。
NAV_REFRESH_LOCK = threading.Lock()

# 行情刷新锁：统一刷新器与界面手动按钮可能同时发起，重复联网没有意义
# （非阻塞：已经有一轮在跑就直接退出，那一轮完成时会派发同样的事件）
QUOTE_REFRESH_LOCK = threading.Lock()

# 状态读改写锁：只保护 app_meta.nav_refresh_state 的读-改-写，绝不跨联网调用持有
_STATE_LOCK = threading.Lock()

# 一个 tick 没跑完，不允许开下一个
_TICK_LOCK = threading.Lock()

_STARTED = False
_ACTION_CN = {
    "skip_done": "跳过：本时段已完成",
    "skip_max": "跳过：本时段尝试次数已用完",
    "skip_backoff": "跳过：距上次尝试不足 30 分钟",
    "skip_uptodate": "跳过：本地已是今天净值（源上不会更新了）→ 记为完成",
    "advanced": "联网 → 净值日期前进，拿到新净值（完成本时段）",
    "fetched": "联网 → 已取回数据（白天时段完成本时段）",
    "no_new": "联网 → 源上暂无更新，下轮再试",
    "failed": "联网 → 失败（旧数据保留），下轮再试",
    "error": "异常（不影响其他基金），下轮再试",
    "pending": "待联网…",
}


# ====================== 时段与状态 ======================

def current_slot(now=None):
    """当前时段标记 'YYYY-MM-DD@day' / 'YYYY-MM-DD@evening'"""
    now = now or datetime.datetime.now()
    part = "evening" if now.time() >= EVENING_START else "day"
    return f"{now.date().isoformat()}@{part}"


def _parse_ts(s):
    """'YYYY-MM-DD HH:MM:SS' → datetime；解析不了返回 None"""
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _tries_of(rec, slot):
    """这只基金在当前时段的已尝试次数。

    tries 属于 last_try 所在的时段：last_try 不在当前时段 → 归零。
    这样"昨天 evening 试满 6 次"不会带到今天 day 去（用户 2026-09-25 要求）。
    """
    last = _parse_ts(rec.get("last_try"))
    if last is None or current_slot(last) != slot:
        return 0
    return int(rec.get("tries") or 0)


def _load_state(conn):
    """读取按基金状态 {code: {slot, last_try, tries}}；缺失/损坏一律当空（宁可重查一次）"""
    raw = dbm.get_app_meta(conn, STATE_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(conn, state):
    dbm.set_app_meta(conn, STATE_KEY, json.dumps(state, ensure_ascii=False))
    conn.commit()


def _latest_nav_date(conn, code):
    """本地库里该基金的最新净值日期（不联网）"""
    row = src.latest_nav(conn, code)
    return row["nav_date"] if row else None


def holding_codes(conn=None):
    """当前"持有中"（status=holding）的基金代码 —— 与页面自动更新口径一致。

    只关注列表（watch）与历史持仓（closed）不自动联网：前者没有金额、后者已清仓，
    和打开页面时那次自动更新的口径保持相同。
    """
    own = conn is None
    conn = conn or dbm.connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT fund_code FROM holdings WHERE deleted = 0 AND status = 'holding'"
        ).fetchall()
        return [r["fund_code"] for r in rows]
    finally:
        if own:
            conn.close()


# ====================== 净值：判断 → 占位 → 联网 ======================

def refresh_nav(codes, notify=None, mode="auto", background=True):
    """统一净值刷新入口（界面自动更新 / 统一刷新器 tick / 测试都走这里）。

    返回 {"started": bool, "report": [...]}：
      started=False 表示本轮所有基金都无需联网（页面据此不显示"正在获取"）；
      report 每项 {code, action, before, after, tries}，action 见 _ACTION_CN。
      background=True 时先做本地判断（快、不联网）再决定是否起后台线程，
      此时返回的 report 是"判断结果"，后台线程完成后会把结果原地更新。
    """
    codes = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    report = []
    if not codes:
        return {"started": False, "report": report}

    slot = current_slot()
    evening = slot.endswith("@evening")
    now = datetime.datetime.now()

    with _STATE_LOCK:
        conn = dbm.connect()
        try:
            state = _load_state(conn)
            due = []
            for code in codes:
                rec = state.get(code) or {}
                entry = {"code": code, "action": "pending"}
                tries = _tries_of(rec, slot)

                if rec.get("slot") == slot:
                    entry["action"] = "skip_done"
                elif tries >= NAV_MAX_TRIES:
                    entry.update(action="skip_max", tries=tries)
                elif _in_backoff(rec, now):
                    entry.update(action="skip_backoff", last_try=rec.get("last_try"))
                else:
                    before = _latest_nav_date(conn, code)
                    if before == dbm.today_str():
                        # 源上不可能再有更新的了 → 确认无需再试，记为完成
                        state[code] = {"slot": slot, "last_try": rec.get("last_try"), "tries": tries}
                        entry.update(action="skip_uptodate", before=before)
                    else:
                        # 占位：立刻记下"已尝试 + 1"，防止同一只被重复发起
                        state[code] = {"slot": rec.get("slot"),
                                       "last_try": dbm.now_str(),
                                       "tries": tries + 1}
                        entry.update(before=before, tries=tries + 1)
                        due.append(entry)
                report.append(entry)
            _save_state(conn, state)
        finally:
            conn.close()

    if not due:
        return {"started": False, "report": report}

    if background:
        threading.Thread(target=_fetch_phase, args=(due, notify, mode, report),
                         daemon=True, name="xltc-nav").start()
    else:
        _fetch_phase(due, notify, mode, report)
    return {"started": True, "report": report}


def _in_backoff(rec, now):
    last = _parse_ts(rec.get("last_try"))
    if last is None:
        return False
    return (now - last).total_seconds() < NAV_MIN_INTERVAL


def _fetch_phase(due, notify, mode, report=None):
    """联网阶段：逐只拉取、写完成标记。失败只影响该只，旧数据不动。

    整个拉取过程持 NAV_REFRESH_LOCK（V8 引擎线程不安全，必须串行）。
    report 传完整判定列表（含被跳过的），结束时统一打印，便于用户看清每轮做了什么。
    """
    changed, failed = [], []
    with NAV_REFRESH_LOCK:
        for entry in due:
            code = entry["code"]
            try:
                src.refresh_fund_nav(code)
                conn = dbm.connect()
                try:
                    after = _latest_nav_date(conn, code)
                finally:
                    conn.close()
                before = entry.get("before")
                advanced = bool(after) and (before is None or after > before)
                evening = current_slot().endswith("@evening")
                entry["after"] = after
                if not evening or advanced:
                    # day 时段：拿到数据即完成；evening 时段：净值变新才算完成
                    entry["action"] = "advanced" if advanced else "fetched"
                    _mark_done(code)
                    changed.append(code)
                else:
                    entry["action"] = "no_new"
            except Exception as e:
                print(f"[refresher] 刷新净值失败 {code}（旧数据保留）: {e}")
                entry["action"] = "failed"
                failed.append(code)

    _log_report(report if report is not None else due)

    if notify and changed:
        # 只有真的拿到新数据才通知界面重取；全部跳过/失败时保持安静，不打扰用户
        notify({"type": "dataUpdated", "ok": len(failed) == 0, "mode": mode,
                "changed": changed, "failed": failed, "at": dbm.now_str()})


def _log_report(report):
    """逐只打印本轮判定结果（跳过 / 联网 / 为什么），供用户查看刷新器在做什么"""
    if not VERBOSE:
        return
    for e in report:
        extra = ""
        if e.get("before") or e.get("after"):
            extra = f"  （净值日期 {e.get('before') or '—'} → {e.get('after') or '—'}）"
        elif e.get("tries"):
            extra = f"  （本时段第 {e['tries']}/{NAV_MAX_TRIES} 次）"
        print(f"[refresher]     · {e['code']}  {_ACTION_CN.get(e['action'], e['action'])}{extra}")


def _mark_done(code):
    """把这只基金的 slot 记为当前时段（只改 slot，last_try/tries 由占位阶段负责）"""
    slot = current_slot()
    with _STATE_LOCK:
        conn = dbm.connect()
        try:
            state = _load_state(conn)
            rec = state.get(code) or {}
            if rec.get("slot") != slot:
                rec["slot"] = slot
                state[code] = rec
                _save_state(conn, state)
        finally:
            conn.close()


# ====================== 三个任务 ======================

def _task_calendar():
    """交易日历：幂等；联网自带"每市场每天最多一次"防抖，失败保留旧日历"""
    conn = dbm.connect()
    try:
        r = mcal.ensure_calendar(conn)
    finally:
        conn.close()
    detail = r.get("detail") or ""
    if r.get("imported"):
        return f"导入内置年份 {'/'.join(r['imported'])} {detail}".strip()
    updated = {k: v for k, v in r.get("updated", {}).items() if v not in ("ok", "ok_noop")}
    return f"无变化" if not updated else f"{updated}"


def _task_nav(notify, mode="auto"):
    """基金净值：按基金逐个判断是否需要联网"""
    codes = holding_codes()
    if not codes:
        return "没有持仓，跳过"
    result = refresh_nav(codes, notify=notify, mode=mode, background=False)
    report = result["report"]
    netted = [e["code"] for e in report if not str(e["action"]).startswith("skip")]
    if not result["started"]:
        return f"共 {len(codes)} 只，全部跳过（本时段无需联网）"
    return f"共 {len(codes)} 只：联网 {len(netted)} 只，跳过 {len(codes) - len(netted)} 只"


def _task_quotes(api):
    """市场行情：只在有市场处于"交易中"时才联网"""
    sess = api.get_market_sessions()
    trading = [m for m in ("cn", "hk", "us")
               if sess["markets"][m]["state"] == "trading"]
    if not trading:
        return "三市场都不在交易中，跳过联网"
    api.refresh_market_quotes()
    return f"{'/'.join(trading)} 交易中 → 已发起联网刷新"


# ====================== tick 与常驻循环 ======================

def tick(api, notify=None, n=None, log=None):
    """一个刷新节拍：日历 → 净值 → 行情。三者互相独立，任一失败不影响其他。"""
    log = VERBOSE if log is None else log
    if not _TICK_LOCK.acquire(blocking=False):
        if log:
            print("[refresher] 上一轮刷新还没结束，本轮跳过")
        return {"skipped": True}
    try:
        if log:
            print(f"[refresher] ── tick #{n if n is not None else '-'}  "
                  f"{dbm.now_str()}  时段={current_slot()} ──")
        out = {}
        for name, fn in (("日历", _task_calendar),
                         ("净值", lambda: _task_nav(notify)),
                         ("行情", lambda: _task_quotes(api))):
            try:
                brief = fn()
                out[name] = brief
                if log and brief:
                    print(f"[refresher]   {name}：{brief}")
            except Exception as e:
                out[name] = f"异常：{e}"
                print(f"[refresher]   {name}任务异常（不影响其他任务）: {e}")
        return out
    finally:
        _TICK_LOCK.release()


def _loop(api, notify, tick_seconds):
    n = 0
    while True:
        n += 1
        t0 = time.monotonic()
        try:
            tick(api, notify, n)
        except Exception as e:          # 兜底：tick 内部已逐任务隔离，这里只是最后一道
            print(f"[refresher] tick 异常（不影响后续）: {e}")
        time.sleep(max(1.0, tick_seconds - (time.monotonic() - t0)))


def start(api, notify=None, tick_seconds=None):
    """启动统一后台刷新器（常驻守护线程）：立即 tick 一次，之后每 tick_seconds 秒一次。

    重复调用只会启动一次。notify 传 Api._notify_js 即可（完成时通知界面重取）。
    """
    global _STARTED
    if _STARTED:
        return False
    _STARTED = True
    seconds = tick_seconds or TICK_SECONDS
    print(f"[refresher] 统一刷新器已启动：启动立即检查一次，之后每 {seconds} 秒一次")
    threading.Thread(target=_loop, args=(api, notify, seconds),
                     daemon=True, name="xltc-refresher").start()
    return True
