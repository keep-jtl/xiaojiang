# -*- coding: utf-8 -*-
"""盯盘提醒 1B 引擎（Stage A：只做后端，不接 main/UI/真实库）。

职责：
- 约 60 秒 tick；交易时段按市场约 5 分钟定向刷新被提醒引用的指数行情；
- 行情必须“交易中 + quote_time 存在 + 属于市场当天 + 不超过新鲜度阈值”才允许触发；
- 基金只消费 refresher 的 advanced（确认出现新 nav_date）报告，不单独重复刷新；
- 触发事件写入与 until_trigger 失效同事务；通知结果另事务更新，通知失败不重复触发；
- 启动后的第一次成功净值刷新只建基线，不补造关机期间错过的提醒。

不使用 Windows-Toasts；通知通道由 app.notify_channel 的接口注入。
"""
import datetime
import sqlite3
import threading
import time
import uuid
from collections import deque
from decimal import Decimal, InvalidOperation

from . import alert_rules
from . import converters as cv
from . import db as dbm
from . import market_cal as mcal
from . import refresher
from .notify_channel import NotifyResult, Notifier, NullNotifier
from .sources import market_quotes as mq

TICK_SECONDS = 60
QUOTE_REFRESH_SECONDS = 300          # 交易时段约 5 分钟定向刷新
QUOTE_MAX_AGE_SECONDS = 600          # 候选阈值；盘中实测后再定
FUTURE_TOLERANCE_SECONDS = 120       # 源时钟轻微超前容忍
PENDING_STALE_SECONDS = 120          # 崩溃后 pending 通知不重发，超时标记 failed
NOTIFY_KEY = "notify_alerts_enabled"


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_quote_time(value):
    if not value:
        return None
    try:
        return datetime.datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


