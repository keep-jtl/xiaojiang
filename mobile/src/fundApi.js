// Android 直接访问公开基金接口；CapacitorHttp 使用原生网络，绕过 WebView CORS。
import { CapacitorHttp } from '@capacitor/core';
import { getMeta, setMeta } from './db.js';
import { parseScaled } from './money.js';

const FUND_LIST_URL = 'https://fund.eastmoney.com/js/fundcode_search.js';
const NAV_URL = 'https://api.fund.eastmoney.com/f10/lsjz';
const FUND_LIST_KEY = 'fund_list_json';
const FUND_LIST_AT = 'fund_list_at';
const LIST_TTL = 24 * 60 * 60 * 1000;

const HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36',
  'Accept': 'application/json,text/javascript,*/*'
};

async function httpGet(url, responseType = 'text', extraHeaders = {}) {
  const res = await CapacitorHttp.get({
    url,
    headers: { ...HEADERS, ...extraHeaders },
    responseType
  });
  if (res.status < 200 || res.status >= 300) {
    throw new Error('数据源返回 HTTP ' + res.status);
  }
  return res.data;
}

async function loadFundListFromNetwork() {
  const text = await httpGet(FUND_LIST_URL, 'text', { Referer: 'https://fund.eastmoney.com/' });
  const body = String(text).replace(/^﻿/, '').trim()
    .replace(/^var\s+r\s*=\s*/, '').replace(/;\s*$/, '');
  const rows = JSON.parse(body);
  return rows.map((r) => ({
    code: String(r[0]),
    name: String(r[2]),
    fund_type: String(r[3] || '')
  }));
}

export async function getFundList() {
  const cached = await getMeta(FUND_LIST_KEY);
  const at = Number(await getMeta(FUND_LIST_AT) || 0);
  if (cached && Date.now() - at < LIST_TTL) {
    try { return JSON.parse(cached); } catch (_) { /* 重新下载 */ }
  }
  try {
    const list = await loadFundListFromNetwork();
    await setMeta(FUND_LIST_KEY, JSON.stringify(list));
    await setMeta(FUND_LIST_AT, String(Date.now()));
    return list;
  } catch (err) {
    if (cached) {
      try { return JSON.parse(cached); } catch (_) { /* ignore */ }
    }
    throw err;
  }
}

export { searchFunds } from './fundSearch.js';

export async function fetchLatestNav(code) {
  const url = NAV_URL + '?fundCode=' + encodeURIComponent(code) +
    '&pageIndex=1&pageSize=2';
  let data = await httpGet(url, 'json', { Referer: 'https://fundf10.eastmoney.com/' });
  if (typeof data === 'string') {
    try { data = JSON.parse(data); } catch (_) { throw new Error('净值数据格式异常'); }
  }
  const list = data && data.Data && data.Data.LSJZList;
  if (!Array.isArray(list) || !list.length) throw new Error('数据源暂未返回该基金净值');
  const latest = list[0];
  const prev = list[1] || null;
  const navScaled = parseScaled(latest.DWJZ, 4);
  if (!Number.isFinite(navScaled)) throw new Error('净值数值异常');
  const prevNavScaled = prev ? parseScaled(prev.DWJZ, 4) : null;
  const dailyPctScaled = latest.JZZZL == null || latest.JZZZL === ''
    ? null : parseScaled(latest.JZZZL, 4);
  return {
    nav_date: String(latest.FSRQ || '').slice(0, 10),
    nav_scaled: navScaled,
    prev_nav_scaled: Number.isFinite(prevNavScaled) ? prevNavScaled : null,
    daily_pct_scaled: Number.isFinite(dailyPctScaled) ? dailyPctScaled : null
  };
}
