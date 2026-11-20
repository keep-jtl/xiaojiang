# -*- coding: utf-8 -*-
"""组合体检纯计算模块（无网络、不写库、无副作用）。

设计原则（对齐 GPT《组合体检 Plan 审核结论》）：
- 输入由调用方从库读出后传入，本模块只做计算，便于单元测试与双端对照；
- 仅统计 holding 状态基金，watch/closed 不混入当前持仓市值；
- 内部全程 Decimal、不提前四舍五入；输出关键数值用整数缩放：
  金额=分、净值=×10000、百分比=×10000（UI 再按口径显示 2/4 位）；
- 数据不足时给 available=False 与诚实说明，绝不填充示例数字；
- 第一版「值得关注」只列客观数据，不发明风险阈值、不做风险评级。

口径标注（GPT 技术修正 1/2）：
- 市值/权重基于单位净值（unit_nav）；
- 日收益率用数据源官方日增长率（daily_pct，含分红口径），用于波动率/连续下跌；
- 最大回撤基于单位净值序列，未计分红再投资，命名为「单位净值历史回撤」，
  不代表完整投资总回报回撤（当前库无复权/累计净值）。
"""
from decimal import Decimal, ROUND_HALF_UP

# 缩放因子（与 converters 一致，此处本地引用避免与 IO 耦合）
_NAV = Decimal("10000")
_MONEY = Decimal("100")
_PCT = Decimal("10000")
_HUNDRED = Decimal("100")

# 观测窗口
_WINDOW = 20            # 近 20 个有效净值/收益率
_VOL_MIN = 20           # 波动率需要 20 个有效日收益率
_RETURN_MIN = 21        # 近 20 日区间表现需要 21 个净值（20 个间隔）


def _d(v, scale):
    return Decimal(int(v)) / scale


def _pct_to_int(value_decimal):
    """Decimal 比例（如 0.1234=12.34%）→ 百分比数值×10000 整数（12.34 → 123400）。

    与项目 pct_scaled 口径一致（db.py：+1.82% → 18200），即百分比数值×10000。
    """
    return int((value_decimal * _PCT * _HUNDRED).to_integral_value(
        rounding=ROUND_HALF_UP))


def _market_value_cents(shares_scaled, nav_scaled):
    s = _d(shares_scaled, _NAV)
    n = _d(nav_scaled, _NAV)
    return int((s * n * _MONEY).to_integral_value(rounding=ROUND_HALF_UP))


def _weight(value_cents, total_cents):
    return Decimal(int(value_cents)) / Decimal(int(total_cents))


def _max_drawdown(nav_rows):
    """单位净值序列最大回撤（比例 Decimal），返回 (比例, 峰值日期, 谷值日期)。"""
    peak = None
    peak_date = None
    worst = Decimal("0")
    worst_dates = (None, None)
    for r in nav_rows:
        nav = _d(r["nav"], _NAV)
        if peak is None or nav > peak:
            peak, peak_date = nav, r["date"]
        dd = nav / peak - 1
        if dd < worst:
            worst = dd
            worst_dates = (peak_date, r["date"])
    return worst, worst_dates[0], worst_dates[1]


def _down_streak(nav_rows):
    """从最新净值往前，官方日增长率为负的连续天数（缺失不计）。"""
    n = 0
    for r in reversed(nav_rows):
        pct = r.get("pct")
        if pct is None:
            break
        if pct < 0:
            n += 1
        else:
            break
    return n


def _volatility(nav_rows):
    """近20个有效日收益率的样本标准差（÷19，不年化）；不足返回 None。

    日收益率取官方日增长率：pct_scaled/10000 得百分比数值，再/100 得比例，含分红口径。
    """
    daily = [_d(r["pct"], _PCT) / _HUNDRED for r in nav_rows
             if r.get("pct") is not None]
    if len(daily) < _VOL_MIN:
        return None
    window = daily[-_VOL_MIN:]
    mean = sum(window) / Decimal(len(window))
    var = sum((x - mean) ** 2 for x in window) / Decimal(len(window) - 1)
    return var.sqrt()


