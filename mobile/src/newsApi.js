// 手机端真实市场事件：只使用可公开访问的官方 RSS（美联储、美国劳工统计局）。
// 不把示例新闻写进代码；网络失败时返回错误，由页面展示已有缓存或明确提示。
import { CapacitorHttp } from '@capacitor/core';
import { translateNewsItems } from './translation.js';

const SOURCES = [
  {
    id: 'fed_rss',
    name: '美联储',
    url: 'https://www.federalreserve.gov/feeds/press_all.xml',
    headers: { 'User-Agent': 'XiaoLiCaiTong/0.1 (Android personal app)' }
  },
  {
    id: 'bls_rss',
    name: '美国劳工统计局',
    url: 'https://www.bls.gov/feed/bls_latest.rss',
    headers: { 'User-Agent': 'XiaoLiCaiTong/0.1 (Android personal app)' }
  }
];

const MAX_ITEMS = 36;
const LOOKBACK_DAYS = 14;

function stripHtml(text) {
  return String(text || '')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function parseDate(text) {
  const d = new Date(String(text || '').trim());
  return Number.isNaN(d.getTime()) ? null : d;
}

function classify(title, source) {
  const t = String(title || '').toLowerCase();
  if (source === 'fed_rss') {
    if (t.includes('enforcement')) return null;
    if (/(monetary policy|fomc|federal funds|interest rate|rate cut|rate hike|inflation|discount rate)/.test(t)) {
      return { category: '货币政策', importance: 1 };
    }
    if (/(speech|remarks|testimony)/.test(t)) return { category: '官员讲话', importance: 0.7 };
    if (/(supervision|regulatory|regulation|capital)/.test(t)) return { category: '监管政策', importance: 0.6 };
    return { category: '美联储动态', importance: 0.45 };
  }
  if (/(consumer price|producer price|employment|job|payroll|unemployment|cpi|ppi)/.test(t)) {
    return { category: '美国经济数据', importance: 0.9 };
  }
  return { category: '美国经济数据', importance: 0.55 };
}

async function fetchXml(source) {
  const res = await CapacitorHttp.get({
    url: source.url,
    headers: source.headers,
    responseType: 'text'
  });
  if (res.status < 200 || res.status >= 300) {
    throw new Error(source.name + ' RSS 返回 HTTP ' + res.status);
  }
  return String(res.data || '');
}

function parseItems(xml, source) {
  const doc = new DOMParser().parseFromString(xml, 'text/xml');
  if (doc.getElementsByTagName('parsererror').length) {
    throw new Error(source.name + ' RSS 解析失败');
  }
  const nodes = Array.from(doc.getElementsByTagName('item'));
  const items = [];
  for (const node of nodes) {
    const title = stripHtml(node.getElementsByTagName('title')[0]?.textContent);
    const link = String(node.getElementsByTagName('link')[0]?.textContent || '').trim();
    const pub = String(node.getElementsByTagName('pubDate')[0]?.textContent || '').trim();
    const desc = stripHtml(node.getElementsByTagName('description')[0]?.textContent);
    if (!title || !link || !pub) continue;
    const date = parseDate(pub);
    if (!date) continue;
    const meta = classify(title, source.id);
    if (!meta) continue;
    items.push({
      id: source.id + ':' + (link || title),
      source: source.id,
      source_name: source.name,
      title,
      summary: desc,
      url: link,
      published_at: date.toISOString(),
      published_ts: Math.floor(date.getTime() / 1000),
      importance: meta.importance,
      category: meta.category
    });
  }
  return items;
}

// holdings 只用来标注“与我的持仓相关 / 市场发现”，不伪造公司级新闻。
export function linkToHoldings(items, holdings) {
  const list = (holdings || []).filter((h) => h.status === 'holding');
  const hasQdii = list.some((h) => /QDII/i.test(String(h.fund_type || '')));
  for (const item of items) {
    const macro = item.source === 'fed_rss' || item.source === 'bls_rss';
    item.relation = macro && list.length ? 'macro' : 'discover';
    item.related = macro && list.length > 0;
    item.relation_text = hasQdii
      ? '宏观环境 · 影响 QDII/全球风险偏好'
      : (list.length ? '宏观环境 · 影响你的组合所处市场' : '市场发现');
  }
  return items;
}

export async function fetchNews(holdings) {
  const cutoff = Date.now() - LOOKBACK_DAYS * 24 * 3600 * 1000;
  const errors = [];
  const all = [];
  const results = await Promise.allSettled(SOURCES.map(async (s) => {
    const xml = await fetchXml(s);
    return parseItems(xml, s);
  }));
  results.forEach((r, i) => {
    if (r.status === 'fulfilled') all.push(...r.value);
    else errors.push(SOURCES[i].name + '：' + (r.reason?.message || '获取失败'));
  });
  const rawItems = linkToHoldings(all, holdings)
    .filter((x) => x.published_ts * 1000 >= cutoff)
    .sort((a, b) => b.published_ts - a.published_ts)
    .slice(0, MAX_ITEMS);
  if (!rawItems.length) {
    throw new Error(errors.length ? errors.join('；') : '官方 RSS 暂无可展示的最新事件');
  }
  // 英文标题/摘要自动译为中文；翻译失败时保留原文，不阻断新闻列表。
  const items = await translateNewsItems(rawItems);
  return { items, errors, at: new Date().toISOString() };
}

export function formatNewsTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}
