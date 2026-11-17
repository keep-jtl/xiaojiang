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
import sys
import threading
import uuid

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from . import converters as cv
from . import alert_engine
from . import autostart
from . import db as dbm
from . import fund_manager
from . import market_cal as mcal
from . import news_service
from . import portfolio_analysis
from . import refresher
from . import system_notify
from .refresher import QUOTE_REFRESH_LOCK
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
    "system_asked": "notify_system_asked",
}

# 盯盘提醒 1A：只做规则真实保存，不做检查引擎/通知/历史。
# 数值原文存 TEXT（见 alerts.cond_value），不需要缩放换算；这里只做校验与规范化。
ALERT_NOTE_MAX = 200
ALERT_COND_TYPES = {"pct", "line"}
ALERT_DIRECTIONS = {"up", "down"}
ALERT_VALID_TYPES = {"once", "until_trigger", "range"}  # ongoing 页面未暴露
MARKET_TYPE_LABELS = {"index": "指数", "sector": "赛道", "commodity": "商品"}


def _alert_enabled_count(conn):
    """启用中的提醒数（首页与提醒页共用，口径必须一致）"""
    return conn.execute(
        "SELECT COUNT(*) c FROM alerts WHERE deleted = 0 AND enabled = 1"
    ).fetchone()["c"]


def _alert_id(value):
    """前端传来的提醒 id → 正整数；无效时给出可读错误"""
    try:
        i = int(value)
    except (TypeError, ValueError):
        raise ValueError("提醒编号不正确")
    if i <= 0:
        raise ValueError("提醒编号不正确")
    return i


def _alert_cond_value(raw):
    """用户输入的条件数值 → 规范化十进制字符串；只接受有限正数。"""
    text = str(raw if raw is not None else "").strip()
    if not text or len(text) > 20:
        raise ValueError("请填写有效的触发数值")
    try:
        d = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("触发数值必须是数字")
    if not d.is_finite() or d <= 0:
        raise ValueError("触发数值必须大于 0")
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if not s or len(s) > 20:
        raise ValueError("触发数值过长")
    return s


def _alert_date(value, label):
    """严格 YYYY-MM-DD → date；空值返回 None。"""
    if value in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{label}格式不正确，应为 YYYY-MM-DD")


def _fund_tag_cls(fund_type):
    t = (fund_type or "").upper()
    return "tag-blue" if ("指数" in t or "QDII" in t) else "tag-green"


def _market_tag_txt(mkt_type):
    return MARKET_TYPE_LABELS.get(mkt_type or "", mkt_type or "指数")


def _alert_same_object(existing, obj_type, fund_code, mkt_code):
    if not existing or existing["obj_type"] != obj_type:
        return False
    if obj_type == "fund":
        return existing["fund_code"] == fund_code
    return existing["mkt_code"] == mkt_code


def _alert_market_for(conn, obj_type, fund_code, mkt_code):
    """提醒对象所属交易日历市场：场外基金按 A 股日历，指数按 market_meta.market。"""
    if obj_type == "fund":
        return "cn"
    row = conn.execute(
        "SELECT market FROM market_meta WHERE code = ?", (mkt_code,)
    ).fetchone()
    return row["market"] if row else None


def _alert_next_trade_date(conn, market, base_date=None):
    """下一交易日（不含基准日）；日历缺失时明确报错，绝不猜日期。"""
    if market not in ("cn", "hk", "us"):
        raise ValueError("该提醒对象没有可用的交易日历")
    if base_date is None:
        now_bj = datetime.datetime.now()
        base_date = mcal.ny_from_beijing(now_bj).date() if market == "us" else now_bj.date()
    mcal.ensure_calendar(conn, markets=(market,), allow_network=False)
    nd = mcal.next_trade_date(conn, market, base_date)
    if nd is None:
        raise ValueError("交易日历缺失，无法计算下一交易日；请改用自定义时间范围")
    return nd


