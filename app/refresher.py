# -*- coding: utf-8 -*-
"""统一后台刷新器（2026-09-26 按用户重新明确的刷新需求简化版）

为什么要有它
------------
以前每个页面各写各的定时器：开着哪一页、哪一页才刷新，节奏不一致还可能重复联网。
现在改为 **Python 侧一个常驻后台线程统一负责**，界面只接收"刷新完成"事件后重取数据。

刷新节奏（2026-09-26 用户明确；优先级高于"尽量少联网"）
------------------------------------------------------
    软件启动 → **立即刷新一次（启动刷新，强制执行）**
    → 之后每 TICK_SECONDS 秒（30 分钟）刷新一次 → 一直循环到软件关闭

"每次启动一定检查一次"是硬要求：即使当天已经刷过、距上次启动没多久、周末或节假日
休市，启动后那一次也要真实联网。运行期间的每一轮同样每只基金、每只指数都真实联网，
**不再有任何跳过规则**（2026-09-26 删除，见下方"已删除的旧规则"）。

一个刷新节拍依次跑三个任务，**三者互相独立，任一失败都不影响其他**：
    ① 交易日历：交给 mcal.ensure_calendar，它自带本地缓存
       —— "刷新周期到了" ≠ "必须重新下载整份日历"：有当天缓存就直接用，不重复联网
    ② 基金净值：逐只真实联网拉取（含 QDII，允许净值日期天然滞后）
    ③ 市场行情：真实联网拉取（休市/节假日也照拉一次，确认能取到的最新行情）

失败处理：任何一步失败都只影响它自己 —— 库中旧数据原样保留、不弹窗、不打断别的任务，
下一轮（30 分钟后）再试；单只基金失败不影响其他基金。

已删除的旧规则（2026-09-26，用户明确"每次启动必查 + 每 30 分钟必查"后不再需要）
-----------------------------------------------------------------------------
    时段 slot（day / evening）与 current_slot()
        —— 原用于"本时段已完成就不重复联网"，与"每轮必查"直接冲突
    每只基金 30 分钟冷却 NAV_MIN_INTERVAL
        —— 定时刷新本身就是 30 分钟，冷却会让"该刷的那一轮"被卡掉
    每个时段最多 6 次 NAV_MAX_TRIES 与 tries 计数
        —— 为压制整晚反复联网而设，现在不再按"整晚"组织刷新
    本地净值已是今天 → 跳过（skip_uptodate）
        —— 现在每轮都真的去数据源要一次，不靠本地日期推断"源上不会有新的了"
    行情"只在有市场交易中才联网"
        —— 改为每轮都拉；休市期间拿到的通常仍是上一交易日收盘数据，这是正常的
    由以上规则产生的 app_meta.nav_refresh_state（按基金 slot/last_try/tries）
        —— 本文件已不再读写它（库里若还留着这个键，只是历史残留，无副作用）

"最近检查时间"（2026-09-26 新增）
---------------------------------
app_meta.nav_last_check_at = 最近一次**真正联网检查基金净值**的时刻。
"检查过" ≠ "净值更新了"：数据源没有更新的净值时，nav_date 保持不动（绝不伪造），
但这个时间照常前进，因为确实查了。只有**确实发出联网请求并成功返回**才写；
一次网都没联（例如本轮没有持仓）就不写，不制造假时间。首页"净值最近检查于 …"读它。
与 last_success_fetch_at 的分工：那个是"净值何时获取"（手动/添加流程的动作时间，
我的基金页在用，本文件不写它）；这个是"净值何时检查"（首页显示口径）。

以后新的数据模块（市场事件 / 盯盘提醒 / 组合体检）怎么接进来
----------------------------------------------------------
在 `tick()` 的任务元组里加一项即可，其它都不用动：

    for name, fn in (("日历", _task_calendar),
                     ("净值", lambda: _task_nav(notify)),
                     ("行情", lambda: _task_quotes(api)),
                     ("市场事件", _task_news)):      # ← 新任务照这个格式加一行

新任务只要满足两点就会被自动纳入"启动刷新 + 每 30 分钟"：
  1) 自己写成 `def _task_xxx(): ...`，**内部自己 try/except**（或依赖 tick 的逐任务隔离），
     失败时保留旧数据、不要抛出到外面拖累其他任务；
  2) 真的联了网、拿到新数据时，用 notify({...}) 派发事件通知界面重取。
**在这些模块真正接上数据源之前不要往这里加**——那只会制造假刷新。

已知技术债（TODO，2026-09-25 记录，仍未处理）
--------------------------------------------
`sources.fund_nav.refresh_fund_nav()` 每次都拉**整段历史净值**（005827 约 1900 条 /
QDII 000041 约 4400 条），一轮就是几百 KB。以前靠 30 分钟冷却 + 每时段 6 次上限压制，
**这些限制 2026-09-26 已按用户要求删除**，所以现在是"每 30 分钟、每只基金各拉一次整段
历史"。数据量不大、实测可接受，但这是目前最主要的优化点：将来改成只取最新净值或
增量更新后，刷新开销会立刻降下来。用户已知情并接受当前口径。
"""
import threading
import time

