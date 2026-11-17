// 金额/净值/份额统一用整数缩放，避免 JS 浮点误差进入账本。
// 金额：分；净值/份额：×10000；涨跌幅：×10000（1.23% -> 12300）。

export function parseScaled(value, decimals = 4) {
  const s = String(value == null ? '' : value).trim().replace(/,/g, '');
  const m = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(s);
  if (!m || (!m[2] && !m[3])) return NaN;
  const sign = m[1] === '-' ? -1 : 1;
  const intPart = m[2] || '0';
  let frac = m[3] || '';
  let digits;
  if (frac.length <= decimals) {
    digits = intPart + frac.padEnd(decimals, '0');
  } else {
    const keep = frac.slice(0, decimals);
    const next = Number(frac[decimals] || '0');
    digits = intPart + keep.padEnd(decimals, '0');
    let n = BigInt(digits);
    if (next >= 5) n += 1n;
    return sign * Number(n);
  }
  return sign * Number(BigInt(digits));
}

export function roundDiv(n, d) {
  if (!d) return NaN;
  return n >= 0 ? Math.floor(n / d + 0.5) : -Math.floor(-n / d + 0.5);
}

export function costCents(priceScaled, sharesScaled) {
  return roundDiv(priceScaled * sharesScaled, 1000000);
}

export function marketValueCents(sharesScaled, navScaled) {
  return roundDiv(sharesScaled * navScaled, 1000000);
}

export function todayProfitCents(sharesScaled, navScaled, prevNavScaled) {
  if (prevNavScaled == null) return null;
  return roundDiv(sharesScaled * (navScaled - prevNavScaled), 1000000);
}

export function formatMoney(cents, withSign = false) {
  if (cents == null || Number.isNaN(cents)) return '—';
  const n = Math.round(cents);
  const sign = n < 0 ? '-' : (withSign && n > 0 ? '+' : '');
  const abs = Math.abs(n);
  const yuan = Math.floor(abs / 100);
  const fen = String(abs % 100).padStart(2, '0');
  return sign + '¥' + yuan.toLocaleString('zh-CN') + '.' + fen;
}

/* 百位整数（百分比×100 / 份额×100）→ 固定 2 位；零值不带符号，避免 -0.00 */
function formatHundredths(h) {
  const abs = Math.abs(h);
  const sign = h < 0 ? '-' : '';
  const int = Math.floor(abs / 100);
  const frac = String(abs % 100).padStart(2, '0');
  return sign + int + '.' + frac;
}

/* 单位净值：固定 4 位，直接从缩放整数格式化，不经过浮点 */
export function formatNav(scaled) {
  if (scaled == null || Number.isNaN(scaled)) return '—';
  const n = Math.round(scaled);
  const sign = n < 0 ? '-' : '';
  const abs = Math.abs(n);
  return sign + Math.floor(abs / 10000) + '.' + String(abs % 10000).padStart(4, '0');
}

/* 份额：固定 2 位 + 千分位（内部仍按 ×10000 原始精度计算） */
export function formatShares(scaled) {
  if (scaled == null || Number.isNaN(scaled)) return '—';
  const h = roundDiv(scaled, 100);
  const abs = Math.abs(h);
  const sign = h < 0 ? '-' : '';
  return sign + Math.floor(abs / 100).toLocaleString('zh-CN') + '.' +
    String(abs % 100).padStart(2, '0');
}

/* 单位成本价：固定 4 位，由总本金（分）÷ 份额（×10000）一次算到净值缩放精度 */
export function formatCostPrice(costCentsValue, sharesScaled) {
  if (costCentsValue == null || !sharesScaled) return '—';
  return formatNav(roundDiv(costCentsValue * 1000000, sharesScaled));
}

/* 涨跌幅：固定 2 位；四舍五入到 0 时显示 0.00%，不显示 -0.00% */
export function formatPctScaled(scaled, withSign = true) {
  if (scaled == null || Number.isNaN(scaled)) return '—';
  const h = roundDiv(scaled, 100);
  if (h === 0) return '0.00%';
  return (h > 0 && withSign ? '+' : '') + formatHundredths(h) + '%';
}

/* 收益率：固定 2 位，用整数分一次算到百分比×100，避免重复舍入 */
export function formatRatioPct(profitCentsValue, costCentsValue) {
  if (profitCentsValue == null || !costCentsValue) return '—';
  const h = roundDiv(profitCentsValue * 10000, costCentsValue);
  if (h === 0) return '0.00%';
  return (h > 0 ? '+' : '') + formatHundredths(h) + '%';
}

export function formatDate(value) {
  return value || '—';
}