def _alert_validate(conn, payload, existing=None):
    """校验并规范化 create/update 的入参；返回可直接写 alerts 表的 dict。"""
    if not isinstance(payload, dict):
        raise ValueError("提醒参数格式不正确")

    obj_type = str(payload.get("obj_type") or "").strip()
    if obj_type not in ("fund", "mkt"):
        raise ValueError("请选择提醒对象")
    fund_code = str(payload.get("fund_code") or "").strip() or None
    mkt_code = str(payload.get("mkt_code") or "").strip() or None
    if obj_type == "fund":
        mkt_code = None
        if not fund_code:
            raise ValueError("请选择要提醒的基金")
    else:
        fund_code = None
        if not mkt_code:
            raise ValueError("请选择要提醒的指数")

    cond_type = str(payload.get("cond_type") or "").strip()
    if cond_type not in ALERT_COND_TYPES:
        raise ValueError("请选择涨跌幅或数值线条件")
    direction = str(payload.get("direction") or "").strip()
    if direction not in ALERT_DIRECTIONS:
        raise ValueError("请选择涨破/跌破方向")
    cond_value = _alert_cond_value(payload.get("cond_value"))

    valid_type = str(payload.get("valid_type") or "").strip()
    if valid_type == "forever":  # 兼容旧界面字段
        valid_type = "until_trigger"
    if valid_type not in ALERT_VALID_TYPES:
        raise ValueError("请选择有效期")

    same_obj = _alert_same_object(existing, obj_type, fund_code, mkt_code)
    if not same_obj:
        if obj_type == "fund":
            hit = conn.execute(
                "SELECT 1 FROM holdings WHERE fund_code = ? AND deleted = 0 "
                "AND status IN ('holding','watch') LIMIT 1",
                (fund_code,),
            ).fetchone()
            if not hit:
                raise ValueError("该基金已不在我的基金/关注列表中，请先添加或重新选择")
        else:
            hit = conn.execute(
                "SELECT 1 FROM market_meta WHERE code = ? AND enabled = 1", (mkt_code,)
            ).fetchone()
            if not hit:
                raise ValueError("该指数不在可用的预置清单中")

    start = _alert_date(payload.get("start_date"), "开始日期")
    end = _alert_date(payload.get("end_date"), "结束日期")
    today = datetime.date.today()

    if valid_type == "once":
        market = _alert_market_for(conn, obj_type, fund_code, mkt_code)
        if not market:
            raise ValueError("找不到该对象的交易日历")
        # 只改条件/备注时保留原有下一交易日，避免日期被往后推。
        if (existing and existing["valid_type"] == "once" and same_obj
                and existing["start_date"] and existing["end_date"]
                and existing["start_date"] == existing["end_date"]):
            start = datetime.date.fromisoformat(existing["start_date"])
            end = start
        else:
            start = _alert_next_trade_date(conn, market)
            end = start
    elif valid_type == "until_trigger":
        end = None
        if start and start < today:
            raise ValueError("开始日期不能早于今天")
    else:  # range
        if not start or not end:
            raise ValueError("自定义时间范围需要填开始和结束日期")
        if end < start:
            raise ValueError("结束日期不能早于开始日期")
        if end < today:
            raise ValueError("结束日期不能早于今天")

    note = str(payload.get("note") or "").strip()
    if len(note) > ALERT_NOTE_MAX:
        raise ValueError(f"备注不能超过 {ALERT_NOTE_MAX} 字")

    enabled = existing["enabled"] if existing is not None else 1
    if "enabled" in payload:
        enabled = 1 if _truthy(payload.get("enabled")) else 0

    return {
        "obj_type": obj_type,
        "fund_code": fund_code,
        "mkt_code": mkt_code,
        "cond_type": cond_type,
        "direction": direction,
        "cond_value": cond_value,
        "valid_type": valid_type,
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "note": note or None,
        "enabled": int(enabled),
    }