from . import db as dbm
from . import market_cal as mcal
from . import news_service
from .sources import fund_nav as src

# ---------- 可调参数 ----------
TICK_SECONDS = 30 * 60                # 运行期间每 30 分钟一个刷新节拍（启动时另有一次立即刷新）
CHECK_KEY = "nav_last_check_at"       # app_meta 里的"最近一次真正联网检查净值"时刻
VERBOSE = True                        # 打印每一轮的检查过程（方便用户看它怎么工作）

# 净值刷新全局锁：akshare 底层 JS 引擎（py_mini_racer/V8）线程不安全，并发调用会崩溃
# （2026-09-03 实测复现）。所有联网拉净值的入口必须经此锁串行执行。
NAV_REFRESH_LOCK = threading.Lock()

# 行情刷新锁：统一刷新器与界面手动按钮可能同时发起，重复联网没有意义
# （非阻塞：已经有一轮在跑就直接退出，那一轮完成时会派发同样的事件）
QUOTE_REFRESH_LOCK = threading.Lock()

# app_meta 写入锁：保护 nav_last_check_at 的写入，绝不跨联网调用持有
_STATE_LOCK = threading.Lock()

# 一个刷新节拍没跑完，不允许叠加下一轮
_TICK_LOCK = threading.Lock()

_STARTED = False
_ACTION_CN = {
    "advanced": "联网 → 净值已更新",
    "no_new": "联网 → 数据源暂无更新的净值（旧净值保留）",
    "failed": "联网 → 失败（旧数据保留），下一轮再试",
    "pending": "待联网…",
}


# ====================== 基础读取 ======================

def _latest_nav_date(conn, code):
    """本地库里该基金的最新净值日期（不联网）"""
    row = src.latest_nav(conn, code)
    return row["nav_date"] if row else None


