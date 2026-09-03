# -*- coding: utf-8 -*-
"""数值缩放换算——全项目唯一换算入口（schema v2 精度方案，2026-09-03 用户确认）

原则（决策记录 2026-09-03）：
- 金额存整数分（元 ×100）
- 净值/累计净值/份额 ×10000 存整数（源为 4 位小数）
- 涨跌幅 ×100 存整数（2 位小数，带符号）
- 指数点位 ×100 存整数（2 位小数）
- 计算链全部使用 decimal.Decimal（从字符串构造），绝不使用 float 参与钱的计算
- 入库校验：超出约定小数位的数据抛错或跳过，防止异常源数据悄悄丢精度
"""
from decimal import Decimal, InvalidOperation

# 缩放因子（Decimal，一次定义全项目引用）
SCALE_MONEY = Decimal("100")      # 金额：元 → 分（用户确认，固定不变）
SCALE_NAV   = Decimal("10000")    # 净值/份额 → ×10000（4 位小数）
SCALE_PCT   = Decimal("10000")    # 涨跌幅 → ×10000
                                  # （2026-09-03 真测修正：原定 ×100 按"2 位小数"假设，
                                  #   实测天天基金 2018-2019 年历史日增长率为 4 位小数
                                  #   （如 -2.1586），×100 会拒收丢数据；×10000 兼容 2~4 位全部真实数据）
SCALE_POINT = Decimal("10000")    # 指数点位 → ×10000（与净值同容差，行情接入时以真测为准）


def to_scaled(x, scale, label="数值"):
    """任意输入（Decimal/字符串/整数/浮点）→ 缩放整数。

    - 从字符串/Decimal 构造，保证与源文本一致；
    - 浮点输入经 str(x) 取最短表示（如 1.5191 的 float64 存为 1.5191 时 str 恰好还原），
      还原不了的情形由下方小数位校验兜底；
    - 缩放后仍非整数（源数据超过约定小数位）→ 抛 ValueError，由调用方记录日志处理。
    """
    if x is None:
        raise ValueError(f"{label} 为空")
    try:
        d = Decimal(str(x))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} 不是可解析的数字：{x!r}")
    v = d * scale
    if v != v.to_integral_value():
        raise ValueError(f"{label} {x} 超过约定小数位（×{scale} 后仍非整数），已拒绝入库")
    return int(v)


def from_scaled(v, scale):
    """缩放整数 → Decimal（所有比较/计算从这里开始，全程无浮点）"""
    return Decimal(int(v)) / scale


# ---- 常用换算 ----
def yuan_to_fen(x):
    return to_scaled(x, SCALE_MONEY, "金额")


def fen_to_yuan(v):
    return from_scaled(v, SCALE_MONEY)


def nav_to_scaled(x):
    return to_scaled(x, SCALE_NAV, "净值")


def nav_from_scaled(v):
    return from_scaled(v, SCALE_NAV)


def shares_to_scaled(x):
    return to_scaled(x, SCALE_NAV, "份额")


def shares_from_scaled(v):
    return from_scaled(v, SCALE_NAV)


def pct_to_scaled(x):
    return to_scaled(x, SCALE_PCT, "涨跌幅")


def pct_from_scaled(v):
    return from_scaled(v, SCALE_PCT)


def point_to_scaled(x):
    return to_scaled(x, SCALE_POINT, "点位")


def point_from_scaled(v):
    return from_scaled(v, SCALE_POINT)


def format_yuan(cents, decimals=2):
    """整数分 → 展示字符串（如 4300000 → '43000.00'）；负值带 - 号"""
    return "{:.{}f}".format(fen_to_yuan(cents), decimals)
