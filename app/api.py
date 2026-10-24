# -*- coding: utf-8 -*-
"""pywebview js_api —— 网页界面与 Python 数据层之间的唯一接口

规则（2026-09-03 用户确认的界面方案 A 边界 + 2026-09-04 三细节）：
- 界面只通过 window.pywebview.api.xxx() 取数/写数；
- 页面效果与操作流程保持已验收原型不变，仅替换数据来源；
- 金额/份额的"显示值"由界面用整数分/整数字符串直接拼，避免展示层浮点误差：
  金额一律返回整数分（cents），界面 fmtMoney() 纯整数格式化；
  份额/净值/百分比返回 Python Decimal 算好的展示字符串。
- 净值"三要素"原则（2026-09-04 用户确认）：净值日期（哪天）、获取时间（何时
  抓到的）、数据来源（哪来的）必须区分清楚，不得让用户把抓取时刻当成实时价格；
- 自动更新 = 检查本地状态，需要才联网。判定规则已统一收敛到 refresher 模块
  （2026-09-25 起：按基金逐个判断时段/退避/次数，不再用全局"当天一次"标记）；
  失败不清空/不覆盖旧数据，用户随时可手动重试。

本文件按「我的基金」页改造起步提供接口，随逐页改造扩充。
"""
import datetime
import json
import threading
import uuid

from decimal import Decimal, ROUND_HALF_UP

from . import converters as cv
from . import db as dbm
from . import market_cal as mcal
from . import refresher
from .refresher import NAV_REFRESH_LOCK, QUOTE_REFRESH_LOCK
from .sources import fund_list, fund_nav as src
from .sources import market_quotes as mq

# 持仓记录/复盘允许前端直接更新的文本字段白名单
HOLDING_TEXT_FIELDS = {"buy_reason", "buy_judge", "watch_points", "remark"}

# 页面显示用的数据源名称（source 字段存内部标识，展示名在此映射，换源时只改这里）
SOURCE_LABELS = {"akshare": "天天基金（公开接口）", "manual": "手动建档",
                 "tencent": "腾讯行情", "sina": "新浪行情"}

# 通知偏好（2026-09-26 用户重新明确）——真实来源唯一：SQLite settings 表；
# 值统一 '1'/'0'（沿用 settings 表既有约定），键不存在 = 从未设置过。
#   notify_first_asked    是否已完成"首次通知询问"（全软件只有一个，问过就不再自动问）
#   notify_news_enabled   市场事件通知开关  ┐ 这两个相互独立：各自页面只改自己的键，
#   notify_alerts_enabled 盯盘提醒通知开关  ┘ 只有"首次询问"那一次会把两者设成同一个值
#   notify_news_follow / notify_news_discover / notify_news_event
#       市场事件页内三个分区（我的关注 / 市场发现 / 重大市场事件），同样住进 SQLite
NOTIFY_KEYS = {
    "first_asked": "notify_first_asked",
    "news_enabled": "notify_news_enabled",
    "alerts_enabled": "notify_alerts_enabled",
    "news_follow": "notify_news_follow",
    "news_discover": "notify_news_discover",
    "news_event": "notify_news_event",
}


