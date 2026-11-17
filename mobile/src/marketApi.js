// Android 真实指数行情：腾讯 qt.gtimg.cn 批量公开源（与桌面版同一数据源）。
// 只解析 ASCII 数字字段；中文名使用本地映射，避免移动端 GBK 解码差异影响。
import { CapacitorHttp } from '@capacitor/core';

const CODES = [
  { tx: 'sh000300', name: '沪深300' },
  { tx: 'sh000001', name: '上证指数' },
  { tx: 'sh000905', name: '中证500' },
  { tx: 'sz399006', name: '创业板指' },
  { tx: 'hkHSI', name: '恒生指数' },
  { tx: 'usINX', name: '标普500' },
  { tx: 'usNDX', name: '纳斯达克100' },
  { tx: 'usDJI', name: '道琼斯' }
];

const HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36',
  'Referer': 'https://finance.qq.com/'
};

export async function fetchIndexQuotes() {
  const q = CODES.map((c) => c.tx).join(',');
  const res = await CapacitorHttp.get({
    url: 'https://qt.gtimg.cn/q=' + q,
    headers: HEADERS,
    responseType: 'text'
  });
  if (res.status < 200 || res.status >= 300) {
    throw new Error('行情源返回 HTTP ' + res.status);
  }
  const text = String(res.data || '');
  const byCode = {};
  for (const line of text.split(/\r?\n/)) {
    const m = line.match(/^v_([^=]+)="(.*)";?$/);
    if (!m) continue;
    const parts = m[2].split('~');
    if (parts.length < 33) continue;
    const price = Number(parts[3]);
    const prev = Number(parts[4]);
    let pct = Number(parts[32]);
    if (!Number.isFinite(pct) && Number.isFinite(price) && Number.isFinite(prev) && prev > 0) {
      pct = (price - prev) / prev * 100;
    }
    if (!Number.isFinite(price)) continue;
    byCode[m[1]] = {
      price: price,
      pct: Number.isFinite(pct) ? pct : null,
      time: parts[30] || ''
    };
  }
  const items = CODES.map((c) => {
    const v = byCode[c.tx];
    if (!v) return { code: c.tx, name: c.name, price: null, pct: null, time: '' };
    return { code: c.tx, name: c.name, price: v.price, pct: v.pct, time: v.time };
  });
  if (!items.some((x) => x.price != null)) {
    throw new Error('行情源暂不可用');
  }
  return { items: items, at: new Date().toISOString() };
}

export function formatIndexPrice(v) {
  if (v == null || Number.isNaN(v)) return '—';
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function formatIndexPct(v) {
  if (v == null || Number.isNaN(v)) return '—';
  const h = Math.round(v * 100);            /* 百分比×100，先取整再判断，避免 -0.00% */
  if (h === 0) return '0.00%';
  return (h > 0 ? '+' : '-') + (Math.abs(h) / 100).toFixed(2) + '%';
}

export function marketOptions() {
  return CODES.map((c) => ({ code: c.tx, name: c.name }));
}