def _return_20(nav_rows):
    """近20个有效净值区间表现（比例）；不足21个净值返回 None。"""
    if len(nav_rows) < _RETURN_MIN:
        return None
    latest = _d(nav_rows[-1]["nav"], _NAV)
    base = _d(nav_rows[-_RETURN_MIN]["nav"], _NAV)
    return latest / base - 1


def _position_metrics(p):
    """单只基金的全部计算结果。"""
    rows = p["nav_rows"]
    nav_count = len(rows)
    latest = rows[-1] if rows else None
    prev = rows[-2] if len(rows) >= 2 else None
    has_nav = latest is not None
    latest_nav_scaled = latest["nav"] if has_nav else None
    latest_date = latest["date"] if has_nav else None

    # 市值/盈亏：无净值时为 None，不按 0 冒充全亏（与 Android positionItem 对齐）
    if has_nav:
        value_cents = _market_value_cents(p["shares_scaled"], latest_nav_scaled)
        profit_cents = value_cents - int(p["cost_cents"])
        profit_pct_scaled = _pct_to_int(
            Decimal(int(profit_cents)) / Decimal(int(p["cost_cents"])))
    else:
        value_cents = None
        profit_cents = None
        profit_pct_scaled = None

    # 最新已公布净值变动贡献（带日期，不做跨日合计）
    latest_contribution = None
    if has_nav and prev is not None:
        s = _d(p["shares_scaled"], _NAV)
        delta = _d(latest["nav"], _NAV) - _d(prev["nav"], _NAV)
        latest_contribution = int(
            (s * delta * _MONEY).to_integral_value(rounding=ROUND_HALF_UP)
        )

    if rows:
        mdd_ratio, mdd_peak, mdd_trough = _max_drawdown(rows)
        mdd_pct_scaled = _pct_to_int(mdd_ratio)
    else:
        mdd_pct_scaled, mdd_peak, mdd_trough = None, None, None
    ret20 = _return_20(rows)
    vol = _volatility(rows)
    streak = _down_streak(rows)

    insuff = []
    if nav_count < _RETURN_MIN:
        insuff.append(f"净值观测 {nav_count} 条，不足 {_RETURN_MIN} 条，近20日表现与波动率不计算")

    return {
        "code": p["code"],
        "name": p["name"],
        "fund_type": p["fund_type"],
        "has_nav": has_nav,
        "nav_count": nav_count,
        "latest_nav_date": latest_date,
        "prev_nav_date": prev["date"] if prev else None,
        "latest_nav_scaled": latest_nav_scaled,
        "value_cents": value_cents,
        "profit_cents": profit_cents,
        "profit_pct_scaled": profit_pct_scaled,
        "latest_contribution_cents": latest_contribution,
        "max_drawdown_pct_scaled": mdd_pct_scaled,
        "max_drawdown_peak_date": mdd_peak,
        "max_drawdown_trough_date": mdd_trough,
        "return_20_pct_scaled": _pct_to_int(ret20) if ret20 is not None else None,
        "volatility_20_pct_scaled": _pct_to_int(vol) if vol is not None else None,
        "down_streak_days": streak,
        "insufficiencies": insuff,
    }


