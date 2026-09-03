# -*- coding: utf-8 -*-
"""pywebview js_api —— 网页界面与 Python 数据层之间的唯一接口

规则（2026-09-03 用户确认的界面方案 A 边界）：
- 界面只通过 window.pywebview.api.xxx() 取数/写数；
- 页面效果与操作流程保持已验收原型不变，仅替换数据来源；
- 返回值一律 JSON 可序列化（dict/list/str/int/float/None/bool），
  Decimal/日期对象转成字符串后返回；金额/份额统一给展示字符串。

本文件按「我的基金」页改造起步提供接口，随逐页改造扩充。
"""
import threading
import uuid

from . import converters as cv
from . import db as dbm
from .sources import fund_nav as src

# 持仓记录/复盘允许前端直接更新的文本字段白名单
HOLDING_TEXT_FIELDS = {"buy_reason", "buy_judge", "watch_points", "remark"}

# 净值刷新全局锁：后台自动刷新与手动刷新可能同时发生，而 akshare 底层 JS 引擎
# （py_mini_racer/V8）线程不安全，并发调用会崩溃（2026-09-03 实测复现）。
# 所有联网拉净值入口必须经此锁串行执行。
NAV_REFRESH_LOCK = threading.Lock()


class Api:
    """网页 JS 可调用的对象；pywebview 会把它的方法暴露为 window.pywebview.api.*"""

    # ---------- 健康检查 ----------
    def ping(self):
        return "pong"

    # ---------- 我的基金页 ----------
    def get_holdings(self):
        """全部持仓（含已清仓历史），每行附真实计算值。

        返回分组（与已验收 funds.html 的 持有/关注/历史 三列表对应）：
        {"holdings": [..], "watch": [..], "history": [..]}
        持有行含：id, fund_code, name, fund_type, tag, status, open_date,
        cost_yuan, shares, buy_reason, buy_judge, watch_points, remark,
        nav_date, nav, market_value_yuan, profit_yuan, today_change_pct, today_profit_yuan
        历史行含：closed_at, sell_yuan, profit_yuan, clear_reason/clear_judge/clear_good/clear_review
        所有金额/份额/净值均为展示字符串；None 表示尚无数据。
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
                    self._attach_live(conn, h)  # 内部取出原始缩放值后弹出
                    result["holdings"].append(h)
                elif st == "watch":
                    h.pop("cost_cents", None)
                    h.pop("shares_scaled", None)
                    h["cost_yuan"] = None
                    h["shares"] = None
                    result["watch"].append(h)
                else:  # closed 历史持仓
                    h["cost_yuan"] = self._yuan_str(h.pop("cost_cents", None))
                    h["sell_yuan"] = self._yuan_str(h.pop("sell_cents", None))
                    h["profit_yuan"] = self._yuan_str(h.pop("profit_cents", None))
                    h["shares"] = self._shares_str(h.pop("shares_scaled", None))
                    result["history"].append(h)
            return result
        finally:
            conn.close()

    def _attach_live(self, conn, h):
        """持有行附加最新净值与市值/收益/今日数据（Decimal 精确链，同一连接内查询）"""
        code = h["fund_code"]
        cost_c = h.pop("cost_cents", None)
        shares_s = h.pop("shares_scaled", None)
        h["cost_yuan"] = str(cv.fen_to_yuan(cost_c))
        h["shares"] = str(cv.shares_from_scaled(shares_s))
        nav = src.latest_nav(conn, code)
        if nav is None:
            h["nav_date"] = h["nav"] = None
            h["market_value_yuan"] = h["profit_yuan"] = None
            h["today_change_pct"] = h["today_profit_yuan"] = None
            return h
        h["nav_date"] = nav["nav_date"]
        h["nav"] = str(cv.nav_from_scaled(nav["unit_nav_scaled"]))
        h["market_value_yuan"] = str(
            cv.fen_to_yuan(src.market_value_cents(shares_s, nav["unit_nav_scaled"])))
        h["profit_yuan"] = str(
            cv.fen_to_yuan(src.profit_cents(shares_s, nav["unit_nav_scaled"], cost_c)))
        pct = nav.get("daily_pct_scaled")
        h["today_change_pct"] = str(cv.pct_from_scaled(pct)) if pct is not None else None
        prev = conn.execute(
            "SELECT unit_nav_scaled FROM fund_nav_daily "
            "WHERE fund_code = ? AND nav_date < ? ORDER BY nav_date DESC LIMIT 1",
            (code, nav["nav_date"]),
        ).fetchone()
        if prev is not None:
            h["today_profit_yuan"] = str(
                cv.fen_to_yuan(src.today_profit_cents(shares_s, nav["unit_nav_scaled"], prev["unit_nav_scaled"])))
        else:
            h["today_profit_yuan"] = None
        return h

    @staticmethod
    def _yuan_str(v):
        return str(cv.fen_to_yuan(v)) if v is not None else None

    @staticmethod
    def _shares_str(v):
        return str(cv.shares_from_scaled(v)) if v is not None else None

    def search_fund(self, keyword):
        """按代码/名称搜索基金（akshare 全市场基金列表本地过滤），最多 20 条；
        网络失败时返回 {"items": [], "offline": true}（界面提示稍后重试）。"""
        try:
            import akshare as ak
            df = ak.fund_name_em()
        except Exception:
            return {"items": [], "offline": True}
        k = (keyword or "").strip()
        out = []
        for _, r in df.iterrows():
            code = str(r["基金代码"])
            name = str(r["基金简称"])
            ftype = str(r.get("基金类型", ""))
            if k in code or k.upper() in name.upper():
                out.append({"code": code, "name": name, "fund_type": ftype})
                if len(out) >= 20:
                    break
        return {"items": out, "offline": False}

    def add_holding(self, payload):
        """添加一笔持仓（已验收流程：搜索/手动填 → 本金+份额+买入时间 → 记录可选）

        payload: {fund_code, name(可选建档), fund_type(可选), cost_yuan, shares,
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
                # 1) 基金档案：库里没有则建档（名称优先取搜索结果）
                if conn.execute("SELECT 1 FROM fund_meta WHERE code = ?", (code,)).fetchone() is None:
                    conn.execute(
                        "INSERT OR IGNORE INTO fund_meta (code, name, fund_type, source, fetched_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, 'manual', ?, ?, ?)",
                        (code, (payload.get("name") or code).strip(), payload.get("fund_type") or "", now, now, now),
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
            self._spawn_nav_refresh(code)
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
            return {"ok": True, "sell_yuan": str(cv.fen_to_yuan(sell_c)),
                    "profit_yuan": str(cv.fen_to_yuan(profit_c))}
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

    def refresh_nav(self, code):
        """立即联网刷新一只基金净值（前端显示加载态），返回本次写入行数"""
        with NAV_REFRESH_LOCK:
            try:
                return {"ok": True, "rows": src.refresh_fund_nav(code)}
            except Exception as e:
                return {"ok": False, "error": f"刷新失败：{e}"}

    def refresh_all_nav(self, codes):
        """后台刷新多只基金净值（打开页面自动更新用），完成时派发 dataUpdated 事件；
        返回立即，不阻塞界面。"""
        codes = [c for c in (codes or []) if c]
        if codes:
            threading.Thread(target=self._refresh_all_worker, args=(codes,), daemon=True).start()
        return {"ok": True}

    def _refresh_all_worker(self, codes):
        with NAV_REFRESH_LOCK:
            for code in codes:
                try:
                    src.refresh_fund_nav(code)
                except Exception as e:
                    print(f"[api] 刷新净值失败 {code}: {e}")
        self._notify_js("dataUpdated")

    def _spawn_nav_refresh(self, code):
        threading.Thread(target=self._refresh_all_worker, args=([code],), daemon=True).start()

    def _notify_js(self, event):
        """向所有窗口派发 JS 事件（如数据刷新完成 → 前端重取）"""
        try:
            import webview
            for w in webview.windows:
                w.evaluate_js(f"window.__xltcOnData && window.__xltcOnData('{event}')")
        except Exception:
            pass