class AlertEngine:
    def __init__(self, notifier=None, ui_notify=None, quote_refresher=None,
                 now_fn=None, monotonic_fn=None):
        self.notifier: Notifier = notifier or NullNotifier()
        self.ui_notify = ui_notify
        self.quote_refresher = quote_refresher or mq.refresh_market_quotes
        self.now_fn = now_fn or datetime.datetime.now
        self.monotonic_fn = monotonic_fn or time.monotonic

        self._started = False
        self._stop = threading.Event()
        self._thread = None
        self._tick_lock = threading.Lock()
        self._nav_lock = threading.Lock()
        self._nav_queue = deque(maxlen=500)
        self._nav_baseline_pending = True
        self._last_quote_refresh = {}
        self._listener = self.on_nav_report
        self._listener_registered = False

    # ---------- 生命周期 ----------
    def start(self):
        if self._started:
            return False
        self._started = True
        self._stop.clear()
        with self._nav_lock:
            self._nav_queue.clear()
            self._nav_baseline_pending = True
        self._last_quote_refresh = {}
        if not self._listener_registered:
            refresher.add_nav_listener(self._listener)
            self._listener_registered = True
        try:
            self.recover_pending()
        except Exception as e:
            print(f"[alert_engine] 启动时清理 pending 通知失败（不影响引擎）: {e}")
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="xltc-alert-engine")
        self._thread.start()
        print("[alert_engine] 提醒引擎已启动：约 60 秒检查一次")
        return True

    def stop(self):
        if not self._started:
            return False
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        if self._listener_registered:
            refresher.remove_nav_listener(self._listener)
            self._listener_registered = False
        with self._nav_lock:
            self._nav_queue.clear()
        self._thread = None
        self._started = False
        print("[alert_engine] 提醒引擎已停止")
        return True

    def _loop(self):
        while not self._stop.is_set():
            t0 = self.monotonic_fn()
            try:
                self.tick()
            except Exception as e:
                print(f"[alert_engine] tick 异常（不影响后续）: {e}")
            remain = max(1.0, TICK_SECONDS - (self.monotonic_fn() - t0))
            self._stop.wait(remain)

    # ---------- 刷新器 → 引擎 ----------
    def on_nav_report(self, event):
        """refresher 完成净值刷新后的回调；只入队，不做重活。"""
        if not isinstance(event, dict):
            return
        report = event.get("report") or []
        successful = any(
            e.get("action") in ("advanced", "no_new") for e in report
        )
        with self._nav_lock:
            # 启动后第一次成功刷新只建立基线；全失败则等下一次成功。
            if event.get("startup") or self._nav_baseline_pending:
                if successful:
                    self._nav_baseline_pending = False
                return
            for e in report:
                if e.get("action") != "advanced":
                    continue
                code = e.get("code")
                after = e.get("after")
                before = e.get("before")
                if not code or not after or before is None:
                    continue
                self._nav_queue.append({
                    "code": str(code),
                    "before": str(before),
                    "after": str(after),
                    "started_at": event.get("started_at"),
                    "finished_at": event.get("finished_at"),
                })

    def _drain_nav(self, now):
        items = []
        with self._nav_lock:
            while self._nav_queue:
                items.append(self._nav_queue.popleft())
        count = 0
        for item in items:
            try:
                count += self._process_nav_item(item, now)
            except Exception as e:
                print(f"[alert_engine] 处理净值事件失败 {item.get('code')}: {e}")
        return count

    def _process_nav_item(self, item, now):
        code = item["code"]
        after = str(item["after"])[:10]
        started_at = item.get("started_at")
        today = now.date()
        if after > today.isoformat():
            print(f"[alert_engine] 忽略未来净值日 {code} {after}")
            return 0

        conn = dbm.connect()
        try:
            latest = conn.execute(
                "SELECT MAX(nav_date) FROM fund_nav_daily WHERE fund_code = ?",
                (code,),
            ).fetchone()[0]
            # 已有更新的净值 → 交给更新的报告；不补造中间日期。
            if latest and str(latest) > after:
                return 0
            row = conn.execute(
                "SELECT nav_date, unit_nav_scaled, daily_pct_scaled "
                "FROM fund_nav_daily WHERE fund_code = ? AND nav_date = ?",
                (code, after),
            ).fetchone()
            if not row:
                return 0
            prev = conn.execute(
                "SELECT nav_date, unit_nav_scaled FROM fund_nav_daily "
                "WHERE fund_code = ? AND nav_date < ? ORDER BY nav_date DESC LIMIT 1",
                (code, after),
            ).fetchone()
            alerts = conn.execute(
                "SELECT a.*, f.name AS fund_name, f.fund_type AS fund_type "
                "FROM alerts a JOIN fund_meta f ON f.code = a.fund_code "
                "WHERE a.deleted = 0 AND a.enabled = 1 AND a.obj_type = 'fund' "
                "AND a.fund_code = ? AND EXISTS ("
                "  SELECT 1 FROM holdings h WHERE h.fund_code = a.fund_code "
                "  AND h.deleted = 0 AND h.status IN ('holding','watch')"
                ")",
                (code,),
            ).fetchall()
        finally:
            conn.close()

        count = 0
        for alert in alerts:
            try:
                # 规则创建晚于本次刷新开始 → 不因“刚创建”立即触发。
                if started_at and str(alert["created_at"] or "") > str(started_at):
                    continue
                if not alert_rules.is_active_on(alert, today):
                    continue
                if self._event_exists(alert["id"], after):
                    continue

                if str(alert["cond_type"]) == "pct":
                    if row["daily_pct_scaled"] is not None:
                        actual = cv.pct_from_scaled(row["daily_pct_scaled"])
                    elif prev is not None:
                        try:
                            actual = (
                                Decimal(row["unit_nav_scaled"]) /
                                Decimal(prev["unit_nav_scaled"]) - 1
                            ) * 100
                        except (InvalidOperation, ZeroDivisionError):
                            continue
                    else:
                        continue
                else:
                    actual = cv.nav_from_scaled(row["unit_nav_scaled"])

                if not alert_rules.matches(alert, actual):
                    continue
                if self._record_and_notify(
                    alert, after,
                    alert_rules.cond_snapshot(alert),
                    alert_rules.format_actual(alert, actual),
                    now,
                ):
                    count += 1
            except Exception as e:
                print(f"[alert_engine] 基金规则 {alert['id']} 检查失败: {e}")
        return count

    # ---------- 指数行情 ----------
    def _check_markets(self, now):
        conn = dbm.connect()
        try:
            rows = conn.execute(
                "SELECT a.*, m.name AS mkt_name, m.market AS market, m.mkt_type AS mkt_type "
                "FROM alerts a JOIN market_meta m ON m.code = a.mkt_code "
                "WHERE a.deleted = 0 AND a.enabled = 1 AND a.obj_type = 'mkt' "
                "AND m.enabled = 1"
            ).fetchall()
            if not rows:
                return 0
            groups = {}
            for row in rows:
                groups.setdefault(row["market"], []).append(row)

            work = []
            for market, alerts in groups.items():
                try:
                    local_date = (mcal.ny_from_beijing(now).date()
                                  if market == "us" else now.date())
                    cal = mcal.is_trading_day(conn, market, local_date)
                    if cal is None:
                        print(f"[alert_engine] {market} 官方日历缺失，跳过本轮")
                        continue
                    state, market_date, _, _ = mcal.session(
                        market, now, cal[0], cal[1])
                    if state != "trading":
                        continue
                    eligible = [
                        a for a in alerts
                        if alert_rules.is_active_on(a, market_date)
                        and not self._event_exists_conn(conn, a["id"], market_date)
                    ]
                    if not eligible:
                        continue
                    codes = sorted({a["mkt_code"] for a in eligible})
                    work.append((market, market_date, eligible, codes))
                except Exception as e:
                    print(f"[alert_engine] {market} 市场规则准备失败: {e}")
            return self._run_market_work(work, now)
        finally:
            conn.close()

    def _run_market_work(self, work, now):
        count = 0
        for market, market_date, eligible, codes in work:
            try:
                count += self._check_one_market(market, market_date, eligible, codes, now)
            except Exception as e:
                print(f"[alert_engine] {market} 行情检查失败: {e}")
        return count

    def _check_one_market(self, market, market_date, eligible, codes, now):
        mono = self.monotonic_fn()
        last = self._last_quote_refresh.get(market)
        if last is None or mono - last >= QUOTE_REFRESH_SECONDS:
            if refresher.QUOTE_REFRESH_LOCK.acquire(blocking=False):
                try:
                    conn = dbm.connect()
                    try:
                        result = self.quote_refresher(conn, codes)
                    finally:
                        conn.close()
                    self._last_quote_refresh[market] = mono
                    stale = []
                    if isinstance(result, dict):
                        stale = list((result.get("stale") or {}).keys())
                    self._ui_notify({
                        "type": "marketQuotesUpdated",
                        "ok": not stale,
                        "stale": stale,
                        "at": _fmt(now),
                    })
                except Exception as e:
                    self._last_quote_refresh[market] = mono
                    print(f"[alert_engine] {market} 定向行情刷新失败（保留旧值）: {e}")
                finally:
                    refresher.QUOTE_REFRESH_LOCK.release()
            else:
                print(f"[alert_engine] {market} 行情刷新锁被占用，本轮不重复联网")

        quotes = self._load_quotes(codes)
        count = 0
        for alert in eligible:
            try:
                quote = quotes.get(alert["mkt_code"])
                if not quote or quote["price_scaled"] is None:
                    continue
                if not self._quote_is_fresh(
                        quote["quote_time"], market, market_date, now):
                    continue
                if str(alert["cond_type"]) == "pct":
                    if quote["change_pct_scaled"] is None:
                        continue
                    actual = cv.pct_from_scaled(quote["change_pct_scaled"])
                else:
                    actual = cv.point_from_scaled(quote["price_scaled"])
                if not alert_rules.matches(alert, actual):
                    continue
                if self._event_exists(alert["id"], market_date):
                    continue
                if self._record_and_notify(
                    alert, market_date,
                    alert_rules.cond_snapshot(alert),
                    alert_rules.format_actual(alert, actual),
                    now,
                ):
                    count += 1
            except Exception as e:
                print(f"[alert_engine] 指数规则 {alert['id']} 检查失败: {e}")
        return count

    def _load_quotes(self, codes):
        if not codes:
            return {}
        conn = dbm.connect()
        try:
            qmarks = ",".join("?" * len(codes))
            rows = conn.execute(
                f"SELECT mkt_code, price_scaled, change_pct_scaled, quote_time, fetched_at "
                f"FROM market_quotes WHERE mkt_code IN ({qmarks})",
                tuple(codes),
            ).fetchall()
            return {r["mkt_code"]: r for r in rows}
        finally:
            conn.close()

    def _quote_is_fresh(self, quote_time, market, market_date, now):
        qt = _parse_quote_time(quote_time)
        if qt is None:
            return False
        qdate = (mcal.ny_from_beijing(qt).date()
                 if market == "us" else qt.date())
        if qdate.isoformat() != str(market_date):
            return False
        age = (now - qt).total_seconds()
        return -FUTURE_TOLERANCE_SECONDS <= age <= QUOTE_MAX_AGE_SECONDS

    # ---------- 触发与通知 ----------
    def _event_exists(self, alert_id, trigger_date):
        conn = dbm.connect()
        try:
            return self._event_exists_conn(conn, alert_id, trigger_date)
        finally:
            conn.close()

    @staticmethod
    def _event_exists_conn(conn, alert_id, trigger_date):
        row = conn.execute(
            "SELECT 1 FROM alert_events WHERE alert_id = ? AND trigger_date = ? LIMIT 1",
            (alert_id, trigger_date),
        ).fetchone()
        return row is not None

    def _record_and_notify(self, alert, trigger_date, snapshot, actual_text, now):
        at = _fmt(now)
        event_id = None
        conn = dbm.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO alert_events (uuid, alert_id, trigger_date, triggered_at,"
                " cond_snapshot, actual_value, trigger_kind, notify_state, notify_result,"
                " notify_error, acked, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'realtime', 'skipped', 'pending', NULL, 0, ?)",
                (uuid.uuid4().hex, alert["id"], trigger_date, at,
                 snapshot, actual_text, at),
            )
            event_id = cur.lastrowid
            if str(alert["valid_type"]) == "until_trigger":
                cur2 = conn.execute(
                    "UPDATE alerts SET triggered_at = ?, updated_at = ? "
                    "WHERE id = ? AND deleted = 0 AND valid_type = 'until_trigger' "
                    "AND triggered_at IS NULL",
                    (at, at, alert["id"]),
                )
                if cur2.rowcount != 1:
                    conn.rollback()
                    return None
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            return None
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[alert_engine] 写入触发事件失败 alert={alert['id']}: {e}")
            return None
        finally:
            conn.close()

        self._ui_notify({
            "type": "dataUpdated",
            "ok": True,
            "mode": "alert",
            "changed": [alert["fund_code"] if str(alert["obj_type"]) == "fund"
                        else alert["mkt_code"]],
            "failed": [],
            "at": at,
        })
        self._notify_event(event_id, alert, snapshot, actual_text)
        return event_id

    def _notify_event(self, event_id, alert, snapshot, actual_text):
        try:
            conn = dbm.connect()
            try:
                enabled = dbm.get_setting(conn, NOTIFY_KEY)
            finally:
                conn.close()
            if str(enabled or "0") != "1":
                reason = ("通知偏好未设置" if enabled is None
                          else "用户关闭了盯盘提醒通知")
                self._mark_notify(event_id, "skipped", reason)
                return
            body = f"{alert_rules.object_label(alert)} {snapshot} · 当前 {actual_text}"
            try:
                result = self.notifier.send("小财通 · 盯盘提醒", body)
            except Exception as e:
                result = NotifyResult(False, str(e))
            if result is None:
                result = NotifyResult(False, "通知通道未返回结果")
            if result.submitted:
                self._mark_notify(event_id, "submitted", None)
            else:
                self._mark_notify(event_id, "failed", result.error or "通知发送失败")
        except Exception as e:
            print(f"[alert_engine] 通知结果更新失败 event={event_id}: {e}")

    def _mark_notify(self, event_id, result, error):
        legacy = "sent" if result == "submitted" else "skipped"
        conn = dbm.connect()
        try:
            conn.execute(
                "UPDATE alert_events SET notify_state = ?, notify_result = ?,"
                " notify_error = ? WHERE id = ?",
                (legacy, result, error, event_id),
            )
            conn.commit()
        finally:
            conn.close()

    def recover_pending(self, now=None):
        """启动/运行中把超时 pending 标记 failed；不重发，避免旧通知突然出现。"""
        now = now or self.now_fn()
        cutoff = _fmt(now - datetime.timedelta(seconds=PENDING_STALE_SECONDS))
        conn = dbm.connect()
        try:
            conn.execute(
                "UPDATE alert_events SET notify_state = 'skipped',"
                " notify_result = 'failed',"
                " notify_error = COALESCE(notify_error, '程序中断，通知未完成')"
                " WHERE notify_result = 'pending' AND created_at < ?",
                (cutoff,),
            )
            conn.commit()
        finally:
            conn.close()

    def tick(self, now=None):
        if not self._tick_lock.acquire(blocking=False):
            return {"skipped": True}
        try:
            now = now or self.now_fn()
            try:
                self.recover_pending(now)
            except Exception as e:
                print(f"[alert_engine] pending 清理失败（继续本轮）: {e}")
            nav_count = self._drain_nav(now)
            market_count = self._check_markets(now)
            return {"skipped": False, "nav": nav_count, "market": market_count}
        finally:
            self._tick_lock.release()

    def _ui_notify(self, payload):
        if not self.ui_notify:
            return
        try:
            self.ui_notify(payload)
        except Exception as e:
            print(f"[alert_engine] UI 通知失败（不影响引擎）: {e}")


_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def start(notifier=None, ui_notify=None):
    """模块级单例入口；Stage B 才由 main.py 调用。"""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = AlertEngine(notifier=notifier, ui_notify=ui_notify)
        return _ENGINE.start()


def stop():
    global _ENGINE
    with _ENGINE_LOCK:
        engine = _ENGINE
    return engine.stop() if engine else False