def build_report(positions, today=None):
    """组合体检主入口。

    positions: list[dict]，每项含
      code/name/fund_type/shares_scaled/cost_cents/
      nav_rows=[{"date","nav","pct"}]（升序，nav 为×10000整数，pct 可空）
    today: 可选 date，用于计算「数据距今天数」，测试可注入固定日期
    返回结构化报告（全部可 JSON 序列化，关键数值为整数缩放）。
    """
    import datetime as _dt
    today_date = today if today is not None else _dt.date.today()
    if not positions:
        return {
            "empty": True,
            "message": "当前没有 holding 状态的持仓，组合体检暂无可分析数据。",
            "style_overlap": {"available": False,
                              "message": "暂无法进行风格重叠分析，缺少底层持仓数据。"},
        }

    metrics = [_position_metrics(p) for p in positions]
    valued = [m for m in metrics if m["value_cents"] is not None]
    missing = [m for m in metrics if m["value_cents"] is None]
    has_any_value = len(valued) > 0
    # 完整性（GPT 边界要求）：只有全部持仓都取到净值，才能给出「完整组合」口径。
    # 部分缺失时不得把已知部分合计冒充组合总资产，也不得把部分持仓权重
    # 重新归一化后冒充完整组合占比。
    is_complete = len(metrics) > 0 and len(missing) == 0

    total_cost = sum(int(p["cost_cents"]) for p in positions)
    # 已知部分小计（有明确标签，仅用于展示「已取到净值的那部分」）
    valued_codes = {m["code"] for m in valued}
    known_value = sum(m["value_cents"] for m in valued) if has_any_value else None
    known_cost = (sum(int(p["cost_cents"]) for p in positions
                      if p["code"] in valued_codes)
                  if has_any_value else None)
    known_profit = (known_value - known_cost
                    if has_any_value else None)

    # 完整组合口径：仅在数据完整时成立
    total_value = known_value if is_complete else None
    total_profit = (total_value - total_cost) if is_complete else None

    # 权重：只在数据完整时计算完整组合权重；缺失项与不完整组合均无权重
    for m in metrics:
        if is_complete and m["value_cents"] is not None:
            m["weight_pct_scaled"] = _pct_to_int(_weight(m["value_cents"], total_value))
        else:
            m["weight_pct_scaled"] = None

    ordered = sorted(valued, key=lambda m: -m["value_cents"])
    top3 = ordered[:3]
    if is_complete:
        top3_concentration = sum(m["value_cents"] for m in top3) / Decimal(total_value)
        top3_concentration_pct_scaled = _pct_to_int(top3_concentration)
    else:
        top3_concentration_pct_scaled = None

    # 净值日期口径（GPT 修正1）：最新净值日期是否统一
    nav_dates = sorted({m["latest_nav_date"] for m in valued})
    date_uniform = len(nav_dates) == 1
    latest_total = sum(m["latest_contribution_cents"] for m in valued
                       if m["latest_contribution_cents"] is not None)
    # 组合合计的最新净值变动：只有「数据完整 + 日期统一」才成立
    latest_total_full = latest_total if (date_uniform and is_complete) else None

    # 数据截止（GPT 边界要求二）：客观陈述最新净值日期与距今天数，不发明「过期」阈值
    data_as_of = max(nav_dates) if nav_dates else None
    data_age_days = None
    if data_as_of:
        try:
            data_age_days = (today_date - _dt.date.fromisoformat(data_as_of)).days
        except (ValueError, TypeError):
            data_age_days = None

    # 持仓结构：有净值按市值降序，无净值项排后并诚实标注
    structure_order = ordered + [m for m in metrics if m["value_cents"] is None]
    structure = [{
        "code": m["code"], "name": m["name"], "fund_type": m["fund_type"],
        "has_nav": m["has_nav"],
        "value_cents": m["value_cents"], "weight_pct_scaled": m["weight_pct_scaled"],
        "profit_cents": m["profit_cents"], "profit_pct_scaled": m["profit_pct_scaled"],
        "latest_nav_date": m["latest_nav_date"], "nav_count": m["nav_count"],
    } for m in structure_order]

    return {
        "empty": False,
        "data_basis": {
            "nav_basis": "unit_nav",
            "return_basis": "official_daily_growth",
            "drawdown_note": "最大回撤基于单位净值，未计分红再投资，非完整投资总回报回撤。",
        },
        "summary": {
            "fund_count": len(metrics),
            "complete": is_complete,
            "valued_count": len(valued),
            "missing_nav_count": len(missing),
            "missing_nav_codes": [m["code"] for m in missing],
            "total_value_cents": total_value,
            "total_cost_cents": total_cost,
            "total_profit_cents": total_profit,
            "total_profit_pct_scaled": _pct_to_int(
                Decimal(total_profit) / Decimal(total_cost))
                if is_complete else None,
            # 已知部分小计（有明确标签，不代表完整组合）
            "known_value_cents": known_value,
            "known_cost_cents": known_cost,
            "known_profit_cents": known_profit,
            "known_note": (
                f"{len(metrics)} 只持仓中有 {len(missing)} 只缺少有效净值，"
                f"暂无法计算完整组合总市值。" if missing else None),
            "data_as_of": data_as_of,
            "data_age_days": data_age_days,
            "largest_holding_code": ordered[0]["code"] if (ordered and is_complete) else None,
            "largest_holding_weight_pct_scaled":
                ordered[0]["weight_pct_scaled"] if (ordered and is_complete) else None,
            "top3_concentration_pct_scaled": top3_concentration_pct_scaled,
            "nav_date_uniform": date_uniform,
            "nav_dates": nav_dates,
        },
        "holdings_structure": structure,
        "concentration": {
            "items": [{"code": m["code"], "name": m["name"],
                       "weight_pct_scaled": m["weight_pct_scaled"]} for m in ordered]
            if is_complete else [],
            "top3_codes": [m["code"] for m in top3] if is_complete else [],
            "top3_concentration_pct_scaled": top3_concentration_pct_scaled,
            "max_single_weight_pct_scaled":
                ordered[0]["weight_pct_scaled"] if (ordered and is_complete) else None,
            "complete": is_complete,
            "note": ("此为基金持仓集中度，非底层资产风险分散度；基金数量不等于底层分散。"
                     if is_complete else
                     "部分持仓缺少有效净值，无法计算完整组合集中度；"
                     "不把已知部分权重当作完整组合占比。"),
        },
        "style_overlap": {
            "available": False,
            "message": "暂无法进行可靠的风格重叠分析，当前缺少基金底层持仓（重仓股）数据。",
        },
        "impact": {
            "latest": {
                "nav_date_uniform": date_uniform,
                "items": [{"code": m["code"], "name": m["name"],
                           "contribution_cents": m["latest_contribution_cents"],
                           "nav_date": m["latest_nav_date"],
                           "prev_nav_date": m["prev_nav_date"]} for m in valued],
                "total_cents": latest_total_full,
                "known_total_cents": latest_total if has_any_value else None,
                "complete": is_complete,
                "label": ("按统一净值日期计算" if (date_uniform and is_complete) else
                          ("按已取得净值的基金计算（部分基金缺少净值）"
                           if date_uniform else
                           "按各基金最新已公布净值计算")),
            },
            "cumulative": {
                "items": [{"code": m["code"], "name": m["name"],
                           "profit_cents": m["profit_cents"],
                           "profit_pct_scaled": m["profit_pct_scaled"]}
                          for m in sorted(valued, key=lambda x: -x["profit_cents"])],
                "total_cents": total_profit,
                "complete": is_complete,
            },
        },
        "recent_risk": [{
            "code": m["code"], "name": m["name"], "nav_count": m["nav_count"],
            "return_20_pct_scaled": m["return_20_pct_scaled"],
            "max_drawdown_pct_scaled": m["max_drawdown_pct_scaled"],
            "max_drawdown_basis": "unit_nav",
            "max_drawdown_peak_date": m["max_drawdown_peak_date"],
            "max_drawdown_trough_date": m["max_drawdown_trough_date"],
            "volatility_20_pct_scaled": m["volatility_20_pct_scaled"],
            "down_streak_days": m["down_streak_days"],
            "insufficiencies": m["insufficiencies"],
        } for m in metrics],
        "watchlist": {
            "no_rating": True,
            "note": "第一版只展示客观数据，不设定风险阈值、不做风险评级。",
            "objective": {
                "max_single_weight_pct_scaled":
                    ordered[0]["weight_pct_scaled"] if (ordered and is_complete) else None,
                "top3_concentration_pct_scaled": top3_concentration_pct_scaled,
                "max_drawdown_pct_scaled": min(
                    (m["max_drawdown_pct_scaled"] for m in valued),
                    default=None),
                "latest_nav_dates": nav_dates,
                "latest_contribution": [
                    {"code": m["code"],
                     "contribution_cents": m["latest_contribution_cents"],
                     "nav_date": m["latest_nav_date"]} for m in valued],
            },
        },
    }