def _alert_rule_vm(row, today):
    """数据库行 → 提醒页现有视图模型（页面渲染函数不需要重写）。"""
    obj_type = row["obj_type"]
    is_fund = obj_type == "fund"
    code = row["fund_code"] if is_fund else row["mkt_code"]
    name = (row["fund_name"] if is_fund else row["mkt_name"]) or code or ""
    fund_type = row["fund_type"] or ""
    mkt_type = row["mkt_type"] or ""
    tag_txt = (fund_type or "基金") if is_fund else _market_tag_txt(mkt_type)
    tag_cls = _fund_tag_cls(fund_type) if is_fund else "tag-blue"
    sub = f"{code} · 我的基金" if is_fund else f"预设清单 · {tag_txt}"

    cond_type = row["cond_type"]
    direction = row["direction"]
    value = row["cond_value"]
    if cond_type == "pct":
        dir_cn = "涨超" if direction == "up" else "跌超"
        cond_core = f"单日{dir_cn} {value}%"
        cond_note = "涨跌幅条件 · 场外基金" if is_fund else "涨跌幅条件 · 指数"
        cond_icon = "📈" if direction == "up" else "📉"
    else:
        dir_cn = "涨破" if direction == "up" else "跌破"
        unit = "元" if is_fund else "点"
        cond_core = f"{dir_cn} {value} {unit}"
        cond_note = "数值线条件 · 基金净值" if is_fund else "数值线条件 · 指数点位"
        cond_icon = "🎯"

    valid = row["valid_type"]
    start = row["start_date"] or ""
    end = row["end_date"] or ""
    obj_zone = "fund" if is_fund else "mkt"
    if valid == "once":
        zone = "plan"
        eff_main = f"下一交易日（{start}）生效"
        eff_sub = "有效期：仅下一交易日 · 系统自动计算"
        valid_ui = "once"
    elif valid == "range":
        zone = "plan" if start and start > today else obj_zone
        eff_main = f"从 {start} 起"
        eff_sub = f"有效期：{start} ~ {end}"
        valid_ui = "range"
    elif valid == "ongoing":
        zone = obj_zone
        eff_main = "长期有效"
        eff_sub = "有效期：长期有效"
        valid_ui = "forever"  # 页面未暴露 ongoing；openEdit 会用 validTypeRaw 拦住
    else:  # until_trigger
        zone = "plan" if start and start > today else obj_zone
        eff_main = f"从 {start} 起" if start and start > today else "立即生效"
        eff_sub = "有效期：持续到触发 · 触发后自动失效"
        valid_ui = "forever"

    note = row["note"] or ""
    return {
        "id": row["id"],
        "zone": zone,
        "objType": obj_type,
        "objCode": code,
        "objName": name,
        "name": name,
        "sub": sub,
        "tagCls": tag_cls,
        "tagTxt": tag_txt,
        "condIcon": cond_icon,
        "condCore": cond_core,
        "condNote": cond_note,
        "effMain": eff_main,
        "effSub": eff_sub,
        "note": note,
        "on": bool(row["enabled"]),
        "back": {
            "objType": obj_type,
            "objCode": code,
            "objName": name,
            "condType": cond_type,
            "dir": dir_cn,
            "num": value,
            "validType": valid_ui,
            "validTypeRaw": valid,
            "dateFrom": start,
            "dateTo": end,
            "note": note,
        },
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
            enabled = _alert_enabled_count(conn)
            return {"enabled_count": enabled, "today": items}
        finally:
            conn.close()

    def get_alert_history(self, limit=100):
        """历史触发记录（真实事件；通知提交结果与用户已读是两个独立状态）。"""
        conn = dbm.connect()
        try:
            try:
                n = int(limit)
            except (TypeError, ValueError):
                n = 100
            n = max(1, min(n, 500))
            rows = conn.execute(
                "SELECT e.id, e.alert_id, e.trigger_date, e.triggered_at, e.cond_snapshot,"
                " e.actual_value, e.trigger_kind, e.notify_state, e.notify_result,"
                " e.notify_error, e.acked, a.obj_type, a.cond_type, a.direction,"
                " COALESCE(f.name, m.name, a.fund_code, a.mkt_code) AS obj_name"
                " FROM alert_events e"
                " JOIN alerts a ON a.id = e.alert_id"
                " LEFT JOIN fund_meta f ON f.code = a.fund_code"
                " LEFT JOIN market_meta m ON m.code = a.mkt_code"
                " ORDER BY e.triggered_at DESC, e.id DESC LIMIT ?", (n,)).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                d["acked"] = bool(r["acked"])
                items.append(d)
            return {"ok": True, "items": items}
        finally:
            conn.close()

    # ---------- 盯盘提醒规则（1A：只做真实保存，不做检查/通知/历史） ----------
    def get_alert_page(self):
        """提醒管理页数据：真实规则 + 真实对象清单 + 启用数。

        规则返回页面现有视图模型形状（zone/name/cond/eff/back 等），
        页面只替换数据源，不重写已验收的渲染结构。
        """
        conn = None
        try:
            conn = dbm.connect()
            today = dbm.today_str()
            rows = conn.execute(
                "SELECT a.*, f.name AS fund_name, f.fund_type AS fund_type,"
                " m.name AS mkt_name, m.mkt_type AS mkt_type"
                " FROM alerts a"
                " LEFT JOIN fund_meta f ON f.code = a.fund_code"
                " LEFT JOIN market_meta m ON m.code = a.mkt_code"
                " WHERE a.deleted = 0"
                " ORDER BY a.id"
            ).fetchall()
            rules = [_alert_rule_vm(r, today) for r in rows]

            # 真实基金对象：持有 + 关注；同一代码按 holding 优先去重。
            fund_rows = conn.execute(
                "SELECT h.fund_code, h.status, f.name, f.fund_type"
                " FROM holdings h JOIN fund_meta f ON f.code = h.fund_code"
                " WHERE h.deleted = 0 AND h.status IN ('holding','watch')"
                " ORDER BY CASE h.status WHEN 'holding' THEN 0 ELSE 1 END,"
                " h.open_date, h.fund_code"
            ).fetchall()
            funds, seen = [], set()
            for r in fund_rows:
                code = r["fund_code"]
                if code in seen:
                    continue
                seen.add(code)
                status = r["status"]
                status_label = "持有" if status == "holding" else "关注"
                fund_type = r["fund_type"] or ""
                funds.append({
                    "code": code,
                    "name": r["name"] or code,
                    "status": status,
                    "statusLabel": status_label,
                    "tagCls": _fund_tag_cls(fund_type),
                    "tagTxt": fund_type or "基金",
                    "sub": f"{code} · {status_label}",
                })

            # 真实市场对象：9 个指数 + 4 个赛道 ETF + 1 个黄金 ETF（ETF 仅作代理）。
            market_rows = conn.execute(
                "SELECT code, name, mkt_type, market FROM market_meta"
                " WHERE enabled = 1 ORDER BY display_order, code"
            ).fetchall()
            markets = [{
                "code": r["code"],
                "name": r["name"],
                "mktType": r["mkt_type"],
                "market": r["market"],
                "tagCls": "tag-blue",
                "tagTxt": _market_tag_txt(r["mkt_type"]),
            } for r in market_rows]

            return {
                "ok": True,
                "rules": rules,
                "funds": funds,
                "markets": markets,
                "enabled_count": _alert_enabled_count(conn),
            }
        except Exception as e:
            return {"ok": False, "error": f"读取提醒失败：{e}"}
        finally:
            if conn:
                conn.close()

    def create_alert(self, payload):
        """新建提醒规则；只落库，不检查、不通知。"""
        conn = None
        try:
            conn = dbm.connect()
            data = _alert_validate(conn, payload)
            now = dbm.now_str()
            cur = conn.execute(
                "INSERT INTO alerts (uuid, obj_type, fund_code, mkt_code, cond_type,"
                " direction, cond_value, valid_type, start_date, end_date, triggered_at,"
                " note, enabled, deleted, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, ?, ?)",
                (uuid.uuid4().hex, data["obj_type"], data["fund_code"], data["mkt_code"],
                 data["cond_type"], data["direction"], data["cond_value"],
                 data["valid_type"], data["start_date"], data["end_date"],
                 data["note"], data["enabled"], now, now),
            )
            conn.commit()
            return {"ok": True, "id": cur.lastrowid}
        except ValueError as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return {"ok": False, "error": str(e)}
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return {"ok": False, "error": f"保存提醒失败：{e}"}
        finally:
            if conn:
                conn.close()

    def update_alert(self, aid, payload):
        """编辑提醒规则；保留 uuid/created_at，默认保留启用状态。"""
        conn = None
        try:
            aid = _alert_id(aid)
            conn = dbm.connect()
            existing = conn.execute(
                "SELECT * FROM alerts WHERE id = ? AND deleted = 0", (aid,)
            ).fetchone()
            if not existing:
                return {"ok": False, "error": "提醒不存在或已删除"}
            data = _alert_validate(conn, payload, existing)
            # triggered_at 只允许 until_trigger 非空（数据库 CHECK 要求）。
            triggered_at = (existing["triggered_at"]
                            if data["valid_type"] == "until_trigger" else None)
            conn.execute(
                "UPDATE alerts SET obj_type=?, fund_code=?, mkt_code=?, cond_type=?,"
                " direction=?, cond_value=?, valid_type=?, start_date=?, end_date=?,"
                " triggered_at=?, note=?, enabled=?, updated_at=?"
                " WHERE id = ? AND deleted = 0",
                (data["obj_type"], data["fund_code"], data["mkt_code"],
                 data["cond_type"], data["direction"], data["cond_value"],
                 data["valid_type"], data["start_date"], data["end_date"],
                 triggered_at, data["note"], data["enabled"], dbm.now_str(), aid),
            )
            conn.commit()
            return {"ok": True, "id": aid}
        except ValueError as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return {"ok": False, "error": str(e)}
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return {"ok": False, "error": f"更新提醒失败：{e}"}
        finally:
            if conn:
                conn.close()

    def set_alert_enabled(self, aid, enabled):
        """启用/暂停一条提醒；重复设置同一状态也成功（幂等）。"""
        conn = None
        try:
            aid = _alert_id(aid)
            conn = dbm.connect()
            row = conn.execute(
                "SELECT deleted FROM alerts WHERE id = ?", (aid,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "提醒不存在"}
            if row["deleted"]:
                return {"ok": False, "error": "提醒已删除"}
            conn.execute(
                "UPDATE alerts SET enabled = ?, updated_at = ? WHERE id = ? AND deleted = 0",
                (1 if _truthy(enabled) else 0, dbm.now_str(), aid),
            )
            conn.commit()
            return {"ok": True, "id": aid}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return {"ok": False, "error": f"更新提醒失败：{e}"}
        finally:
            if conn:
                conn.close()

    def delete_alert(self, aid):
        """软删除一条提醒；重复删除同一 id 也成功（幂等）。"""
        conn = None
        try:
            aid = _alert_id(aid)
            conn = dbm.connect()
            row = conn.execute(
                "SELECT deleted FROM alerts WHERE id = ?", (aid,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "提醒不存在"}
            if not row["deleted"]:
                conn.execute(
                    "UPDATE alerts SET deleted = 1, enabled = 0, updated_at = ? WHERE id = ?",
                    (dbm.now_str(), aid),
                )
                conn.commit()
            return {"ok": True, "id": aid}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return {"ok": False, "error": f"删除提醒失败：{e}"}
        finally:
            if conn:
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

    def get_notify_capabilities(self):
        """真实系统通知能力（Windows Toast 可测试；自动推送仍未接通）。"""
        return system_notify.capabilities()

    def get_desktop_settings(self):
        """桌面端后台行为：关闭到托盘、登录自启动、托盘可用状态。"""
        conn = dbm.connect()
        try:
            close_to_tray = dbm.get_setting(conn, "close_to_tray")
        finally:
            conn.close()
        auto = autostart.get_status()
        return {
            "close_to_tray": close_to_tray != "0",  # 默认开启
            "autostart": auto,
            "tray_available": bool(sys.platform == "win32"),
            "tray_running": getattr(self, "tray_running", None),
        }

    def set_close_to_tray(self, enabled):
        """关闭主窗口时隐藏到托盘（True）或直接退出（False）。"""
        conn = dbm.connect()
        try:
            dbm.set_setting(conn, "close_to_tray", "1" if _truthy(enabled) else "0")
            conn.commit()
            return {"ok": True, "close_to_tray": bool(_truthy(enabled))}
        finally:
            conn.close()

    def set_autostart(self, enabled):
        """真实写/删 HKCU Run；失败返回错误，不假装成功。"""
        return autostart.set_enabled(bool(_truthy(enabled)))

    def get_autostart_status(self):
        return autostart.get_status()

    def minimize_to_tray(self):
        """把主窗口隐藏到托盘（由 main.py 注入回调）。"""
        cb = getattr(self, "hide_window", None)
        if not callable(cb):
            return {"ok": False, "error": "当前不是桌面窗口环境，无法最小化到托盘"}
        try:
            cb()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": f"最小化到托盘失败：{exc}"}

    def quit_app(self):
        """真正退出程序（由 main.py 注入回调）。"""
        cb = getattr(self, "exit_app", None)
        if not callable(cb):
            return {"ok": False, "error": "当前不是桌面窗口环境，无法退出程序"}
        try:
            cb()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": f"退出失败：{exc}"}

    def open_notification_settings(self):
        """打开 Windows 系统通知设置。"""
        return system_notify.open_settings()

    def send_test_notification(self, kind="generic"):
        """发送一条真实 Windows 系统通知，用于验证通道；不是模拟页面内提示。"""
        texts = {
            "news": ("小财通 · 市场事件提醒（测试）",
                     "这是一条真实 Windows 系统通知。新匹配到你持仓的官方事件会自动推送，"
                     "并在免打扰下弹出桌面横幅。"),
            "alerts": ("小财通 · 盯盘提醒（测试）",
                       "这是一条真实 Windows 系统通知。提醒规则由后台引擎每 60 秒检查，"
                       "触发后自动推送并弹出桌面横幅。"),
            "generic": ("小财通 · 测试通知",
                        "如果你看到这条通知，说明 Windows 系统通知通道已接通。"
                        "市场事件与盯盘提醒均可自动推送。"),
        }
        title, body = texts.get(kind, texts["generic"])
        return system_notify.send(title, body)

    def ask_notify_system(self, enabled):
        """首次系统通知引导的落点：记下已询问；选开启时同时打开两类通知偏好。

        注意：这里只保存偏好并记录“已询问”，真正发送由界面随后调用
        send_test_notification；发送失败不影响“已询问”标记，用户可在设置页重试。
        """
        conn = dbm.connect()
        try:
            on = _truthy(enabled)
            dbm.set_setting(conn, NOTIFY_KEYS["system_asked"], "1")
            if on:
                dbm.set_setting(conn, NOTIFY_KEYS["news_enabled"], "1")
                dbm.set_setting(conn, NOTIFY_KEYS["alerts_enabled"], "1")
            conn.commit()
            return {short: dbm.get_setting(conn, key) for short, key in NOTIFY_KEYS.items()}
        finally:
            conn.close()

    def get_engine_status(self):
        """盯盘监测真实运行状态（供提醒页状态驱动，不写死“运行中”）。"""
        st = alert_engine.status()
        conn = dbm.connect()
        try:
            enabled = dbm.get_setting(conn, NOTIFY_KEYS["alerts_enabled"])
        finally:
            conn.close()
        cap = system_notify.capabilities()
        st["notifications_enabled"] = bool(enabled) and _truthy(enabled)
        st["notifier_available"] = bool(cap and cap.get("available"))
        # 应用内偏好 ≠ Windows 系统通知总开关。把真实系统开关一并返回，
        # 页面才能显示「应用内提醒：开 / 系统通知：关」，不再把偏好误写成“桌面通知已开启”。
        st["toast_enabled"] = cap.get("toast_enabled") if cap else None
        return st

    # ---------- 市场事件（阶段一：SEC + 美联储） ----------
    def get_news_feed(self, days=14):
        """真实市场事件列表 + 真实统计 + 数据源状态。

        只返回数据库里已有的事件；页面据 summary 显示真实条数。
        窗口按类型过滤：实时 7 天、财报 14 天。
        没有相关事件时 funds 为空，绝不编造或凑数。
        刷新周期与净值刷新共用 app_meta.last_refresh_at，页面不再各显示各的时间。
        """
        conn = dbm.connect()
        try:
            refresh_at = dbm.get_app_meta(conn, "last_refresh_at")
            return news_service.get_feed(conn, days, refresh_at=refresh_at)
        finally:
            conn.close()

    def set_news_state(self, news_id, patch=None, is_read=None, is_favorite=None, is_ignored=None):
        """持久化一条新闻的用户状态（只写传入字段，其余不动）。

        兼容两种调用：set_news_state(id, {"is_favorite": true})（网页常用）
        或 set_news_state(id, True, False, None)（位置参数）。
        """
        try:
            nid = int(news_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "新闻编号不正确"}
        if isinstance(patch, dict):
            is_read = patch.get("is_read", is_read)
            is_favorite = patch.get("is_favorite", is_favorite)
            is_ignored = patch.get("is_ignored", is_ignored)
        elif patch is not None:
            is_read = patch
        conn = dbm.connect()
        try:
            exists = conn.execute("SELECT id FROM news_items WHERE id = ?", (nid,)).fetchone()
            if not exists:
                return {"ok": False, "error": "这条新闻已过期或被清理"}
            cur = conn.execute(
                "SELECT is_read, is_favorite, is_ignored FROM news_user_state WHERE news_id = ?",
                (nid,),
            ).fetchone()
            old = dict(cur) if cur else {"is_read": 0, "is_favorite": 0, "is_ignored": 0}
            for key, value in (("is_read", is_read), ("is_favorite", is_favorite),
                               ("is_ignored", is_ignored)):
                if value is not None:
                    old[key] = 1 if _truthy(value) else 0
            conn.execute(
                "INSERT INTO news_user_state (news_id, is_read, is_favorite, is_ignored, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(news_id) DO UPDATE SET "
                " is_read=excluded.is_read, is_favorite=excluded.is_favorite, "
                " is_ignored=excluded.is_ignored, updated_at=excluded.updated_at",
                (nid, old["is_read"], old["is_favorite"], old["is_ignored"], dbm.now_str()),
            )
            conn.commit()
            return {"ok": True, "news_id": nid, **old}
        except Exception as exc:
            return {"ok": False, "error": "保存新闻状态失败：%s" % exc}
        finally:
            conn.close()

    def get_research_overview(self):
        """研究工具的真实数据：市场花名册/行情 + 现任基金经理。

        不返回任何示例公司、示例经理或编造的“赛道”；没有数据时返回空数组，
        由页面显示“尚未获取/暂无”。
        """
        conn = dbm.connect()
        try:
            market_rows = conn.execute(
                "SELECT m.code, m.name, m.mkt_type, m.market, m.display_order,"
                " q.price_scaled, q.change_pct_scaled, q.quote_time, q.source, q.fetched_at "
                "FROM market_meta m LEFT JOIN market_quotes q ON q.mkt_code = m.code "
                "WHERE m.enabled = 1 ORDER BY m.mkt_type, m.display_order, m.code"
            ).fetchall()
            markets = []
            for r in market_rows:
                d = dict(r)
                d["type_label"] = MARKET_TYPE_LABELS.get(d["mkt_type"], d["mkt_type"])
                markets.append(d)
            codes = [r["fund_code"] for r in conn.execute(
                "SELECT DISTINCT fund_code FROM holdings "
                "WHERE deleted = 0 AND status = 'holding' ORDER BY fund_code"
            ).fetchall()]
            managers = fund_manager.get_managers(codes)
            out = []
            for code in codes:
                meta = conn.execute(
                    "SELECT name, fund_type, manager FROM fund_meta WHERE code = ?", (code,)
                ).fetchone()
                item = managers.get(code) or {"ok": False, "error": "尚未读取"}
                name = meta["name"] if meta else code
                manager_name = (meta["manager"] if meta else "") or ""
                if item.get("ok") and item.get("managers"):
                    manager_name = "、".join([m["name"] for m in item["managers"]])
                    # 只缓存姓名；详细任期/规模按需实时显示，避免把旧详情冒充最新。
                    conn.execute(
                        "UPDATE fund_meta SET manager = ?, updated_at = ? WHERE code = ?",
                        (manager_name, dbm.now_str(), code),
                    )
                    conn.commit()
                out.append({
                    "code": code,
                    "fund_name": name,
                    "fund_type": (meta["fund_type"] if meta else "") or "",
                    "manager": manager_name,
                    "ok": bool(item.get("ok")),
                    "managers": item.get("managers") or [],
                    "error": item.get("error"),
                })
            return {"markets": markets, "managers": out, "at": dbm.now_str()}
        finally:
            conn.close()

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
                    m = conn.execute("SELECT name, fund_type, manager FROM fund_meta WHERE code = ?", (code,)).fetchone()
                    names[code] = dict(m) if m else {"name": code, "fund_type": "", "manager": None}
                h["name"] = names[code]["name"]
                h["fund_type"] = names[code]["fund_type"] or ""
                h["manager"] = names[code].get("manager") or ""
                h.pop("deleted", None)
                st = h["status"]
                if st == "holding":
                    self._attach_live(conn, h)
                    result["holdings"].append(h)
                elif st == "watch":
                    for k in ("cost_cents", "shares_scaled", "sell_cents", "profit_cents",
                              "closed_at", "clear_reason", "clear_judge", "clear_good", "clear_review"):
                        h.pop(k, None)
                    # 关注基金也附本地最新净值（只读缓存，不联网、不改 schema）；
                    # 首页/我的基金显示“净值日 + 最新披露日涨跌幅”，不冒充盘中行情。
                    nav = src.latest_nav(conn, code)
                    if nav is None:
                        h["nav_date"] = h["nav_str"] = h["fetched_at"] = h["source"] = None
                        h["today_pct_str"] = None
                    else:
                        h["nav_date"] = nav["nav_date"]
                        h["nav_str"] = _nav_str(nav["unit_nav_scaled"])
                        h["fetched_at"] = nav["fetched_at"]
                        h["source"] = SOURCE_LABELS.get(nav["source"], nav["source"])
                        pct = nav.get("daily_pct_scaled")
                        h["today_pct_str"] = _pct2_str(pct) if pct is not None else None
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
            # 与市场事件共用同一刷新周期；首页用它显示，避免两个页面时间对不上。
            result["refresh_cycle_at"] = (dbm.get_app_meta(conn, "last_refresh_at")
                                          or result["last_check_at"])
            return result
        finally:
            conn.close()

    def get_portfolio_report(self):
        """组合体检：只读 holding 持仓 + 真实净值，调纯计算模块返回结构化报告。

        不联网、不写库、无副作用；仅统计 status='holding' 且未删除的持仓，
        watch/closed 不混入当前市值。空持仓由纯计算模块返回 empty 说明。
        """
        conn = dbm.connect()
        try:
            holds = conn.execute(
                "SELECT h.fund_code, h.shares_scaled, h.cost_cents,"
                " f.name, f.fund_type "
                "FROM holdings h JOIN fund_meta f ON f.code = h.fund_code "
                "WHERE h.status='holding' AND h.deleted=0"
            ).fetchall()
            positions = []
            for r in holds:
                nav_rows = conn.execute(
                    "SELECT nav_date AS date, unit_nav_scaled AS nav,"
                    " daily_pct_scaled AS pct FROM fund_nav_daily "
                    "WHERE fund_code=? ORDER BY nav_date ASC",
                    (r["fund_code"],),
                ).fetchall()
                positions.append({
                    "code": r["fund_code"],
                    "name": r["name"],
                    "fund_type": r["fund_type"] or "",
                    "shares_scaled": r["shares_scaled"],
                    "cost_cents": r["cost_cents"],
                    "nav_rows": [dict(x) for x in nav_rows],
                })
            return portfolio_analysis.build_report(positions)
        finally:
            conn.close()

    def _attach_live(self, conn, h):
        """持有行附加：三要素（净值日/获取时间/来源）+ 精确计算值 + 展示字符串"""
        code = h["fund_code"]
        cost_c = h.pop("cost_cents", None)
        shares_s = h.pop("shares_scaled", None)
        h["cost_cents"] = cost_c
        h["shares_str"] = _shares_str(shares_s)
        h["cost_price_str"] = _cost_price_str(cost_c, shares_s)
        nav = src.latest_nav(conn, code)
        if nav is None:
            h["nav_date"] = h["nav_str"] = h["fetched_at"] = h["source"] = None
            h["market_value_cents"] = h["profit_cents"] = None
            h["today_profit_cents"] = h["today_pct_str"] = h["profit_pct_str"] = None
            return h
        h["nav_date"] = nav["nav_date"]
        h["nav_str"] = _nav_str(nav["unit_nav_scaled"])
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
        h["profit_pct_str"] = _pct_ratio_str(h["profit_cents"], cost_c)
        return h

    @staticmethod
    def _attach_history(h):
        """历史行附加展示字段（金额给分、百分比给字符串，界面不再算账）"""
        h["cost_cents"] = h.pop("cost_cents", None)
        h["sell_cents"] = h.pop("sell_cents", None)
        h["profit_cents"] = h.pop("profit_cents", None)
        h["profit_pct_str"] = _pct_ratio_str(h["profit_cents"], h["cost_cents"])
        h.pop("shares_scaled", None)
        return h

    @staticmethod
    def _summary(conn, holdings):
        """资产总览汇总（整数分求和 + Decimal 算百分比，全在 Python 侧）。

        口径（对齐 GPT《收口及双端交付决定》）：
        - 所有持仓都有有效净值（complete）：给完整组合总市值/累计盈亏/收益率/今日盈亏；
        - 部分持仓缺净值：完整口径字段为 None（界面显示 —），另给「已估值部分」
          小计与完整度，绝不把已知部分合计冒充组合总资产；
        - 全部无净值：total/profit 一律 None（界面显示 —，不把"还没算出来"显示成 0）；
        - 成本 cost_cents 恒有值（含全部持仓成本）。
        """
        s = {"count": len(holdings), "cost_cents": 0,
             "complete": False,
             "total_cents": None, "profit_cents": None,
             "today_profit_cents": None,
             "profit_pct_str": None, "today_pct_str": None,
             "valued_count": 0, "missing_count": 0, "missing_codes": [],
             "known_value_cents": None, "known_cost_cents": None,
             "known_profit_cents": None, "known_today_profit_cents": None,
             "data_as_of": None, "data_age_days": None, "known_note": None}
        total_cost = 0
        valued = []
        missing = []
        nav_dates = set()
        for h in holdings:
            total_cost += h["cost_cents"] or 0
            if h["market_value_cents"] is not None:
                valued.append(h)
                if h.get("nav_date"):
                    nav_dates.add(h["nav_date"])
            else:
                missing.append(h)
        s["cost_cents"] = total_cost
        s["valued_count"] = len(valued)
        s["missing_count"] = len(missing)
        s["missing_codes"] = [h["fund_code"] for h in missing]
        complete = len(holdings) > 0 and not missing
        s["complete"] = complete

        # 已知部分小计（部分缺失时供首页克制展示，带明确标签）
        if valued:
            known_value = sum(h["market_value_cents"] for h in valued)
            known_cost = sum(h["cost_cents"] or 0 for h in valued)
            s["known_value_cents"] = known_value
            s["known_cost_cents"] = known_cost
            s["known_profit_cents"] = known_value - known_cost
            known_today = [h["today_profit_cents"] for h in valued
                           if h["today_profit_cents"] is not None]
            if known_today:
                s["known_today_profit_cents"] = sum(known_today)

        # 完整组合口径：仅在数据完整时成立
        if complete:
            total_value = sum(h["market_value_cents"] for h in valued)
            total_profit = sum(h["profit_cents"] or 0 for h in valued)
            s["total_cents"] = total_value
            s["profit_cents"] = total_profit
            s["profit_pct_str"] = _pct_ratio_str(total_profit, total_cost)
            today_parts = [h["today_profit_cents"] for h in valued
                           if h["today_profit_cents"] is not None]
            if today_parts:
                today_total = sum(today_parts)
                s["today_profit_cents"] = today_total
                # 今日盈亏% = 今日盈亏 / 昨日市值
                if (total_value - today_total) > 0:
                    s["today_pct_str"] = _pct2_str_for(total_value - today_total, today_total)

        # 数据截止日期与距今天数（客观陈述，不发明「过期」阈值）
        if nav_dates:
            data_as_of = max(nav_dates)
            s["data_as_of"] = data_as_of
            try:
                d = datetime.date.fromisoformat(data_as_of)
                s["data_age_days"] = (datetime.date.today() - d).days
            except ValueError:
                pass
        if missing:
            s["known_note"] = (
                f"{len(holdings)} 只持仓中有 {len(missing)} 只缺少有效净值，"
                "以下金额仅为已取得净值的持仓市值，不是完整组合总市值。")
        return s

    # ---------- 基金搜索 ----------
    def search_fund(self, keyword):
        """按名称/代码搜索全市场真实基金（首次联网加载名单约几秒，此后走缓存）；
        网络失败返回 {"items": [], "offline": true}，界面如实提示。
        联网失败但存在过期磁盘缓存时，stale=true 并带 cache_at 供界面标注更新时间。"""
        try:
            items = fund_list.search(keyword, 20)
            info = fund_list.cache_info()
            return {
                "items": items,
                "offline": False,
                "stale": bool(info.get("stale")),
                "cache_at": info.get("fetched_at") or 0,
            }
        except Exception:
            return {"items": [], "offline": True}

    def get_portfolio_report(self):
        """组合体检：只读 holding 持仓 + 真实净值，调纯计算模块返回结构化报告。

        不联网、不写库；仅统计 status='holding' 且未删除的基金，watch/closed 不混入。
        无 holding 持仓时返回纯计算模块的 empty 说明，界面显示诚实空态。
        """
        conn = dbm.connect()
        try:
            holds = conn.execute(
                "SELECT h.fund_code, h.shares_scaled, h.cost_cents, "
                " f.name, f.fund_type FROM holdings h "
                "JOIN fund_meta f ON f.code = h.fund_code "
                "WHERE h.status='holding' AND h.deleted=0 ORDER BY h.fund_code"
            ).fetchall()
            positions = []
            for r in holds:
                nav_rows = [{
                    "date": x["nav_date"],
                    "nav": x["unit_nav_scaled"],
                    "pct": x["daily_pct_scaled"],
                } for x in conn.execute(
                    "SELECT nav_date, unit_nav_scaled, daily_pct_scaled "
                    "FROM fund_nav_daily WHERE fund_code=? ORDER BY nav_date",
                    (r["fund_code"],)
                ).fetchall()]
                positions.append({
                    "code": r["fund_code"],
                    "name": r["name"],
                    "fund_type": r["fund_type"] or "",
                    "shares_scaled": r["shares_scaled"],
                    "cost_cents": r["cost_cents"],
                    "nav_rows": nav_rows,
                })
            return portfolio_analysis.build_report(positions)
        finally:
            conn.close()

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
            "nav_str": _nav_str(cached["unit_nav_scaled"]),
            "nav_scaled": cached["unit_nav_scaled"],
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

    def refresh_data(self):
        """手动刷新统一周期：净值 + 行情 + 市场事件共用一次刷新时间。

        页面上的「刷新数据」走这里，不再只刷净值，避免净值时间前进而事件时间不动。
        后台线程复用 refresher.tick 的互斥锁和逐任务隔离；若刚巧有启动/定时刷新在跑，
        本轮会安全跳过，不叠加联网请求。
        """
        threading.Thread(
            target=refresher.tick,
            args=(self,),
            kwargs={"notify": self._notify_js},
            daemon=True,
            name="xltc-manual-refresh",
        ).start()
        return {"ok": True, "queued": True}

    def refresh_all_nav(self, codes, mode="manual"):
        """手动刷新（界面按钮）：立即后台联网更新，完成时派发 dataUpdated 事件"""
        codes = _clean_codes(codes)
        if codes:
            threading.Thread(target=self._refresh_worker, args=(codes, mode), daemon=True).start()
        return {"ok": True}

    def refresh_nav(self, code):
        """立即联网刷新一只基金净值（带锁同步，前端显示加载态）。

        走 refresher.refresh_nav 统一入口，以便净值变化能派发给提醒引擎；
        返回 {ok, error?}，失败时库里旧净值原样保留。
        """
        code = str(code or "").strip()
        if not code:
            return {"ok": False, "error": "基金代码为空"}
        out = refresher.refresh_nav([code], notify=None, mode="manual")
        report = out.get("report") or []
        if not report:
            return {"ok": False, "error": "基金代码为空"}
        if report[0]["action"] == "failed":
            return {"ok": False, "error": "刷新失败（旧净值保留）"}
        return {"ok": True}

    def _refresh_worker(self, codes, mode):
        """后台刷新线程：逐只更新，失败只记该只；成功后把动作时间记入
        app_meta.last_success_fetch_at（界面"何时获取"依据）。

        2026-11-16 改为复用 refresher.refresh_nav 统一入口：
        手动/添加流程的净值变化也会派发给提醒引擎，避免引擎收不到；
        dataUpdated 事件由 refresh_nav 统一派发，这里不再重复派发。
        任何情况下都不改动库里已有的旧净值（更新失败 = 旧数据原样保留）。
        """
        out = refresher.refresh_nav(codes, notify=self._notify_js, mode=mode)
        report = out.get("report") or []
        ok = [e["code"] for e in report if e["action"] != "failed"]
        if ok:
            conn = dbm.connect()
            try:
                dbm.set_app_meta(conn, "last_success_fetch_at", dbm.now_str())
                conn.commit()
            finally:
                conn.close()

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
    """点位缩放整数 → 展示字符串 '4,548.05'（界面直接显示，四舍五入到 2 位）"""
    v = cv.point_from_scaled(price_scaled).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(v, ",.2f")


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


def _signed_pct(v):
    """百分比统一出口：四舍五入到 0 时显示 0.00（不带符号），正数带 +，负数带 -"""
    if v == 0:
        return "0.00"
    s = format(v, "f")
    return s if s.startswith("-") else "+" + s


def _nav_str(nav_scaled):
    """单位净值缩放整数（×10000）→ 固定 4 位小数展示（1.3914 / 1.5000）"""
    if nav_scaled is None:
        return None
    v = cv.nav_from_scaled(nav_scaled).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return format(v, "f")


def _shares_str(shares_scaled):
    """份额缩放整数 → 固定 2 位展示字符串 '23,924.00'（界面直接显示）"""
    if shares_scaled is None:
        return None
    v = cv.shares_from_scaled(shares_scaled).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(v, ",.2f")


def _cost_price_str(cost_cents, shares_scaled):
    """单位成本价 = 总本金 ÷ 份额，固定 4 位小数（1.5000）"""
    if cost_cents is None or not shares_scaled:
        return None
    try:
        v = (Decimal(cost_cents) * 100 / Decimal(shares_scaled)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP)
    except Exception:
        return None
    return format(v, "f")


def _pct2_str(pct_scaled):
    """涨跌幅缩放整数（×10000）→ 展示字符串 '+1.82' / '-0.50' / '0.00'"""
    if pct_scaled is None:
        return None
    v = cv.pct_from_scaled(pct_scaled).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return _signed_pct(v)


def _pct_ratio_str(part_cents, whole_cents):
    """占比百分比（分母可能为负/零）：固定 2 位，如 '+55.61' / '-9.30'"""
    if part_cents is None or not whole_cents:
        return None
    try:
        v = (Decimal(part_cents) / Decimal(whole_cents) * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None
    return _signed_pct(v)


def _pct2_str_for(base_cents, part_cents):
    """百分比分母非分金额场景（今日盈亏/昨日市值），保留 2 位小数"""
    if not base_cents:
        return None
    try:
        v = (Decimal(part_cents) / Decimal(base_cents) * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None
    return _signed_pct(v)
