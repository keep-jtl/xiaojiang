// 组合体检纯计算模块（手机端，无网络、不写库、无副作用）。
//
// 方案 A（GPT《Android 组合体检方案 A 决策》正式批准）：
// - 手机端只存储最新净值（holdings 行冗余 nav_scaled/prev_nav_scaled/daily_pct_scaled），
//   没有历史净值序列，因此只计算现有数据能可靠支撑的指标：
//   持仓结构、风险集中、影响最大、值得关注（客观数据）；
// - 风格重复两端都缺底层重仓股，继续诚实占位；
// - 近期风险保留独立模块，明确标注「手机端暂未支持」，不使用「加载中/数据异常/即将开放」
//   等易误导文案，不填任何假数字；
// - 全程整数缩放（金额=分、净值/份额=×10000、百分比=百分比数值×10000），
//   百分比用整数 roundDiv 一次算到缩放精度，与 Python ROUND_HALF_UP 口径对齐。
import {
  marketValueCents, todayProfitCents, roundDiv
} from './money.js';

// 比例（如 0.1234）→ 百分比数值×10000（12.34% → 123400），与 Python _pct_to_int 对齐。
function ratioToPctScaled(numerator, denominator) {
  if (!denominator) return null;
  return roundDiv(numerator * 1000000, denominator);
}

// 近期风险能力说明（文案严格按 GPT 决策，不自行改写）。
function recentRiskUnsupported() {
  return {
    supported: false,
    title: '近期风险变化',
    status: '手机端暂未支持',
    intro: '了解持仓基金近期的净值波动、回撤与连续涨跌情况。',
    history_title: '历史净值分析',
    history_intro: '当前 Android 版本尚未接入历史净值序列，因此暂时无法提供以下历史指标：',
    missing_metrics: [
      '近 20 个有效净值日表现',
      '近 252 个有效净值日最大回撤',
      '近 20 日波动率',
      '连续下跌天数'
    ],
    full_title: '需要查看完整分析？',
    full_note: 'Windows 版已支持完整的近期风险分析，可以在电脑端查看上述指标。'
  };
}

// 单只持仓的计算结果。
function positionItem(h) {
  const hasNav = h.nav_scaled != null;
  const valueCents = hasNav ? marketValueCents(h.shares_scaled, h.nav_scaled) : null;
  const profitCents = valueCents == null ? null : valueCents - (h.cost_cents || 0);
  const profitPctScaled = profitCents == null
    ? null : ratioToPctScaled(profitCents, h.cost_cents);
  const contributionCents = hasNav
    ? todayProfitCents(h.shares_scaled, h.nav_scaled, h.prev_nav_scaled)
    : null;
  return {
    code: h.fund_code,
    name: h.name,
    fund_type: h.fund_type || '',
    has_nav: hasNav,
    latest_nav_date: hasNav ? (h.nav_date || null) : null,
    latest_nav_scaled: hasNav ? h.nav_scaled : null,
    value_cents: valueCents,
    cost_cents: h.cost_cents || 0,
    profit_cents: profitCents,
    profit_pct_scaled: profitPctScaled,
    contribution_cents: contributionCents,
    weight_pct_scaled: null // 汇总后回填
  };
}