class Api:
    """网页 JS 可调用的对象；pywebview 会把它的方法暴露为 window.pywebview.api.*"""

    # ---------- 健康检查 ----------
    def ping(self):
        return "pong"

    # ---------- 首页：市场速览 + 市场会话（2026-09-05，共用统一日历服务） ----------
    def get_market_quotes(self, codes=None):
        """市场速览行情（首页 8 指数等）。codes 缺省 = market_meta 全部启用对象。

        每项 {code, name, market, price_str, pct_str, prev_str,
              quote_time(行情时间), fetched_at(获取时间), source_label, available}
        从未成功拉过（库内无行）→ available=False 各项 None，界面显示"暂不可用"；
        绝不用示例数据冒充（用户 2026-09-05 口径）。
        """
        conn = dbm.connect()
        try:
            if codes:
                qmarks = ",".join("?" * len(codes))
                rows = conn.execute(
                    f"SELECT m.code, m.name, m.market, q.price_scaled, q.change_pct_scaled,"
                    f" q.prev_close_scaled, q.quote_time, q.fetched_at, q.source "
                    f"FROM market_meta m LEFT JOIN market_quotes q ON q.mkt_code = m.code "
                    f"WHERE m.code IN ({qmarks}) AND m.enabled = 1 "
                    f"ORDER BY m.display_order", (*codes,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT m.code, m.name, m.market, q.price_scaled, q.change_pct_scaled,"
                    " q.prev_close_scaled, q.quote_time, q.fetched_at, q.source "
                    "FROM market_meta m LEFT JOIN market_quotes q ON q.mkt_code = m.code "
                    "WHERE m.enabled = 1 ORDER BY m.display_order").fetchall()
            items = []
            for r in rows:
                it = {"code": r["code"], "name": r["name"], "market": r["market"],
                      "available": r["price_scaled"] is not None}
                if it["available"]:
                    it["price_str"] = _price2_str(r["price_scaled"])
                    it["pct_str"] = _pct2_str(r["change_pct_scaled"])
                    it["prev_str"] = _price2_str(r["prev_close_scaled"])
                    it["quote_time"] = r["quote_time"] or None
                    it["fetched_at"] = r["fetched_at"]
                    it["source_label"] = SOURCE_LABELS.get(r["source"], r["source"])
                else:
                    for k in ("price_str", "pct_str", "prev_str", "quote_time", "fetched_at", "source_label"):
                        it[k] = None
                items.append(it)
            return {"items": items}
        finally:
            conn.close()

    def refresh_market_quotes(self, codes=None):
        """手动刷新行情（后台线程）：腾讯主源→新浪备用→入库；完成派发
        dataUpdated {type: 'marketQuotesUpdated'}，界面重取。失败保留旧值不覆盖。"""
        conn = dbm.connect()
        try:
            if codes:
                qmarks = ",".join("?" * len(codes))
                rows = conn.execute(
                    f"SELECT code FROM market_meta WHERE enabled = 1 AND code IN ({qmarks})",
                    (*codes,)).fetchall()
            else:
                rows = conn.execute("SELECT code FROM market_meta WHERE enabled = 1").fetchall()
            all_codes = [r["code"] for r in rows]
        finally:
            conn.close()
        if not all_codes:
            return {"ok": True, "skipped": True}
        threading.Thread(target=self._quote_worker, args=(all_codes,), daemon=True).start()
        return {"ok": True}

    def _quote_worker(self, codes):
        """行情刷新线程（统一刷新器与界面手动按钮共用）。

        同一时刻只跑一轮：已经有一轮在跑时直接退出 —— 那一轮完成时会派发同样的
        marketQuotesUpdated 事件，界面照样会更新，重复联网没有意义。
        """
        if not QUOTE_REFRESH_LOCK.acquire(blocking=False):
            return
        try:
            conn = dbm.connect()
            try:
                res = mq.refresh_market_quotes(conn, codes)
                stale_codes = list(res["stale"].keys())
            finally:
                conn.close()
        finally:
            QUOTE_REFRESH_LOCK.release()
        self._notify_js({"type": "marketQuotesUpdated", "ok": len(stale_codes) == 0,
                         "stale": stale_codes, "at": dbm.now_str()})

    def get_market_sessions(self):
        """三市场会话状态（交易日历统一服务的唯一界面出口，盯盘提醒页复用）。

        返回 {checked_at, order: [市场序（交易中的市场前置）],
              markets: {cn/hk/us: {state, label, note, date}},
              summary: 一句话市场状态, warnings: [异常说明]}
        state ∈ trading/preopen/closed/holiday/noon_break/no_data
        label ∈ 交易中/未开盘/已收盘/休市/无法判断；note 为小白可读小字；
        no_data = 官方日历缺失（未公布年份）→ 显式"无法判断"，绝不猜。
        """
        conn = dbm.connect()
        try:
            return _market_sessions(conn)
        finally:
            conn.close()

    def get_today_alerts(self):
        """首页「今日提醒」卡（MVP 真实数据：提醒引擎尚未接线时即为真实空态）。

        返回 {enabled_count, today: [{id, obj_name, cond_type, direction, cond_value,
              note, triggered_at, actual_value, notify_state, acked}]}
        today = 今日（trigger_date=今天）的触发记录；没有触发 → 空数组，界面显示空态；
        enabled_count = 启用中的提醒规则数（引导用户去盯盘提醒页）。
        """
        conn = dbm.connect()
        try:
            today = dbm.today_str()
            rows = conn.execute(
                "SELECT a.id, a.obj_type, a.cond_type, a.direction, a.cond_value, a.note,"
                " COALESCE(f.name, m.name, a.fund_code, a.mkt_code) AS obj_name,"
                " e.trigger_date, e.triggered_at, e.actual_value, e.notify_state, e.acked"
                " FROM alerts a"
                " LEFT JOIN alert_events e ON e.alert_id = a.id AND e.trigger_date = ?"
                " LEFT JOIN fund_meta f ON f.code = a.fund_code"
                " LEFT JOIN market_meta m ON m.code = a.mkt_code"
                " WHERE a.deleted = 0 AND e.alert_id IS NOT NULL"
                " ORDER BY e.triggered_at DESC, a.id", (today,)).fetchall()
            items = [{"id": r["id"], "obj_name": r["obj_name"], "cond_type": r["cond_type"],
                      "direction": r["direction"], "cond_value": r["cond_value"],
                      "note": r["note"] or "", "triggered_at": r["triggered_at"],
                      "actual_value": r["actual_value"], "notify_state": r["notify_state"],
                      "acked": bool(r["acked"])} for r in rows]
            enabled = conn.execute(
                "SELECT COUNT(*) c FROM alerts WHERE deleted = 0 AND enabled = 1").fetchone()["c"]
            return {"enabled_count": enabled, "today": items}
        finally:
            conn.close()

    # ---------- 通知偏好（2026-09-26 用户重新明确；SQLite settings 是唯一真实来源） ----------
    def get_notify_settings(self):
        """读取全部通知偏好，原样返回 settings 表里的值（'1'/'0'；未设置过 = None）。

        界面拿到 None 表示"这一项从没设置过"（例如首次询问还没发生），
        由界面按产品默认值决定怎么显示——Python 不替用户猜。
        """
        conn = dbm.connect()
        try:
            return {short: dbm.get_setting(conn, key) for short, key in NOTIFY_KEYS.items()}
        finally:
            conn.close()

    def set_notify_settings(self, patch):
        """只写 patch 里出现的键，其余键一律不动，写完返回最新全量状态。

        "市场事件页只改 notify_news_enabled、盯盘提醒页只改 notify_alerts_enabled"
        这条规则由这里保证：调用方没传的键，本方法碰都不碰（不存在连带的可能）。
        未知键直接忽略，不写库。
        """
        if not isinstance(patch, dict):
            print(f"[api] set_notify_settings 参数不是对象，已忽略：{patch!r}")
            return self.get_notify_settings()
        conn = dbm.connect()
        try:
            for short, value in patch.items():
                key = NOTIFY_KEYS.get(short)
                if not key:
                    continue                      # 未知键忽略（防止把别的设置写坏）
                dbm.set_setting(conn, key, "1" if _truthy(value) else "0")
            conn.commit()
            return {short: dbm.get_setting(conn, key) for short, key in NOTIFY_KEYS.items()}
        finally:
            conn.close()

    def ask_notify_first(self, enabled):
        """首次询问的落点：**唯一**会把两个开关一起设成同一个值的地方。

        enabled=True → 市场事件通知、盯盘提醒通知都开；False → 都关；
        无论选哪个，都记下 notify_first_asked='1'，以后任何页面都不再自动询问。
        """
        return self.set_notify_settings({
            "first_asked": 1,
            "news_enabled": 1 if _truthy(enabled) else 0,
            "alerts_enabled": 1 if _truthy(enabled) else 0,
        })

    # ---------- 我的基金页 ----------
    def get_holdings(self):
        """全部持仓（含已清仓历史），每行附真实计算值。

        返回分组（与已验收 funds.html 的 持有/关注/历史 概念对应）：
        {"holdings": [...], "watch": [...], "history": [...],
         "summary": {...}, "last_fetch_at": "YYYY-MM-DD HH:MM:SS"|None}
        - holding 行：id, fund_code, name, fund_type, tag, status, open_date,
          文本记录四字段, cost_cents(int), shares_str, nav_date, nav_str,
          fetched_at, source, market_value_cents, profit_cents, today_profit_cents,
          today_pct_str, profit_pct_str（无净值时数值为 None）
        - history 行：id, fund_code, name, fund_type, open_date, closed_at,
          cost_cents, sell_cents, profit_cents, profit_pct_str, 买入记录+清仓复盘八文本
        - summary：count/total_cents/cost_cents/profit_cents/today_profit_cents/
          profit_pct_str/today_pct_str（Decimal 精确求和，供资产总览直接显示）
        - last_fetch_at：最近一次成功联网获取净值的时刻（app_meta，全局动作时间，
          我的基金页"三要素"在用）
        - last_check_at：最近一次真正联网**检查**净值的时刻（首页"净值最近检查于"用）。
          与 last_fetch_at 的区别：检查过但没有新净值时它也会前进，因为确实查了
        """
        conn = dbm.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM holdings WHERE deleted = 0 ORDER BY "
                " CASE status WHEN 'holding' THEN 0 WHEN 'watch' THEN 1 ELSE 2 END, open_date"
            ).fetchall()
            names = {}  # 基金代码 → 档案（一次查询避免逐行查库）
            result = {"holdings": [], "watch": [], "history": []}
            for r in rows:
                h = dict(r)
                code = h["fund_code"]
                if code not in names:
                    m = conn.execute("SELECT name, fund_type FROM fund_meta WHERE code = ?", (code,)).fetchone()
                    names[code] = dict(m) if m else {"name": code, "fund_type": ""}
                h["name"] = names[code]["name"]
                h["fund_type"] = names[code]["fund_type"] or ""
                h.pop("deleted", None)
                st = h["status"]
                if st == "holding":
                    self._attach_live(conn, h)
                    result["holdings"].append(h)
                elif st == "watch":
                    for k in ("cost_cents", "shares_scaled", "sell_cents", "profit_cents",
                              "closed_at", "clear_reason", "clear_judge", "clear_good", "clear_review"):
                        h.pop(k, None)
                    result["watch"].append(h)
                else:  # closed 历史持仓
                    self._attach_history(h)
                    result["history"].append(h)
            result["summary"] = self._summary(conn, result["holdings"])
            result["last_fetch_at"] = dbm.get_app_meta(conn, "last_success_fetch_at")
            # 首页"净值最近检查于"读这个。旧库还没写过新键时回退到 last_fetch_at——
            # 它同样只在联网成功后才写，语义一致，不是伪造时间，避免首页一开始空着。
            result["last_check_at"] = (dbm.get_app_meta(conn, refresher.CHECK_KEY)
                                       or result["last_fetch_at"])
            return result
        finally:
            conn.close()

    def _attach_live(self, conn, h):
        """持有行附加：三要素（净值日/获取时间/来源）+ 精确计算值 + 展示字符串"""
        code = h["fund_code"]
        cost_c = h.pop("cost_cents", None)
        shares_s = h.pop("shares_scaled", None)
        h["cost_cents"] = cost_c
        h["shares_str"] = _shares_str(shares_s)
        nav = src.latest_nav(conn, code)
        if nav is None:
            h["nav_date"] = h["nav_str"] = h["fetched_at"] = h["source"] = None
            h["market_value_cents"] = h["profit_cents"] = None
            h["today_profit_cents"] = h["today_pct_str"] = h["profit_pct_str"] = None
            return h
        h["nav_date"] = nav["nav_date"]
        h["nav_str"] = str(cv.nav_from_scaled(nav["unit_nav_scaled"]))
        h["fetched_at"] = nav["fetched_at"]
        h["source"] = SOURCE_LABELS.get(nav["source"], nav["source"])
        h["market_value_cents"] = src.market_value_cents(shares_s, nav["unit_nav_scaled"])
        h["profit_cents"] = src.profit_cents(shares_s, nav["unit_nav_scaled"], cost_c)
        pct = nav.get("daily_pct_scaled")
        h["today_pct_str"] = _pct2_str(pct) if pct is not None else None
        prev = conn.execute(
            "SELECT unit_nav_scaled FROM fund_nav_daily "
            "WHERE fund_code = ? AND nav_date < ? ORDER BY nav_date DESC LIMIT 1",
            (code, nav["nav_date"]),
        ).fetchone()
        if prev is not None:
            h["today_profit_cents"] = src.today_profit_cents(
                shares_s, nav["unit_nav_scaled"], prev["unit_nav_scaled"])
        else:
            h["today_profit_cents"] = None
        h["profit_pct_str"] = _pct1_str(h["profit_cents"], cost_c)
        return h

    @staticmethod
    def _attach_history(h):
        """历史行附加展示字段（金额给分、百分比给字符串，界面不再算账）"""
        h["cost_cents"] = h.pop("cost_cents", None)
        h["sell_cents"] = h.pop("sell_cents", None)
        h["profit_cents"] = h.pop("profit_cents", None)
        h["profit_pct_str"] = _pct1_str(h["profit_cents"], h["cost_cents"])
        h.pop("shares_scaled", None)
        return h

    @staticmethod
    def _summary(conn, holdings):
        """资产总览汇总（整数分求和 + Decimal 算百分比，全在 Python 侧）。

        语义：任何持仓都还没有净值时，total/profit/today 一律 None（界面显示 —，
        避免把"还没算出来"显示成 0 元误导）；成本 cost_cents 恒有值。
        """
        s = {"count": len(holdings), "cost_cents": 0,
             "total_cents": None, "profit_cents": None, "today_profit_cents": None,
             "profit_pct_str": None, "today_pct_str": None}
        mv_any = False
        total = cost = profit = 0
        today = 0
        today_n = 0
        for h in holdings:
            cost += h["cost_cents"] or 0
            mv = h["market_value_cents"]
            if mv is not None:
                mv_any = True
                total += mv
                profit += h["profit_cents"] or 0
            if h["today_profit_cents"] is not None:
                today += h["today_profit_cents"]
                today_n += 1
        s["cost_cents"] = cost
        if mv_any:
            s["total_cents"] = total
            s["profit_cents"] = profit
            s["profit_pct_str"] = _pct1_str(profit, cost)
        if today_n:
            s["today_profit_cents"] = today
            # 今日盈亏% = 今日盈亏 / 昨日市值（与已验收原型口径一致；分母仅计有净值部分）
            if mv_any and (total - today) > 0:
                s["today_pct_str"] = _pct2_str_for(total - today, today)
        return s

    # ---------- 基金搜索 ----------
    def search_fund(self, keyword):
        """按名称/代码搜索全市场真实基金（首次联网加载名单约几秒，此后走缓存）；
        网络失败返回 {"items": [], "offline": true}，界面如实提示。"""
        try:
            return {"items": fund_list.search(keyword, 20), "offline": False}
        except Exception:
            return {"items": [], "offline": True}

    # ---------- 持仓操作 ----------
    def get_fund_snapshot(self, code):
        """选中基金后取“最近公布的真实净值”，供添加表单预览。

        - 本地缓存已经是今天的净值 → 直接用，不再联网；
        - 否则同步联网刷新一次（与手动刷新同一把锁）；失败则回退到本地已有净值；
        - 两者都没有 → 明确返回失败，绝不返回虚构数字。
        返回 {ok, nav_str, nav_date, fetched_at, source, stale_warning?}
        """
        code = str(code or "").strip()
        if not code:
            return {"ok": False, "error": "基金代码为空"}

        conn = dbm.connect()
        try:
            cached = src.latest_nav(conn, code)
        finally:
            conn.close()

        warning = None
        if not (cached and cached["nav_date"] == dbm.today_str()):
            r = self.refresh_nav(code)
            if not r.get("ok"):
                warning = r.get("error") or "联网获取净值失败"
            conn = dbm.connect()
            try:
                cached = src.latest_nav(conn, code)
            finally:
                conn.close()

        if cached is None:
            return {"ok": False, "error": warning or "暂时没有该基金的真实净值"}
        out = {
            "ok": True,
            "nav_str": str(cv.nav_from_scaled(cached["unit_nav_scaled"])),
            "nav_date": cached["nav_date"],
            "fetched_at": cached["fetched_at"],
            "source": SOURCE_LABELS.get(cached["source"], cached["source"]),
        }
        if warning:
            out["stale_warning"] = warning
        return out

    def add_holding(self, payload):
        """添加一笔持仓（2026-10-21 起表单改为“成本价 + 份额”，后端仍存总本金）

        payload: {fund_code, name(建档用), fund_type,
                  cost_price(元/份) 或 cost_yuan(总本金，兼容旧调用), shares,
                  open_date 'YYYY-MM-DD', tag 'heavy|normal|watch',
                  buy_reason, buy_judge, watch_points, remark}
        - cost_price × shares → 总本金（分，ROUND_HALF_UP），写入 holdings.cost_cents；
          数据库字段含义不变，已有收益计算/清仓/流水全部复用。
        - 无金额无份额 → 存为仅关注(watch)；否则存为持有(holding)。
        - 返回 {"ok": true, "id": ..} 或 {"ok": false, "error": ".."}；净值拉取后台自动进行。
        """
        try:
            code = str(payload.get("fund_code", "")).strip()
            if not code:
                return {"ok": False, "error": "基金代码不能为空"}
            cost_yuan = payload.get("cost_yuan")
            cost_price = payload.get("cost_price")
            shares = payload.get("shares")
            tag = payload.get("tag", "normal")

            has_cost = (cost_yuan not in (None, "")) or (cost_price not in (None, ""))
            has_shares = shares not in (None, "")
            is_watch = not has_cost and not has_shares
            cost_c = shares_s = None
            if not is_watch:
                if not has_shares:
                    return {"ok": False, "error": "请填写持有份额"}
                shares_s = cv.shares_to_scaled(shares)
                if shares_s <= 0:
                    return {"ok": False, "error": "持有份额必须大于 0"}

                if cost_yuan not in (None, ""):
                    # 兼容旧调用：直接给总本金（元）
                    cost_c = cv.yuan_to_fen(cost_yuan)
                elif cost_price not in (None, ""):
                    # 新表单：持仓成本价（元/份）× 份额 → 总本金，按分四舍五入
                    price = Decimal(str(cost_price))
                    if price <= 0:
                        return {"ok": False, "error": "持仓成本价必须大于 0"}
                    total_yuan = (price * cv.shares_from_scaled(shares_s)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP)
                    cost_c = cv.yuan_to_fen(str(total_yuan))
                else:
                    return {"ok": False, "error": "请填写持仓成本价"}

                if cost_c <= 0:
                    return {"ok": False, "error": "投入成本必须大于 0"}
            now = dbm.now_str()
            conn = dbm.connect()
            try:
                # 1) 基金档案：库里没有则建档（名称/类型优先取搜索结果）
                if conn.execute("SELECT 1 FROM fund_meta WHERE code = ?", (code,)).fetchone() is None:
                    conn.execute(
                        "INSERT OR IGNORE INTO fund_meta (code, name, fund_type, source, fetched_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, 'manual', ?, ?, ?)",
                        (code, (payload.get("name") or code).strip(), payload.get("fund_type") or "",
                         now, now, now),
                    )
                # 2) 写持仓行
                cur = conn.execute(
                    "INSERT INTO holdings (uuid, fund_code, status, tag, cost_cents, shares_scaled, open_date,"
                    " buy_reason, buy_judge, watch_points, remark, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), code, "watch" if is_watch else "holding", tag,
                     cost_c, shares_s, payload.get("open_date") or None,
                     payload.get("buy_reason"), payload.get("buy_judge"),
                     payload.get("watch_points"), payload.get("remark"), now, now),
                )
                hid = cur.lastrowid
                # 3) 买入流水（持有才记金额；watch 不产生流水）
                if not is_watch:
                    conn.execute(
                        "INSERT INTO invest_events (uuid, holding_id, event_type, event_date, amount_cents,"
                        " shares_scaled, note, created_at) VALUES (?, ?, 'buy', ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), hid, payload.get("open_date") or dbm.today_str(),
                         cost_c, shares_s, "添加持仓", now),
                    )
                conn.commit()
            finally:
                conn.close()
            # 4) 净值拉取（后台线程，不阻塞界面）
            self._spawn_nav_refresh([code])
            return {"ok": True, "id": hid}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"保存失败：{e}"}

    def set_holding_tag(self, hid, tag):
        """切换 重仓/普通/仅关注 标记（已验收：点击循环切换）"""
        if tag not in ("heavy", "normal", "watch"):
            return {"ok": False, "error": "未知标记"}
        conn = dbm.connect()
        try:
            conn.execute("UPDATE holdings SET tag = ?, updated_at = ? WHERE id = ?", (tag, dbm.now_str(), hid))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def update_holding_texts(self, hid, fields):
        """更新持仓记录/备注文本（白名单字段，仅记录性内容）"""
        sets, vals = [], []
        for k, v in fields.items():
            if k in HOLDING_TEXT_FIELDS:
                sets.append(f"{k} = ?")
                vals.append(v if v is not None else "")
        if not sets:
            return {"ok": True}
        vals.append(dbm.now_str())
        conn = dbm.connect()
        try:
            conn.execute(f"UPDATE holdings SET {', '.join(sets)}, updated_at = ? WHERE id = ?", (*vals, hid))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def close_holding(self, hid, clear_reason, clear_judge, clear_good, clear_review):
        """清仓（已验收流程）：按最新净值自动算卖出金额与最终收益，状态转 closed，
        写卖出流水；复盘四问文本由界面提供。无净值时拒绝（先刷新）。"""
        conn = dbm.connect()
        try:
            row = conn.execute("SELECT * FROM holdings WHERE id = ? AND deleted = 0", (hid,)).fetchone()
            if row is None or row["status"] != "holding":
                return {"ok": False, "error": "该持仓不存在或已清仓"}
            nav = src.latest_nav(conn, row["fund_code"])
            if nav is None:
                return {"ok": False, "error": "还没有净值数据，请先刷新净值再清仓"}
            sell_c = src.market_value_cents(row["shares_scaled"], nav["unit_nav_scaled"])
            profit_c = sell_c - row["cost_cents"]
            now = dbm.now_str()
            conn.execute(
                "UPDATE holdings SET status = 'closed', closed_at = ?, sell_cents = ?, profit_cents = ?,"
                " clear_reason = ?, clear_judge = ?, clear_good = ?, clear_review = ?, updated_at = ? WHERE id = ?",
                (dbm.today_str(), sell_c, profit_c, clear_reason, clear_judge, clear_good, clear_review, now, hid),
            )
            conn.execute(
                "INSERT INTO invest_events (uuid, holding_id, event_type, event_date, amount_cents,"
                " shares_scaled, note, created_at) VALUES (?, ?, 'sell', ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), hid, dbm.today_str(), sell_c, row["shares_scaled"], "清仓", now),
            )
            conn.commit()
            return {"ok": True, "sell_cents": sell_c, "profit_cents": profit_c}
        except Exception as e:
            return {"ok": False, "error": f"清仓失败：{e}"}
        finally:
            conn.close()

    def delete_holding(self, hid):
        """彻底删除一笔（含历史）：软删标记（云同步墓碑预留），流水不可再回看"""
        conn = dbm.connect()
        try:
            conn.execute("UPDATE holdings SET deleted = 1, updated_at = ? WHERE id = ?", (dbm.now_str(), hid))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    # ---------- 净值刷新（统一后台刷新器 + 手动） ----------
    # 2026-09-26 用户重新明确刷新需求后，这里删除了原 maybe_auto_refresh
    # （"打开页面时的检查式自动更新"入口）。原因：净值刷新已改为不再有任何跳过规则
    # （每次调用都真实联网），而统一刷新器已经负责"启动立即刷新 + 每 30 分钟一次"；
    # 若仍保留"打开页面就检查"，用户在页面之间来回切换会反复触发整段历史净值的
    # 真实联网，属于重复刷新。页面现在只做三件事：首屏读本地数据、用户手动点刷新、
    # 收到 Python 完成事件后重取一次。

    def refresh_all_nav(self, codes, mode="manual"):
        """手动刷新（界面按钮）：立即后台联网更新，完成时派发 dataUpdated 事件"""
        codes = _clean_codes(codes)
        if codes:
            threading.Thread(target=self._refresh_worker, args=(codes, mode), daemon=True).start()
        return {"ok": True}

    def refresh_nav(self, code):
        """立即联网刷新一只基金净值（带锁同步，前端显示加载态），返回本次写入行数"""
        with NAV_REFRESH_LOCK:
            try:
                return {"ok": True, "rows": src.refresh_fund_nav(code)}
            except Exception as e:
                return {"ok": False, "error": f"刷新失败：{e}"}

    def _refresh_worker(self, codes, mode):
        """后台刷新线程：逐只更新，失败只记该只；成功后把动作时间记入
        app_meta.last_success_fetch_at（界面"何时获取"依据）；完成派发事件。
        任何情况下都不改动库里已有的旧净值（更新失败 = 旧数据原样保留）。"""
        failed = []
        with NAV_REFRESH_LOCK:
            for code in codes:
                try:
                    src.refresh_fund_nav(code)
                except Exception as e:
                    print(f"[api] 刷新净值失败 {code}: {e}")
                    failed.append(code)
        ok_count = len(codes) - len(failed)
        now = dbm.now_str()
        conn = dbm.connect()
        try:
            if ok_count > 0:
                dbm.set_app_meta(conn, "last_success_fetch_at", now)
                # 手动/添加流程也确实联了网 → 同步记"最近一次真正联网检查"时刻
                # （首页显示口径，2026-09-26；键名以 refresher 为准，只此一处定义）
                dbm.set_app_meta(conn, refresher.CHECK_KEY, now)
                conn.commit()  # 界面"何时获取"依据，必须持久化
        finally:
            conn.close()
        self._notify_js({
            "type": "dataUpdated",
            "ok": ok_count > 0,
            "mode": mode,            # auto=打开时自动 / manual=用户手动 / add=添加后自动
            "failed": failed,        # 失败的基金代码列表（空 = 全部成功）
            "at": now,               # 本次刷新的动作时刻
        })

    def _spawn_nav_refresh(self, codes):
        threading.Thread(target=self._refresh_worker, args=(codes, "add"), daemon=True).start()

    def _notify_js(self, payload):
        """向所有窗口派发 JS 事件（JSON 对象，如刷新完成 → 前端重取并显示结果）"""
        try:
            import webview
            js = json.dumps(payload, ensure_ascii=False)
            for w in webview.windows:
                w.evaluate_js(f"window.__xltcOnData && window.__xltcOnData({js})")
        except Exception:
            pass


