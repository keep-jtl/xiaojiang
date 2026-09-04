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
- 自动更新 = 检查缓存，需要才联网：每天最多自动尝试一次（app_meta.auto_refresh_date），
  失败不清空/不覆盖旧数据、如实上报（事件 ok/failed），用户可手动重试。

本文件按「我的基金」页改造起步提供接口，随逐页改造扩充。
"""
import json
import threading
import uuid

from decimal import Decimal, ROUND_HALF_UP

from . import converters as cv
from . import db as dbm
from .sources import fund_list, fund_nav as src

# 持仓记录/复盘允许前端直接更新的文本字段白名单
HOLDING_TEXT_FIELDS = {"buy_reason", "buy_judge", "watch_points", "remark"}

# 净值刷新全局锁：后台自动刷新与手动刷新可能同时发生，而 akshare 底层 JS 引擎
# （py_mini_racer/V8）线程不安全，并发调用会崩溃（2026-09-03 实测复现）。
# 所有联网拉净值入口必须经此锁串行执行。
NAV_REFRESH_LOCK = threading.Lock()

# 页面显示用的数据源名称（source 字段存内部标识，展示名在此映射，换源时只改这里）
SOURCE_LABELS = {"akshare": "天天基金（公开接口）", "manual": "手动建档"}


class Api:
    """网页 JS 可调用的对象；pywebview 会把它的方法暴露为 window.pywebview.api.*"""

    # ---------- 健康检查 ----------
    def ping(self):
        return "pong"

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
        - last_fetch_at：最近一次成功联网获取净值的时刻（app_meta，全局动作时间）
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
    def add_holding(self, payload):
        """添加一笔持仓（已验收流程：搜索/手动填 → 本金+份额 → 记录可选）

        payload: {fund_code, name(建档用), fund_type, cost_yuan, shares,
                  open_date 'YYYY-MM-DD', tag 'heavy|normal|watch',
                  buy_reason, buy_judge, watch_points, remark}
        无金额无份额 → 存为仅关注(watch)；否则存为持有(holding)。
        返回 {"ok": true, "id": ..} 或 {"ok": false, "error": ".."}；净值拉取后台自动进行。
        """
        try:
            code = str(payload.get("fund_code", "")).strip()
            if not code:
                return {"ok": False, "error": "基金代码不能为空"}
            cost_yuan = payload.get("cost_yuan")
            shares = payload.get("shares")
            tag = payload.get("tag", "normal")
            is_watch = not cost_yuan and not shares
            cost_c = shares_s = None
            if not is_watch:
                cost_c = cv.yuan_to_fen(cost_yuan)
                shares_s = cv.shares_to_scaled(shares)
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

    # ---------- 净值刷新（检查式自动更新 + 手动） ----------
    def maybe_auto_refresh(self, codes):
        """打开页面时的自动更新入口：检查缓存，需要才联网（2026-09-04 用户确认）。

        策略（MVP 一期简单可靠版）：同一自然日最多自动联网尝试一次
        （app_meta.auto_refresh_date，无论成败都记，防反复失败重试打网络）；
        次日打开页面会再次自动尝试。手动刷新不受此限制。
        返回 {"ok": true, "skipped": bool, "reason": "today_done"|"no_holdings"|None}
        """
        codes = _clean_codes(codes)
        if not codes:
            return {"ok": True, "skipped": True, "reason": "no_holdings"}
        conn = dbm.connect()
        try:
            if dbm.get_app_meta(conn, "auto_refresh_date") == dbm.today_str():
                return {"ok": True, "skipped": True, "reason": "today_done"}
            dbm.set_app_meta(conn, "auto_refresh_date", dbm.today_str())  # 先占位防并发重复
            conn.commit()  # 必须提交，否则连接关闭时回滚、当天可反复发起
        finally:
            conn.close()
        threading.Thread(target=self._refresh_worker, args=(codes, "auto"), daemon=True).start()
        return {"ok": True, "skipped": False}

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


def _clean_codes(codes):
    """去空去重的基金代码列表"""
    seen, out = set(), []
    for c in (codes or []):
        c = str(c).strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


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
