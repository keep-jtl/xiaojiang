// 英文市场事件自动翻译：MyMemory 公开翻译接口。
// 只翻译英文标题/摘要；中文内容不重复请求；译文写入本机 SQLite 缓存，离线时回退原文。
import { CapacitorHttp } from '@capacitor/core';
import { getTranslationCache, saveTranslationCache } from './db.js';

const API = 'https://api.mymemory.translated.net/get';
const TARGET = 'zh-CN';
const MAX_CHARS = 480;       // MyMemory 单次 q 上限 500；留出安全余量
const MAX_ITEMS = 24;        // 每次抓取最多翻译 24 条，避免首屏等待过久
const CONCURRENCY = 4;

const CJK = /[\u3400-\u9fff]/;
const MEMORY = new Map();

function looksEnglish(text) {
  const s = String(text || '').trim();
  return Boolean(s) && /[A-Za-z]/.test(s) && !CJK.test(s);
}

function clip(text, max = MAX_CHARS) {
  const s = String(text || '').trim();
  if (s.length <= max) return s;
  let cut = s.slice(0, max);
  const lastSpace = cut.lastIndexOf(' ');
  if (lastSpace > max * 0.6) cut = cut.slice(0, lastSpace);
  return cut.replace(/[.,;:，。；：]+$/, '').trim();
}

function cleanTranslated(text) {
  return String(text || '')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/\s+/g, ' ')
    .trim();
}

async function requestTranslation(text) {
  const q = clip(text);
  if (!q || !looksEnglish(q)) return '';
  const key = TARGET + '|' + q;
  if (MEMORY.has(key)) return MEMORY.get(key);
  try {
    const cached = await getTranslationCache(q);
    if (cached) {
      MEMORY.set(key, cached);
      return cached;
    }
    const res = await CapacitorHttp.get({
      url: API + '?q=' + encodeURIComponent(q) + '&langpair=' + encodeURIComponent('en|' + TARGET),
      headers: { 'User-Agent': 'XiaoLiCaiTong/0.1 (Android personal app)' },
      responseType: 'json',
      connectTimeout: 6000,
      readTimeout: 9000
    });
    if (res.status < 200 || res.status >= 300) return '';
    let data = res.data;
    if (typeof data === 'string') {
      try { data = JSON.parse(data); } catch (_) { return ''; }
    }
    const raw = data && data.responseData && data.responseData.translatedText;
    const out = cleanTranslated(raw);
    // 接口限流时会给 “MYMEMORY WARNING” 之类提示，不能当译文。
    if (!out || /MYMEMORY WARNING|QUERY LENGTH LIMIT/i.test(out)) return '';
    if (out.toLowerCase() === q.toLowerCase()) return '';
    MEMORY.set(key, out);
    await saveTranslationCache(q, out, TARGET);
    return out;
  } catch (err) {
    console.warn('market news translate failed', err);
    return '';
  }
}

async function translateOne(item) {
  const title = looksEnglish(item.title) ? await requestTranslation(item.title) : '';
  const summary = looksEnglish(item.summary) ? await requestTranslation(item.summary) : '';
  const status = title ? 'ok' : (summary ? 'partial' : '');
  return { ...item, title_zh: title, summary_zh: summary, translation_status: status };
}

// 小并发池，避免 24 条 × 2 个字段同时打满接口。
async function mapLimited(items, fn, limit) {
  const results = new Array(items.length);
  let next = 0;
  async function worker() {
    while (true) {
      const i = next++;
      if (i >= items.length) return;
      results[i] = await fn(items[i], i);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return results;
}

export async function translateNewsItems(items) {
  const list = items || [];
  const work = list.slice(0, MAX_ITEMS);
  const rest = list.slice(MAX_ITEMS);
  const translated = await mapLimited(work, translateOne, CONCURRENCY);
  return [...translated, ...rest.map((x) => ({ ...x, title_zh: x.title_zh || '', summary_zh: x.summary_zh || '' }))];
}