# ====================== 首页市场会话（文案层） ======================
# 判断逻辑全在 market_cal 引擎（session/is_trading_day/next_trade_date），
# 本层只做"小白可读文案"翻译。口径 = 用户 2026-09-05 四题确认：
#   trading→交易中 / preopen→未开盘 / closed→已收盘 / holiday+noon_break→休市
#   us 开盘时间按夏令时/冬令时换算北京时间（今晚 21:30 / 22:30 开盘）；
#   no_data（日历缺失）→ 无法判断，绝不猜测。
_MARKET_NAMES = {"cn": "A股", "hk": "港股", "us": "美股"}
_WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _price2_str(price_scaled):
    """点位缩放整数 → 展示字符串 '4,548.05'（界面直接显示）"""
    return format(cv.point_from_scaled(price_scaled), ",.2f")


def _bj_hm(ny_dt):
    """美东 datetime → 北京时间 HH:MM（DST 自动）"""
    return mcal.bj_from_ny(ny_dt).strftime("%H:%M")


def _market_sessions(conn):
    """三市场会话组装（见 Api.get_market_sessions docstring）"""
    now = datetime.datetime.now()
    out, warnings, active = {}, [], []
    for m in ("cn", "hk", "us"):
        md = mcal.ny_from_beijing(now).date() if m == "us" else now.date()
        t = mcal.is_trading_day(conn, m, md)
        if t is None:  # 日历缺失（官方未公布的年份，如 A股 2027）→ 显式无法判断
            out[m] = {"state": "no_data", "label": "无法判断", "date": md.isoformat(),
                      "note": "官方交易日历尚未公布，联网后自动更新"}
            warnings.append(f"{_MARKET_NAMES[m]} {md} 官方日历尚未公布，暂时无法判断交易状态")
            continue
        is_open, early_close = t
        state, mdate, oa, ca = mcal.session(m, now, is_open, early_close)
        label = {"trading": "交易中", "preopen": "未开盘", "closed": "已收盘",
                 "noon_break": "休市", "holiday": "休市"}[state]
        if m == "us":
            oa_bj = _bj_hm(datetime.datetime.combine(md, datetime.time.fromisoformat(oa)))
            ca_bj = _bj_hm(datetime.datetime.combine(md, datetime.time.fromisoformat(ca)))
            if state == "trading":
                note = f"北京时间 {ca_bj} 收盘"
            elif state == "preopen":
                note = f"今晚 {oa_bj} 开盘"  # 用户确认句式（夏令时 21:30 / 冬令时 22:30 自动）
            elif state == "closed":
                note = f"数据截至北京时间 {ca_bj}"
            else:  # holiday（美股无午休）
                nxt = mcal.next_trade_date(conn, m, md)
                if nxt is None:
                    note = "官方日历尚未公布"
                else:
                    nxt_bj = _bj_hm(datetime.datetime.combine(nxt, datetime.time.fromisoformat(oa)))
                    note = f"下次开盘：{_WEEK_CN[nxt.weekday()]} {nxt_bj}"
        else:
            if state == "trading":
                note = f"{ca} 收盘"
            elif state == "noon_break":
                note = f"午间休市 · {mcal.LUNCH[m][1]} 开盘"
            elif state == "preopen":
                note = f"{oa} 开盘"
            elif state == "closed":
                note = f"当日 {ca} 收盘"
            else:  # holiday
                nxt = mcal.next_trade_date(conn, m, md)
                note = (f"下一交易日 {nxt:%m-%d}（{_WEEK_CN[nxt.weekday()]}）{oa} 开盘"
                        if nxt else "官方日历尚未公布")
        out[m] = {"state": state, "label": label, "note": note, "date": mdate,
                  "is_open_day": bool(is_open)}
        if state == "trading":
            active.append(m)
    order = active + [m for m in ("cn", "hk", "us") if m not in active]
    # summary：一句话"现在市场整体状态"（首页 hero 备用）
    if active:
        summary = " · ".join(f"{_MARKET_NAMES[m]}交易中" for m in order if m in active) + \
                  (" · 其余市场休市/未开盘" if len(active) < 3 else "")
    else:
        summary = "市场均已收盘或休市，显示最近一次收盘数据"
    return {"checked_at": dbm.now_str(), "order": order, "markets": out,
            "summary": summary, "warnings": warnings}


