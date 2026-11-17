// 手机端盯盘规则：纯计算 + SQLite 持久化；不把“检查过”伪装成“已经触发”。
// 指数取真实行情（腾讯公开源）；基金取本机最新已保存净值。
import {
  getAlertRules, addAlertRule, setAlertRuleEnabled, deleteAlertRule,
  markAlertRuleState, addAlertEvent, getAlertEvents
} from './db.js';

export const COND_LABELS = { pct: '日涨跌幅', line: '价格/净值' };
export const DIR_LABELS = { up: '达到或高于', down: '达到或低于' };
export const VALID_LABELS = { until_trigger: '条件消失后可再次触发', once: '触发一次后关闭' };

export function ruleText(rule) {
  const cond = COND_LABELS[rule.cond_type] || rule.cond_type;
  const dir = DIR_LABELS[rule.direction] || rule.direction;
  const value = rule.cond_type === 'pct' ? rule.cond_value + '%' : '¥' + rule.cond_value;
  return cond + ' ' + dir + ' ' + value;
}

export function ruleObjectText(rule) {
  return rule.obj_name + (rule.obj_type === 'fund' ? '（基金）' : '（指数）');
}

function num(v, fallback = null) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

// 返回 {triggers, evaluations}。triggers 只包含“本次从未触发变为触发”的规则。
export function evaluateRules(rules, quotes, holdings, now = new Date()) {
  const quoteByCode = {};
  for (const q of (quotes?.items || [])) if (q.code) quoteByCode[q.code] = q;
  const holdingByCode = {};
  for (const h of (holdings || [])) if (h.status === 'holding') holdingByCode[h.fund_code] = h;
  const triggers = [];
  const evaluations = [];

  for (const rule of (rules || [])) {
    if (!Number(rule.enabled)) continue;
    if (rule.valid_until && String(rule.valid_until) < now.toISOString().slice(0, 10)) {
      evaluations.push({ rule, state: false, text: '已过有效期' });
      continue;
    }

    let actual = null;
    let display = '—';
    if (rule.obj_type === 'index') {
      const q = quoteByCode[rule.obj_code];
      if (q) {
        actual = rule.cond_type === 'pct' ? num(q.pct) : num(q.price);
        display = rule.cond_type === 'pct'
          ? (actual == null ? '—' : (actual > 0 ? '+' : '') + actual.toFixed(2) + '%')
          : (actual == null ? '—' : '¥' + actual.toFixed(2));
      }
    } else if (rule.obj_type === 'fund') {
      const h = holdingByCode[rule.obj_code];
      if (h && h.nav_scaled != null) {
        actual = rule.cond_type === 'pct'
          ? (h.daily_pct_scaled == null ? null : h.daily_pct_scaled / 10000)
          : h.nav_scaled / 10000;
        display = rule.cond_type === 'pct'
          ? (actual == null ? '—' : (actual > 0 ? '+' : '') + actual.toFixed(2) + '%')
          : '¥' + actual.toFixed(4);
      }
    }

    if (actual == null) {
      evaluations.push({ rule, state: false, text: '暂无可用行情/净值' });
      continue;
    }
    const threshold = Number(rule.cond_value);
    const hit = rule.direction === 'up' ? actual >= threshold : actual <= threshold;
    evaluations.push({ rule, state: hit, actual, display, text: display });
    if (hit && !Number(rule.last_state)) {
      triggers.push({ rule, actual, display });
    }
  }
  return { triggers, evaluations };
}

export async function persistTrigger(rule, display, nowStr) {
  const title = '小财通 · 盯盘提醒';
  const body = ruleObjectText(rule) + '：' + ruleText(rule) + '，当前 ' + display;
  await addAlertEvent({
    rule_id: rule.id,
    title,
    body,
    trigger_value: display,
    triggered_at: nowStr
  });
  await markAlertRuleState(rule.id, true, nowStr);
  if (rule.valid_type === 'once') await setAlertRuleEnabled(rule.id, false);
  return { title, body };
}

export {
  getAlertRules, addAlertRule, setAlertRuleEnabled, deleteAlertRule, getAlertEvents
};
