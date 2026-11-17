# -*- coding: utf-8 -*-
"""盯盘提醒的纯规则函数（1B）。

只做日期/条件判断与文案，不连数据库、不联网、不依赖 pywebview。
引擎与 API 共用，避免“页面一套、引擎一套”的口径漂移。
"""
import datetime
from decimal import Decimal, InvalidOperation

VALID_TYPES = {"once", "until_trigger", "ongoing", "range"}


def _as_date(value):
    """datetime/date/ISO 字符串 → date；无效返回 None。"""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if value in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _flag(row, key):
    try:
        return bool(int(row[key] or 0))
    except (KeyError, TypeError, ValueError):
        return False


def is_active_on(rule, on_date):
    """规则在 on_date 是否有效。

    - 指数由引擎传入市场当地交易日（美股=美东日期）；
    - 基金由引擎传入检查当天（北京时间）；
    - until_trigger 在 triggered_at 非空后失效；
    - ongoing 兼容旧数据，但本阶段不新增前端入口。
    """
    d = _as_date(on_date)
    if d is None or not _flag(rule, "enabled") or _flag(rule, "deleted"):
        return False

    valid = str(rule["valid_type"] or "")
    start = _as_date(rule["start_date"])
    end = _as_date(rule["end_date"])

    if valid == "until_trigger":
        if rule["triggered_at"]:
            return False
        return start is None or d >= start
    if valid == "ongoing":
        return start is None or d >= start
    if valid == "once":
        return start is not None and end is not None and start == end == d
    if valid == "range":
        return start is not None and end is not None and start <= d <= end
    return False


def cond_value(rule):
    """用户输入的正数条件 → Decimal；异常返回 None（调用方跳过，不猜）。"""
    try:
        value = Decimal(str(rule["cond_value"]))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not value.is_finite() or value <= 0:
        return None
    return value


def matches(rule, actual):
    """actual 为当日涨跌幅（带符号百分比）或数值线；方向按 1A 的 up/down 语义。"""
    if actual is None:
        return False
    try:
        actual = actual if isinstance(actual, Decimal) else Decimal(str(actual))
    except (InvalidOperation, TypeError, ValueError):
        return False
    value = cond_value(rule)
    if value is None:
        return False
    up = str(rule["direction"]) == "up"
    if str(rule["cond_type"]) == "pct":
        return actual >= value if up else actual <= -value
    return actual >= value if up else actual <= value


def cond_snapshot(rule):
    """触发时的条件快照；文案与 1A 提醒页保持一致。"""
    is_fund = str(rule["obj_type"]) == "fund"
    value = str(rule["cond_value"])
    if str(rule["cond_type"]) == "pct":
        direction = "涨超" if str(rule["direction"]) == "up" else "跌超"
        return f"单日{direction} {value}%"
    direction = "涨破" if str(rule["direction"]) == "up" else "跌破"
    unit = "元" if is_fund else "点"
    return f"{direction} {value} {unit}"


def format_actual(rule, actual):
    """实际值展示：涨跌幅带符号 2 位；基金净值 4 位；指数点位 2 位。"""
    value = actual if isinstance(actual, Decimal) else Decimal(str(actual))
    if str(rule["cond_type"]) == "pct":
        return f"{value:+.2f}%"
    if str(rule["obj_type"]) == "fund":
        return format(value.quantize(Decimal("0.0001")), "f")
    return format(value.quantize(Decimal("0.01")), "f")


def object_label(rule):
    """提醒对象显示名；兼容测试里没有 join 别名的行。"""
    is_fund = str(rule["obj_type"]) == "fund"
    try:
        if is_fund:
            return rule["fund_name"] or rule["fund_code"]
        return rule["mkt_name"] or rule["mkt_code"]
    except (KeyError, IndexError):
        return (rule["fund_code"] if is_fund else rule["mkt_code"]) or ""