def _clean_codes(codes):
    """去空去重的基金代码列表"""
    seen, out = set(), []
    for c in (codes or []):
        c = str(c).strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _truthy(v):
    """界面传来的真假值统一判定：JSON 的 true/false、数字、'1'/'0' 字符串都认"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _shares_str(shares_scaled):
    """份额缩放整数 → 展示字符串 '23,924.00'（Decimal 格式化，界面直接显示）"""
    if shares_scaled is None:
        return None
    return format(cv.shares_from_scaled(shares_scaled), ",.2f")


def _pct2_str(pct_scaled):
    """涨跌幅缩放整数（×10000）→ 展示字符串 '+1.82' / '-0.50' / '+0.00'"""
    v = cv.pct_from_scaled(pct_scaled).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    s = format(v, "f")
    return s if s.startswith("-") else "+" + s


def _pct1_str(part_cents, whole_cents):
    """占比百分比（分母可能为负/零）：'±x.x'（1 位小数，与已验收原型口径一致）"""
    if part_cents is None or not whole_cents:
        return None
    try:
        v = (Decimal(part_cents) / Decimal(whole_cents) * 100).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP)
    except Exception:
        return None
    s = format(v, "f")
    return s if s.startswith("-") else "+" + s


def _pct2_str_for(base_cents, part_cents):
    """百分比分母非分金额场景（今日盈亏/昨日市值），保留 2 位小数"""
    if not base_cents:
        return None
    try:
        v = (Decimal(part_cents) / Decimal(base_cents) * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None
    s = format(v, "f")
    return s if s.startswith("-") else "+" + s
