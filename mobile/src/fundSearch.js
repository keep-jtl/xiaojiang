// 基金搜索（纯函数，不依赖 Capacitor/SQLite）——与 Windows app/sources/fund_list.py 同一套规则。
// 2026-11-09：兼容“连接/联接”、全半角、空格/标点、关键词顺序，分段覆盖官方全称。

const SEPS = /[\s\-_·、,，。.:：;；/\\|｜+＋()（）\[\]【】<>《》"'“”‘’%]+/g;
const SYNONYMS = [
  ['连接', '联接'],
  ['連接', '联接'],
  ['聯接', '联接'],
  ['聯', '联']
];
const GENERIC_WORDS = ['证券投资基金', '基金'];
const MIN_SEG = 2;
const SHARE_CLASSES = ['a', 'c', 'e'];

const SC_CODE_EXACT = 1000;
const SC_TOKEN_CODE_EXACT = 950;
const SC_NAME_EXACT = 900;
const SC_TOKEN_NAME_EXACT = 850;
const SC_CODE_PREFIX = 800;
const SC_NAME_PREFIX = 700;
const SC_SUBSTRING = 600;
const SC_SEGMENTS = 400;
const SC_TOKEN_SEGMENTS = 390;

const NORMALIZED = new WeakMap();   // list 数组 → 已归一化名称缓存，避免每次搜索重算 2.8 万条

export function normalizeText(value) {
  if (value == null) return '';
  let t = String(value).normalize('NFKC').toLowerCase();
  for (const [oldText, newText] of SYNONYMS) t = t.split(oldText).join(newText);
  return t;
}

export function normalizeTokens(keyword) {
  const k = normalizeText(keyword);
  const raw = k.split(SEPS).filter(Boolean);
  const tokens = [];
  for (let t of raw) {
    for (const g of GENERIC_WORDS) t = t.split(g).join('');
    if (t) tokens.push(t);
  }
  return tokens.length ? tokens : raw;
}

export function compact(value) {
  return normalizeText(value).replace(SEPS, '');
}

function commonPrefixLen(a, b) {
  let n = 0;
  while (n < a.length && n < b.length && a[n] === b[n]) n += 1;
  return n;
}

function canCut(seg, i) {
  if (i <= 0) return true;
  return !(/[0-9]/.test(seg[i - 1]) && /[0-9]/.test(seg[i]));
}

// seg 最少能由 text 的几段拼出（每段连续、顺序保持、每段≥2字符；A/C/E 允许单字）
function minPieces(seg, text) {
  if (!seg || !text) return 0;
  if (text.includes(seg)) return 1;
  if (seg.length === 1 && SHARE_CLASSES.includes(seg) && text.includes(seg)) return 1;
  const n = seg.length;
  if (n < MIN_SEG * 2) return 0;
  if (!text.includes(seg[0])) return 0;
  const inf = n + 1;
  const best = new Array(n + 1).fill(inf);
  best[0] = 0;
  for (let j = MIN_SEG; j <= n; j += 1) {
    for (let i = 0; i <= j - MIN_SEG; i += 1) {
      if (best[i] < inf && canCut(seg, i) && text.includes(seg.slice(i, j))) {
        if (best[i] + 1 < best[j]) best[j] = best[i] + 1;
      }
    }
  }
  return best[n] >= inf ? 0 : best[n];
}

function scoreFund(code, nameCompact, tokens, whole) {
  const prefix = commonPrefixLen(whole, nameCompact);
  if (code === whole) return { score: SC_CODE_EXACT, pieces: 0, prefix };
  for (const t of tokens) {
    if (t === code) return { score: SC_TOKEN_CODE_EXACT, pieces: 0, prefix };
  }
  if (nameCompact === whole) return { score: SC_NAME_EXACT, pieces: 0, prefix };
  for (const t of tokens) {
    if (t === nameCompact) return { score: SC_TOKEN_NAME_EXACT, pieces: 0, prefix };
  }
  if (/^[0-9]+$/.test(whole) && code.startsWith(whole)) {
    return { score: SC_CODE_PREFIX, pieces: 0, prefix };
  }
  if (nameCompact.startsWith(whole)) return { score: SC_NAME_PREFIX, pieces: 0, prefix };
  if (nameCompact.includes(whole) || code.includes(whole)) {
    return { score: SC_SUBSTRING, pieces: 0, prefix };
  }
  if (!nameCompact.includes(whole[0]) && !code.includes(whole[0])) {
    return { score: 0, pieces: 0, prefix: 0 };
  }
  const pieces = minPieces(whole, nameCompact) || minPieces(whole, code);
  if (pieces) return { score: SC_SEGMENTS, pieces, prefix };
  let total = 0;
  for (const t of tokens) {
    const p = minPieces(t, nameCompact) || minPieces(t, code);
    if (!p) return { score: 0, pieces: 0, prefix: 0 };
    total += p;
  }
  return { score: SC_TOKEN_SEGMENTS, pieces: total, prefix };
}

function normalizedFor(list) {
  let cache = NORMALIZED.get(list);
  if (!cache) {
    cache = list.map((f) => {
      const norm = normalizeText(f.name);
      return { norm, compactName: norm.replace(SEPS, '') };
    });
    NORMALIZED.set(list, cache);
  }
  return cache;
}

export function searchFunds(list, keyword, limit = 20) {
  const tokens = normalizeTokens(keyword);
  if (!tokens.length) return [];
  const whole = tokens.join('');
  let max = Number(limit) || 20;
  if (whole.length <= 3 && !/^[0-9]+$/.test(whole)) max = Math.max(max, 100);

  const names = normalizedFor(list);
  const hits = [];
  for (let i = 0; i < list.length; i += 1) {
    const f = list[i];
    const code = String(f.code).toLowerCase();
    const { compactName } = names[i];
    const r = scoreFund(code, compactName, tokens, whole);
    if (r.score) {
      hits.push({
        f, score: r.score, pieces: r.pieces, prefix: r.prefix,
        nameLen: String(f.name).length, code, index: i
      });
    }
  }
  hits.sort((a, b) => (
    b.score - a.score ||
    a.pieces - b.pieces ||
    b.prefix - a.prefix ||
    a.nameLen - b.nameLen ||
    (a.code < b.code ? -1 : a.code > b.code ? 1 : 0) ||
    a.index - b.index
  ));
  return hits.slice(0, max).map((h) => h.f);
}