def holding_codes(conn=None):
    """当前"持有中"（status=holding）的基金代码。

    只关注列表（watch）与历史持仓（closed）不自动联网：前者没有金额、后者已清仓。
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


def mark_checked(now=None):
    """记下"最近一次真正联网检查基金净值"的时刻（app_meta.nav_last_check_at）。

    只在**确实执行了联网检查且请求成功返回**时调用。"检查过"与"净值真的更新了"
    是两个概念（2026-09-26 用户要求区分），所以：
      - 联网成功但源上没有更新的净值 → **照样记**（确实查过了，只是没新数据）；
      - 一次网都没联（例如本轮没有持仓）→ **绝不记**，不能凭空造出一个检查时间。
    与 last_success_fetch_at 的分工：那个是"净值什么时候获取的"（手动/添加流程的
    动作时间，我的基金页在用），这个是"净值什么时候检查的"（首页显示口径）。
    """
    with _STATE_LOCK:
        conn = dbm.connect()
        try:
            dbm.set_app_meta(conn, CHECK_KEY, now or dbm.now_str())
            conn.commit()
        finally:
            conn.close()


# ====================== 净值：每轮逐只真实联网 ======================

def refresh_nav(codes, notify=None, mode="auto"):
    """统一净值刷新入口（统一刷新器 tick / 手动刷新 / 测试都走这里）。

    **每次调用都会对每只基金真实联网**，没有任何跳过规则（2026-09-26 起）。
    返回 {"started": bool, "report": [...]}：
      started=False 表示没有可刷的基金（codes 为空）；
      report 每项 {code, action, before, after}，action 见 _ACTION_CN。
    单只失败不影响其他只，失败时库中旧净值原样保留（由数据源层保证）。
    """
    codes = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    if not codes:
        return {"started": False, "report": []}
    report = [{"code": c, "action": "pending"} for c in codes]
    _fetch_phase(report, notify, mode)
    return {"started": True, "report": report}


def _fetch_phase(report, notify, mode):
    """联网阶段：逐只真实拉取。失败只影响该只，旧数据不动。

    整个拉取过程持 NAV_REFRESH_LOCK（V8 引擎线程不安全，必须串行）。
    先一次性读好各只"联网前的净值日期"（不跨联网持有连接），
    拉完再读一次对比，判断这次有没有拿到更新的净值——只用于展示与日志，
    不参与任何"要不要联网"的决定。
    """
    before_map = {}
    conn = dbm.connect()
    try:
        for entry in report:
            before_map[entry["code"]] = _latest_nav_date(conn, entry["code"])
    finally:
        conn.close()

    changed, failed = [], []
    netted = 0  # 本阶段"确实联网且请求成功返回"的只数（不看有没有新净值）
    with NAV_REFRESH_LOCK:
        for entry in report:
            code = entry["code"]
            entry["before"] = before_map.get(code)
            try:
                src.refresh_fund_nav(code)
                netted += 1
                conn = dbm.connect()
                try:
                    after = _latest_nav_date(conn, code)
                finally:
                    conn.close()
                entry["after"] = after
                before = entry.get("before")
                if after and (before is None or after > before):
                    entry["action"] = "advanced"
                    changed.append(code)
                else:
                    # 数据源上还没有更新的净值：保留旧净值，绝不伪造新日期
                    entry["action"] = "no_new"
            except Exception as e:
                print(f"[refresher] 刷新净值失败 {code}（旧数据保留）: {e}")
                entry["action"] = "failed"
                failed.append(code)

        if netted:
            # 确实联网检查过（哪怕没有新净值）→ 记"最近检查时间"；
            # 一只都没成功（例如全部失败）就什么都不记，不留假时间
            mark_checked()

    _log_report(report)

    if notify and netted:
        # 只要真的联网检查过就通知界面重取：这样首页"净值最近检查于"会立刻刷新。
        # 没有新净值时数字不变、时间前进，正是要表达的区分。
        notify({"type": "dataUpdated", "ok": len(failed) == 0, "mode": mode,
                "changed": changed, "failed": failed, "at": dbm.now_str()})


def _log_report(report):
    """逐只打印本轮检查结果，供用户查看刷新器在做什么"""
    if not VERBOSE:
        return
    for e in report:
        extra = ""
        if e.get("before") or e.get("after"):
            extra = f"  （净值日期 {e.get('before') or '—'} → {e.get('after') or '—'}）"
        print(f"[refresher]     · {e['code']}  {_ACTION_CN.get(e['action'], e['action'])}{extra}")


# ====================== 三个任务 ======================

def _task_calendar():
    """交易日历：交给统一日历服务，自带本地缓存。

    "刷新周期到了" ≠ "必须重新下载整份日历"：ensure_calendar 内部按市场判断
    （每个市场每天最多联网一次，已有缓存直接用），所以这里每轮调用是安全的，
    失败也会保留旧日历、不抛异常。
    """
    conn = dbm.connect()
    try:
        r = mcal.ensure_calendar(conn)
    finally:
        conn.close()
    detail = r.get("detail") or ""
    if r.get("imported"):
        return f"导入内置年份 {'/'.join(r['imported'])} {detail}".strip()
    updated = {k: v for k, v in r.get("updated", {}).items() if v not in ("ok", "ok_noop")}
    return "无变化（使用本地缓存）" if not updated else f"{updated}"


def _task_nav(notify, mode="auto"):
    """基金净值：逐只真实联网（不再判断"需不需要"，每一轮都拉）"""
    codes = holding_codes()
    if not codes:
        return "没有持仓，跳过（记不了检查时间，这是正常的）"
    report = refresh_nav(codes, notify=notify, mode=mode)["report"]
    ok = [e["code"] for e in report if e["action"] != "failed"]
    fresh = [e["code"] for e in report if e["action"] == "advanced"]
    failed = len(codes) - len(ok)
    if not ok:
        return f"共 {len(codes)} 只，全部失败（旧净值保留，下一轮再试）"
    line = f"共 {len(codes)} 只：已联网检查 {len(ok)} 只"
    line += f"，其中 {len(fresh)} 只有更新的净值" if fresh else "（数据源暂无更新的净值）"
    return line + (f"，{failed} 只失败" if failed else "")


def _task_quotes(api):
    """市场行情：每一轮都真实联网（休市/节假日也拉一次，确认能取到的最新行情）。

    休市期间拿到的通常仍是上一交易日收盘数据，这是正常的：数据原样保留，
    交易状态仍由交易日历判断，本任务不改任何状态。
    """
    api.refresh_market_quotes()
    return "已发起联网刷新"


def _task_news(notify):
    """市场事件：抓官方源 → 去重 → 与真实持仓关联 → 入库；失败保留旧数据。

    阶段一只接 SEC EDGAR + 美联储 RSS；抓取失败只更新数据源状态，
    不删除、不改写已有新闻，页面会显示上次成功时间。
    """
    conn = dbm.connect()
    try:
        r = news_service.refresh_all(conn, notify=notify)
    finally:
        conn.close()
    msg = f"新增 {r['new']} 条，关联 {r['matched']} 条"
    if r["errors"]:
        msg += f"；{len(r['errors'])} 个源有错误（旧数据保留）"
    return msg


# ====================== tick 与常驻循环 ======================

def tick(api, notify=None, n=None, log=None):
    """一个刷新节拍：日历 → 净值 → 行情。三者互相独立，任一失败不影响其他。"""
    log = VERBOSE if log is None else log
    if not _TICK_LOCK.acquire(blocking=False):
        if log:
            print("[refresher] 上一轮刷新还没结束，本轮跳过")
        return {"skipped": True}
    try:
        first = (n == 1)
        if log:
            print(f"[refresher] ── {'启动刷新' if first else '定时刷新'} "
                  f"#{n if n is not None else '-'}  {dbm.now_str()} ──")
        out = {}
        for name, fn in (("日历", _task_calendar),
                         ("净值", lambda: _task_nav(notify)),
                         ("行情", lambda: _task_quotes(api)),
                         ("市场事件", lambda: _task_news(notify))):
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
            tick(api, notify, n)      # n=1 即"启动刷新"：启动后立刻就做，不受时间影响
        except Exception as e:        # 兜底：tick 内部已逐任务隔离，这里只是最后一道
            print(f"[refresher] tick 异常（不影响后续）: {e}")
        time.sleep(max(1.0, tick_seconds - (time.monotonic() - t0)))


def start(api, notify=None, tick_seconds=None):
    """启动统一后台刷新器（常驻守护线程）：**立即刷新一次**，之后每 tick_seconds 秒一次。

    重复调用只会启动一次。notify 传 Api._notify_js 即可（完成时通知界面重取）。
    """
    global _STARTED
    if _STARTED:
        return False
    _STARTED = True
    seconds = tick_seconds or TICK_SECONDS
    print(f"[refresher] 统一刷新器已启动：启动立即刷新一次，之后每 {seconds} 秒"
          f"（{seconds // 60} 分钟）一次")
    threading.Thread(target=_loop, args=(api, notify, seconds),
                     daemon=True, name="xltc-refresher").start()
    return True