// 组合体检主入口：输入 holding 状态持仓行数组，输出结构化报告（可 JSON 序列化）。
export function buildReport(holdings, opts) {
  const rows = (holdings || []).filter((h) => h.status === 'holding');
  if (!rows.length) {
    return {
      empty: true,
      message: '当前没有 holding 状态的持仓，组合体检暂无可分析数据。',
      style_overlap: {
        available: false,
        message: '暂无法进行风格重叠分析，缺少底层持仓数据。'
      },
      recent_risk: recentRiskUnsupported()
    };
  }

  const items = rows.map(positionItem);
  const valued = items.filter((m) => m.value_cents != null);
  const missing = items.filter((m) => m.value_cents == null);
  const hasAnyValue = valued.length > 0;
  // 完整性（GPT 边界要求一）：只有全部持仓都取到净值，才能给出「完整组合」口径。
  // 部分缺失时不得把已知部分合计冒充组合总资产，也不得重新归一化权重冒充完整占比。
  const isComplete = items.length > 0 && missing.length === 0;

  const totalCost = items.reduce((acc, m) => acc + m.cost_cents, 0);
  const knownValue = hasAnyValue
    ? valued.reduce((acc, m) => acc + m.value_cents, 0) : null;
  const knownCost = hasAnyValue
    ? valued.reduce((acc, m) => acc + m.cost_cents, 0) : null;
  const knownProfit = hasAnyValue ? knownValue - knownCost : null;

  // 完整组合口径：仅在数据完整时成立；无任何净值时同样为 null（不按 0 冒充全亏）。
  const totalValue = isComplete ? knownValue : null;
  const totalProfit = isComplete ? totalValue - totalCost : null;

  // 权重：只在数据完整时计算完整组合权重，缺失项与不完整组合均无权重。
  for (const m of items) {
    m.weight_pct_scaled = (isComplete && m.value_cents != null)
      ? ratioToPctScaled(m.value_cents, totalValue) : null;
  }

  const ordered = valued.slice().sort((a, b) => b.value_cents - a.value_cents);
  const top3 = ordered.slice(0, 3);
  const top3Value = top3.reduce((acc, m) => acc + m.value_cents, 0);
  const top3ConcentrationPctScaled = isComplete
    ? ratioToPctScaled(top3Value, totalValue) : null;

  // 净值日期口径：最新净值日期是否统一（QDII 可能晚一天）。
  const navDates = Array.from(new Set(valued.map((m) => m.latest_nav_date)
    .filter((d) => d != null))).sort();
  const dateUniform = navDates.length === 1;
  const contributors = valued.filter((m) => m.contribution_cents != null);
  const knownLatestTotal = hasAnyValue
    ? contributors.reduce((acc, m) => acc + m.contribution_cents, 0) : null;
  // 组合合计的最新净值变动：只有「数据完整 + 日期统一」才成立
  const latestTotal = (dateUniform && isComplete) ? knownLatestTotal : null;

  // 数据截止（GPT 边界要求二）：客观给出最新净值日期与距今天数，不发明「过期」阈值。
  const dataAsOf = navDates.length ? navDates[navDates.length - 1] : null;
  let dataAgeDays = null;
  if (dataAsOf) {
    const t = (opts && opts.today)
      ? new Date(opts.today + 'T00:00:00') : new Date();
    const d = new Date(dataAsOf + 'T00:00:00');
    if (!Number.isNaN(d.getTime()) && !Number.isNaN(t.getTime())) {
      dataAgeDays = Math.round((t.getTime() - d.getTime()) / 86400000);
    }
  }

  return {
    empty: false,
    data_basis: {
      nav_basis: 'unit_nav',
      mobile_note: '手机端仅存储最新一条净值，未存储历史净值序列；历史指标请在 Windows 端查看。'
    },
    summary: {
      fund_count: items.length,
      complete: isComplete,
      valued_count: valued.length,
      missing_nav_count: missing.length,
      missing_nav_codes: missing.map((m) => m.code),
      total_value_cents: totalValue,
      total_cost_cents: totalCost,
      total_profit_cents: totalProfit,
      total_profit_pct_scaled: isComplete
        ? ratioToPctScaled(totalProfit, totalCost) : null,
      known_value_cents: knownValue,
      known_cost_cents: knownCost,
      known_profit_cents: knownProfit,
      known_note: missing.length
        ? `${items.length} 只持仓中有 ${missing.length} 只缺少有效净值，暂无法计算完整组合总市值。`
        : null,
      data_as_of: dataAsOf,
      data_age_days: dataAgeDays,
      largest_holding_code: (ordered.length && isComplete) ? ordered[0].code : null,
      largest_holding_weight_pct_scaled:
        (ordered.length && isComplete) ? ordered[0].weight_pct_scaled : null,
      top3_concentration_pct_scaled: top3ConcentrationPctScaled,
      nav_date_uniform: dateUniform,
      nav_dates: navDates
    },
    holdings_structure: items.map((m) => ({
      code: m.code,
      name: m.name,
      fund_type: m.fund_type,
      has_nav: m.has_nav,
      value_cents: m.value_cents,
      weight_pct_scaled: m.weight_pct_scaled,
      profit_cents: m.profit_cents,
      profit_pct_scaled: m.profit_pct_scaled,
      latest_nav_date: m.latest_nav_date
    })),
    concentration: {
      items: isComplete ? ordered.map((m) => ({
        code: m.code,
        name: m.name,
        weight_pct_scaled: m.weight_pct_scaled
      })) : [],
      top3_codes: isComplete ? top3.map((m) => m.code) : [],
      top3_concentration_pct_scaled: top3ConcentrationPctScaled,
      max_single_weight_pct_scaled:
        (ordered.length && isComplete) ? ordered[0].weight_pct_scaled : null,
      complete: isComplete,
      note: isComplete
        ? '此为基金持仓集中度，非底层资产风险分散度；基金数量不等于底层分散。'
        : '部分持仓缺少有效净值，无法计算完整组合集中度；不把已知部分权重当作完整组合占比。'
    },
    style_overlap: {
      available: false,
      message: '暂无法进行可靠的风格重叠分析，当前缺少基金底层持仓（重仓股）数据。'
    },
    impact: {
      latest: {
        nav_date_uniform: dateUniform,
        items: valued.map((m) => ({
          code: m.code,
          name: m.name,
          contribution_cents: m.contribution_cents,
          nav_date: m.latest_nav_date
        })),
        total_cents: latestTotal,
        known_total_cents: knownLatestTotal,
        complete: isComplete,
        label: (dateUniform && isComplete)
          ? '按统一净值日期计算'
          : (dateUniform ? '按已取得净值的基金计算（部分基金缺少净值）'
            : '按各基金最新已公布净值计算')
      },
      cumulative: {
        items: valued.slice().sort((a, b) => b.profit_cents - a.profit_cents).map((m) => ({
          code: m.code,
          name: m.name,
          profit_cents: m.profit_cents,
          profit_pct_scaled: m.profit_pct_scaled
        })),
        total_cents: totalProfit,
        complete: isComplete
      }
    },
    recent_risk: recentRiskUnsupported(),
    watchlist: {
      no_rating: true,
      note: '第一版只展示客观数据，不设定风险阈值、不做风险评级。',
      objective: {
        max_single_weight_pct_scaled:
          (ordered.length && isComplete) ? ordered[0].weight_pct_scaled : null,
        top3_concentration_pct_scaled: top3ConcentrationPctScaled,
        latest_nav_dates: navDates,
        latest_contribution: valued.map((m) => ({
          code: m.code,
          contribution_cents: m.contribution_cents,
          nav_date: m.latest_nav_date
        }))
      }
    }
  };
}
