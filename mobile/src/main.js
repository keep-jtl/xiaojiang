import './style.css';
import {
  initDb, getHoldings, addHolding, updateNav, clearHolding, deleteHolding,
  getMeta, setMeta, nowStr, todayStr, setNewsState, saveNewsCache, getNewsCache
} from './db.js';
import { getFundList, searchFunds, fetchLatestNav } from './fundApi.js';
import { buildReport } from './portfolioAnalysis.js';
import { fetchIndexQuotes, formatIndexPrice, formatIndexPct, marketOptions } from './marketApi.js';
import { fetchNews, formatNewsTime } from './newsApi.js';
import {
  getAlertRules, addAlertRule, setAlertRuleEnabled, deleteAlertRule, getAlertEvents,
  ruleText, ruleObjectText, evaluateRules, persistTrigger
} from './alerts.js';
import {
  notificationStatus, requestNotificationPermission, showLocalNotification
} from './notifications.js';
import {
  parseScaled, roundDiv, costCents, marketValueCents, todayProfitCents,
  formatMoney, formatNav, formatShares, formatCostPrice, formatPctScaled, formatRatioPct
} from './money.js';

const $ = (id) => document.getElementById(id);
const S = {
  holdings: [],
  closed: [],
  lastRefresh: null,
  fundList: null,
  selected: null,
  selectedNav: null,
  searchSeq: 0,
  busy: false,
  banner: '',
  market: null,
  marketAt: 0,
  view: 'home',
  analysisFrom: 'home',
  news: [],
  newsAt: null,
  newsError: '',
  newsFilter: 'all',
  newsLoading: false,
  newsFromCache: false,
  rules: [],
  events: [],
  alertChecking: false,
  notify: { supported: false, granted: false, text: '读取中…' }
};
let toastTimer = null;

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function toast(text) {
  const el = $('toast');
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

function setBanner(text) {
  S.banner = text || '';
  const el = $('banner');
  el.textContent = S.banner;
  el.hidden = !S.banner;
}

function trendClass(value) {
  if (value == null || value === 0) return 'flat';
  return value > 0 ? 'up' : 'down';
}

function summary() {
  // 口径对齐 GPT《收口及双端交付决定》：完整组合口径只在全部持仓有净值时成立。
  let totalCost = 0;
  const valued = [];
  const missing = [];
  for (const h of S.holdings) {
    totalCost += h.cost_cents || 0;
    if (h.nav_scaled != null) {
      const mv = marketValueCents(h.shares_scaled, h.nav_scaled);
      const profit = mv - (h.cost_cents || 0);
      const today = h.prev_nav_scaled != null
        ? todayProfitCents(h.shares_scaled, h.nav_scaled, h.prev_nav_scaled) : null;
      valued.push({ mv, profit, today });
    } else {
      missing.push(h);
    }
  }
  const complete = S.holdings.length > 0 && missing.length === 0;
  const knownValue = valued.length ? valued.reduce((a, x) => a + x.mv, 0) : null;
  const knownProfit = valued.length ? valued.reduce((a, x) => a + x.profit, 0) : null;
  const todayParts = valued.filter((x) => x.today != null);
  const knownToday = todayParts.length
    ? todayParts.reduce((a, x) => a + x.today, 0) : null;
  return {
    count: S.holdings.length,
    cost: totalCost,
    complete,
    valuedCount: valued.length,
    missingCount: missing.length,
    // 完整组合口径（仅 complete 时给值）
    total: complete ? knownValue : null,
    profit: complete ? knownProfit : null,
    today: complete ? knownToday : null,
    todayPct: complete && knownToday != null && (knownValue - knownToday) > 0
      ? formatPctScaled(roundDiv(knownToday * 1000000, knownValue - knownToday), true)
      : null,
    // 已估值部分口径（部分缺失时克制展示）
    knownValue,
    knownProfit,
    knownToday
  };
}

// 首页 + 我的基金页共用的三态渲染（label/金额/收益/底部提示）。
// opts 可覆盖各元素完整 id（我的基金页 id 命名与首页不同）。
function renderSummaryCard(prefix, sum, opts) {
  opts = opts || {};
  const labelEl = $(opts.labelId || (prefix + 'Label'));
  const tagEl = $(opts.tagId || (prefix + 'Tag'));
  const totalEl = $(opts.totalId || (prefix + 'Total'));
  const profitEl = $(opts.profitId || (prefix + 'Profit'));
  const todayEl = $(opts.todayId || (prefix + 'Today'));
  const countEl = $(opts.countId || (prefix + 'Count'));
  const footEl = $(opts.footId || (prefix + 'Foot'));

  // 标题口径：基金页是“完整组合”，首页走独立的今日快照，两者不再共用同一文案。
  if (sum.count && !sum.complete && sum.knownValue != null) {
    labelEl.textContent = (prefix === 'funds' ? '资产总览 · 已估值（' : '已估值持仓市值（') +
      sum.valuedCount + '/' + sum.count + ' 只）';
    tagEl.hidden = false;
  } else {
    labelEl.textContent = prefix === 'funds' ? '资产总览 · 完整组合' : '我的总资产';
    tagEl.hidden = true;
  }

  // 主金额：完整 → 完整总市值；部分缺失 → 已估值部分；全无 → —
  const main = sum.complete ? sum.total : sum.knownValue;
  totalEl.textContent = main == null ? '—' : formatMoney(main);

  // 今日盈亏：完整 → 完整合计；部分缺失 → 已估值部分（标注）；全无 → —
  if (sum.complete && sum.today != null) {
    todayEl.textContent = formatMoney(sum.today, true);
  } else if (!sum.complete && sum.knownToday != null) {
    todayEl.textContent = formatMoney(sum.knownToday, true) + '（已估值部分）';
  } else {
    todayEl.textContent = '—';
  }

  // 累计收益：完整口径；部分缺失时一律 —
  profitEl.textContent = sum.complete && sum.profit != null
    ? formatMoney(sum.profit, true) : '—';
  countEl.textContent = String(sum.count);

  // 底部提示
  if (!sum.count) {
    footEl.textContent = '还没有持仓';
  } else if (!sum.complete && sum.knownValue != null) {
    footEl.textContent = sum.missingCount +
      ' 只缺少有效净值，数字不是完整组合总市值。';
  } else {
    footEl.textContent = S.lastRefresh
      ? '最近刷新 ' + S.lastRefresh + (sum.todayPct ? ' · 今日 ' + sum.todayPct : '')
      : '尚未刷新净值';
  }
}

function renderHomeSummary(sum) {
  // 首页只做“今日快照”：不做完整资产卡，避免和「基金」页重复。
  const labelEl = $('homeLabel');
  const tagEl = $('homeTag');
  const totalEl = $('homeTotal');
  const todayEl = $('homeToday');
  const countEl = $('homeCount');
  const footEl = $('homeFoot');
  if (!totalEl) return;

  if (sum.count && !sum.complete && sum.knownValue != null) {
    labelEl.textContent = '已估值持仓（' + sum.valuedCount + '/' + sum.count + ' 只）';
    tagEl.hidden = false;
  } else {
    labelEl.textContent = '今日概览';
    tagEl.hidden = true;
  }
  const main = sum.complete ? sum.total : sum.knownValue;
  totalEl.textContent = main == null ? '—' : formatMoney(main);

  if (sum.complete && sum.today != null) {
    todayEl.textContent = formatMoney(sum.today, true);
  } else if (!sum.complete && sum.knownToday != null) {
    todayEl.textContent = formatMoney(sum.knownToday, true) + '（部分）';
  } else {
    todayEl.textContent = '—';
  }
  countEl.textContent = String(sum.count);
  if (!sum.count) {
    footEl.textContent = '还没有持仓；先在「基金」页添加。';
  } else if (!sum.complete && sum.knownValue != null) {
    footEl.textContent = sum.missingCount + ' 只缺少有效净值，这里只显示已估值部分。';
  } else {
    footEl.textContent = '最近刷新 ' + (S.lastRefresh || '—') +
      ' · 完整成本与收益在「基金」页';
  }
}

function renderHomeNews() {
  const box = $('homeNews');
  if (!box) return;
  const hint = $('homeNewsAt');
  if (hint) hint.textContent = S.newsAt ? fullNewsTime(S.newsAt) : '—';
  const items = (S.news || []).filter((x) => !Number(x.is_ignored)).slice(0, 3);
  if (!items.length) {
    box.innerHTML = '<div class="home-feed-empty">首页只放最新 3 条；<br>' +
      '打开「事件」页查看全部官方市场事件。' +
      '<div><button class="btn btn-small" id="homeNewsOpen">打开市场事件 ›</button></div></div>';
    return;
  }
  box.innerHTML = items.map((item) => {
    const title = item.title_zh || item.title;
    const meta = [item.source_name || '官方源', shortNewsTime(item.published_at)].filter(Boolean).join(' · ');
    return '<button class="home-feed-row" data-url="' + esc(item.url || '') + '">' +
      '<b>' + esc(title) + '</b><small>' + esc(meta) +
      (item.title_zh ? ' · 已译' : '') + '</small></button>';
  }).join('');
}

function renderHomeAlerts() {
  const box = $('homeAlerts');
  if (!box) return;
  const enabled = (S.rules || []).filter((r) => Number(r.enabled));
  const recent = (S.events || [])[0];
  if (!enabled.length && !recent) {
    box.innerHTML = '<div class="home-feed-empty">还没有启用的盯盘规则。' +
      '<div><button class="btn btn-small" id="homeAlertOpen">去设置提醒 ›</button></div></div>';
    return;
  }
  const lines = [];
  if (enabled.length) {
    lines.push('<div class="home-alert-line"><b>' + enabled.length + ' 条规则运行中</b>' +
      '<small>前台每分钟检查</small></div>');
  }
  if (recent) {
    lines.push('<div class="home-alert-line"><b>' + esc(recent.title || '最近触发') + '</b>' +
      '<small>' + esc(shortNewsTime(recent.triggered_at)) + '</small></div>');
  } else {
    lines.push('<div class="home-alert-line"><b>暂无触发</b><small>命中后会记录在这里</small></div>');
  }
  box.innerHTML = lines.join('');
}

function fmtQuoteTime(v) {
  const s = String(v || '').trim();
  if (/^\d{14}$/.test(s)) {
    return s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8) + ' ' + s.slice(8, 10) + ':' + s.slice(10, 12);
  }
  if (/^\d{4}\/\d{2}\/\d{2}/.test(s)) return s.replace(/\//g, '-').slice(0, 16);
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 16);
  return s;
}

function renderMarket() {
  const box = $('homeMarket');
  if (!box) return;
  if (!S.market || !S.market.items) {
    box.innerHTML = '<div class="empty"><b>行情暂不可用</b>数据源暂时没有返回真实行情，页面不会用示例数字代替。' +
      '<div style="margin-top:8px;"><button class="btn btn-small" id="marketRetry">重试行情</button></div></div>';
    $('homeMarketHint').textContent = '暂不可用';
    return;
  }
  const rows = S.market.items.map((q) => {
    const pct = q.pct == null ? '—' : formatIndexPct(q.pct);
    const cls = q.pct == null ? 'flat' : (q.pct >= 0 ? 'up' : 'down');
    return '<div class="mkt-row">' +
      '<div class="n">' + esc(q.name) + '</div>' +
      '<div class="v"><b class="num">' + formatIndexPrice(q.price) + '</b>' +
      '<small class="num ' + cls + '">' + pct + '</small></div></div>';
  }).join('');
  box.innerHTML = rows;
  const t = S.market.items.map((x) => fmtQuoteTime(x.time)).filter(Boolean).sort().pop();
  $('homeMarketHint').textContent = t ? '行情时间 ' + t : '真实行情';
}

async function loadMarket(force = false) {
  if (!force && S.market && Date.now() - S.marketAt < 60 * 1000) {
    renderMarket();
    return S.market;
  }
  $('homeMarketHint').textContent = '读取中…';
  $('homeMarket').innerHTML = '<div class="loading">正在获取真实行情…</div>';
  try {
    S.market = await fetchIndexQuotes();
    S.marketAt = Date.now();
    renderMarket();
    return S.market;
  } catch (err) {
    console.warn('market quotes failed', err);
    S.market = null;
    S.marketAt = 0;
    renderMarket();
    return null;
  }
}

/* ===== 市场事件（手机端 v2） ===== */
function shortNewsTime(value) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function fullNewsTime(value) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' +
    p(d.getMinutes()) + ':' + p(d.getSeconds());
}

function newsCardHtml(item) {
  const read = Number(item.is_read) ? ' read' : '';
  const fav = Number(item.is_favorite);
  const titleZh = String(item.title_zh || '').trim();
  const summaryZh = String(item.summary_zh || '').trim();
  const title = titleZh || item.title;
  const summary = String(summaryZh || item.summary || '').slice(0, 150);
  const translated = Boolean(titleZh);
  const original = translated && item.title && item.title !== title
    ? '<div class="news-orig">原文：' + esc(String(item.title).slice(0, 180)) + '</div>'
    : '';
  return '<article class="news-card' + read + '" data-id="' + esc(item.id) + '">' +
    '<div class="news-meta"><span class="source">' + esc(item.source_name || '官方源') + '</span>' +
      '<span>' + esc(item.category || '市场事件') + '</span>' +
      '<span>' + esc(shortNewsTime(item.published_at)) + '</span>' +
      (translated ? '<span>自动翻译</span>' : '') + '</div>' +
    '<div class="news-title">' + esc(title) + '</div>' + original +
    (summary ? '<p class="news-text">' + esc(summary) + '</p>' : '') +
    '<div class="news-tags"><span class="news-tag ' + (Number(item.related) ? 'macro' : '') + '">' +
      esc(item.relation_text || (Number(item.related) ? '与持仓相关' : '市场发现')) + '</span>' +
      '<span class="news-tag">' + (Number(item.importance) >= 0.85 ? '重要' : '一般') + '</span></div>' +
    '<div class="news-actions">' +
      '<button class="btn btn-small" data-act="open">阅读原文 ›</button>' +
      '<button class="btn btn-small btn-ghost" data-act="read">' + (Number(item.is_read) ? '标为未读' : '标为已读') + '</button>' +
      '<button class="btn btn-small btn-ghost" data-act="fav">' + (fav ? '★ 已收藏' : '☆ 收藏') + '</button>' +
    '</div></article>';
}

function renderNews() {
  const list = $('newsList');
  if (!list) return;
  const filter = S.newsFilter;
  const all = S.news || [];
  const shown = all.filter((item) => {
    if (filter === 'related') return Number(item.related);
    if (filter === 'discover') return !Number(item.related);
    if (filter === 'fav') return Number(item.is_favorite);
    return true;
  });
  $('newsCount').textContent = String(all.length);
  $('newsRelated').textContent = String(all.filter((x) => Number(x.related)).length);
  $('newsAt').textContent = S.newsAt ? fullNewsTime(S.newsAt) : '—';
  $('newsStatus').textContent = S.newsError
    ? (S.newsFromCache ? '官方 RSS · 显示缓存' : '官方 RSS · 抓取失败')
    : (S.newsFromCache ? '官方 RSS · 缓存' : '官方 RSS · 已更新');
  const fetchCard = $('newsFetchCard');
  if (S.newsError) {
    fetchCard.hidden = false;
    $('newsFetchText').textContent = S.newsError + (S.newsFromCache
      ? '；下面保留最近一次真实抓取的内容，不展示示例新闻。'
      : '；当前没有可用缓存。');
  } else {
    fetchCard.hidden = true;
  }
  if (!shown.length) {
    list.innerHTML = '<div class="news-empty"><b>当前没有可展示的事件</b>' +
      (S.newsLoading ? '正在读取官方 RSS…' : (all.length
        ? '这个筛选下还没有内容，换个筛选试试。'
        : '美联储 / BLS 官方 RSS 最近 14 天没有取到内容；可点“重新抓取”，不会用假新闻填充。')) + '</div>';
    renderHomeNews();
    return;
  }
  list.innerHTML = shown.map(newsCardHtml).join('');
  renderHomeNews();
}

async function loadNews(force = false, at = null) {
  if (S.newsLoading) return;
  const cycleAt = at || nowStr();
  S.newsLoading = true;
  if (!S.news.length) {
    try {
      const cache = await getNewsCache(40);
      if (cache.length) {
        S.news = cache.map((x) => ({
          ...x,
          related: x.relation === 'macro' ? 1 : 0,
          source_name: x.source_name || '官方源'
        }));
        S.newsFromCache = true;
        S.newsAt = at || cache[0].fetched_at || cache[0].published_at;
        renderNews();
      }
    } catch (err) {
      console.warn('news cache read failed', err);
    }
  }
  renderNews();
  try {
    const result = await fetchNews(S.holdings);
    S.news = result.items;
    S.newsFromCache = false;
    S.newsAt = at || result.at || cycleAt;
    S.newsError = result.errors && result.errors.length
      ? '部分来源不可用：' + result.errors.join('；') : '';
    await saveNewsCache(result.items);
    await setMeta('news_fetched_at', S.newsAt);
    renderNews();
  } catch (err) {
    S.newsError = '官方 RSS 抓取失败：' + (err?.message || '网络不可用');
    S.newsFromCache = S.news.length > 0;
    if (!S.newsAt) S.newsAt = at || cycleAt;
    renderNews();
  } finally {
    S.newsLoading = false;
  }
}

async function toggleNewsState(id, patch) {
  try {
    await setNewsState(id, patch);
    const item = S.news.find((x) => x.id === id);
    if (item) {
      if (Object.prototype.hasOwnProperty.call(patch, 'is_read')) item.is_read = patch.is_read ? 1 : 0;
      if (Object.prototype.hasOwnProperty.call(patch, 'is_favorite')) item.is_favorite = patch.is_favorite ? 1 : 0;
    }
    renderNews();
  } catch (err) {
    toast('本地保存失败：' + (err?.message || '数据库不可用'));
  }
}

/* ===== 盯盘提醒（手机端 v2） ===== */
function populateRuleObjects() {
  const sel = $('ruleObject');
  if (!sel) return;
  const current = sel.value;
  const indexes = marketOptions();
  const holdings = S.holdings.filter((h) => h.status === 'holding');
  let html = '<optgroup label="指数">' +
    indexes.map((x) => '<option value="index|' + esc(x.code) + '|' + esc(x.name) + '">' +
      esc(x.name) + ' · 指数</option>').join('') + '</optgroup>';
  if (holdings.length) {
    html += '<optgroup label="我的基金">' +
      holdings.map((h) => '<option value="fund|' + esc(h.fund_code) + '|' + esc(h.name) + '">' +
        esc(h.name) + ' · ' + esc(h.fund_code) + '</option>').join('') + '</optgroup>';
  }
  sel.innerHTML = html;
  if (current) sel.value = current;
}

function renderAlertRules() {
  const box = $('alertRules');
  if (!box) return;
  const rules = S.rules || [];
  $('ruleCount').textContent = rules.length ? '共 ' + rules.length + ' 条' : '';
  $('alertRuleCount').textContent = String(rules.filter((r) => Number(r.enabled)).length);
  $('alertTriggerCount').textContent = String((S.events || []).length);
  if (!rules.length) {
    box.innerHTML = '<div class="empty"><b>还没有盯盘规则</b>点上方「＋ 新建提醒」，选择指数或我的基金，设置涨跌幅/价格条件。</div>';
    return;
  }
  box.innerHTML = rules.map((rule) => {
    const enabled = Number(rule.enabled);
    return '<div class="alert-rule ' + (enabled ? '' : 'disabled') + '" data-id="' + rule.id + '">' +
      '<div class="top"><div class="obj">' + esc(ruleObjectText(rule)) +
        '<small>' + esc(rule.note || '本地规则') + '</small></div>' +
        '<button class="switch ' + (enabled ? 'on' : '') + '" data-act="toggle">' +
          (enabled ? '已启用' : '已暂停') + '</button></div>' +
      '<div class="cond">' + esc(ruleText(rule)) + '</div>' +
      '<div class="meta">' + (Number(rule.last_state) ? '上次已触发：' : '等待触发 · ') +
        esc(rule.last_triggered_at || '尚未触发') + ' · ' +
        esc(rule.valid_type === 'once' ? '触发一次后关闭' : '条件消失后可再触发') + '</div>' +
      '<div class="ops"><button class="btn btn-small btn-danger" data-act="delete">删除规则</button></div>' +
    '</div>';
  }).join('');
}

function renderAlertHistory() {
  const box = $('alertHistory');
  if (!box) return;
  if (!S.events || !S.events.length) {
    box.innerHTML = '<div class="empty">还没有触发记录。规则命中后会写入本机历史，并尝试发送 Android 系统通知。</div>';
    return;
  }
  box.innerHTML = S.events.map((e) => '<div class="history-item">' +
    '<b>' + esc(e.title) + '</b>' +
    '<div>' + esc(e.body) + '</div>' +
    '<small><span class="val">' + esc(e.trigger_value || '') + '</span> · ' + esc(e.triggered_at) + '</small>' +
  '</div>').join('');
}

function renderNotifyState() {
  const n = S.notify || {};
  $('alertNotifyState').textContent = n.supported ? (n.granted ? '已开启' : '未开启') : '不可用';
  $('meNotifyState').textContent = n.text || '读取中…';
  $('meAlertState').textContent = '已接入 · ' + (S.rules || []).filter((r) => Number(r.enabled)).length + ' 条规则';
  $('alertStatus').textContent = (S.rules || []).length + ' 条规则 · ' + (n.granted ? '系统通知已开启' : '系统通知未开启');
  const notice = $('alertNotice');
  if (notice && n.supported === false) {
    notice.hidden = false;
    notice.textContent = '当前运行环境没有 Android 本地通知桥接；规则和触发历史仍会保存在本机，但不会弹出系统通知。安装 APK 后可开启。';
  } else if (notice && !n.granted) {
    notice.hidden = false;
    notice.textContent = '系统通知权限尚未开启：规则会正常检查和记录，但需要点「开启系统通知」授权后才会弹出 Android 通知。';
  } else if (notice) {
    notice.hidden = true;
  }
}

async function loadAlerts() {
  try {
    const [rules, events, notify] = await Promise.all([
      getAlertRules(), getAlertEvents(50), notificationStatus()
    ]);
    S.rules = rules;
    S.events = events;
    S.notify = notify;
    populateRuleObjects();
    renderAlertRules();
    renderAlertHistory();
    renderNotifyState();
    renderHomeAlerts();
  } catch (err) {
    console.warn('alerts load failed', err);
    $('alertRules').innerHTML = '<div class="empty"><b>提醒数据读取失败</b>' +
      esc(err?.message || '本地数据库不可用') + '</div>';
  }
}

async function checkAlerts(manual = false) {
  if (S.alertChecking) return;
  S.alertChecking = true;
  try {
    if (!S.market) await loadMarket(true);
    const result = evaluateRules(S.rules, S.market, S.holdings);
    let sent = 0;
    for (const trigger of result.triggers) {
      const now = nowStr();
      const texts = await persistTrigger(trigger.rule, trigger.display, now);
      const event = {
        rule_id: trigger.rule.id, title: texts.title, body: texts.body,
        trigger_value: trigger.display, triggered_at: now
      };
      S.events.unshift(event);
      const note = await showLocalNotification(texts.title, texts.body);
      if (note.ok) sent++;
      toast('盯盘提醒：' + trigger.rule.obj_name + ' ' + trigger.display);
    }
    if (result.triggers.length) await loadAlerts();
    if (manual) {
      const notice = $('alertNotice');
      notice.hidden = false;
      if (result.triggers.length) {
        notice.textContent = '本次检查触发 ' + result.triggers.length + ' 条规则；' +
          (sent ? '已发送 ' + sent + ' 条 Android 系统通知。' : '系统通知未成功发送，但触发历史已保存。');
      } else {
        notice.textContent = '本次检查没有规则命中。检查依据：' +
          (S.market ? '实时指数行情 + 本机基金净值' : '本机基金净值（指数行情暂不可用）') + '。';
      }
    }
  } catch (err) {
    console.warn('alert check failed', err);
    if (manual) {
      const notice = $('alertNotice');
      notice.hidden = false;
      notice.textContent = '检查失败：' + (err?.message || '本地数据或行情不可用');
    }
  } finally {
    S.alertChecking = false;
  }
}

function updateRuleValueLabel() {
  const type = $('ruleCondType').value;
  const object = $('ruleObject').value || '';
  const isFund = object.startsWith('fund|');
  $('ruleValueLabel').textContent = type === 'pct'
    ? '触发值（%），例如 2 表示日涨跌幅达到 2%'
    : (isFund ? '触发净值（元/份），例如 1.2345' : '触发价格（点），例如 3000');
  $('ruleValue').placeholder = type === 'pct' ? '例如 2' : (isFund ? '例如 1.2345' : '例如 3000');
}

function openRuleModal() {
  populateRuleObjects();
  $('ruleValue').value = '';
  $('ruleNote').value = '';
  $('ruleCondType').value = 'pct';
  $('ruleDirection').value = 'up';
  $('ruleValid').value = 'until_trigger';
  updateRuleValueLabel();
  $('ruleModal').hidden = false;
  setTimeout(() => $('ruleValue').focus(), 120);
}

function closeRuleModal() { $('ruleModal').hidden = true; }

async function saveRule() {
  const selected = String($('ruleObject').value || '').split('|');
  const type = selected[0];
  const code = selected[1];
  const name = selected.slice(2).join('|');
  const condType = $('ruleCondType').value;
  const direction = $('ruleDirection').value;
  const value = Number($('ruleValue').value);
  if (!code || !name || !['index', 'fund'].includes(type)) {
    toast('请选择提醒对象');
    return;
  }
  if (!Number.isFinite(value) || value <= 0) {
    toast('请填写大于 0 的触发值');
    return;
  }
  if (condType === 'pct' && value > 100) {
    toast('日涨跌幅触发值不能超过 100%');
    return;
  }
  try {
    await addAlertRule({
      obj_type: type,
      obj_code: code,
      obj_name: name,
      market_type: type === 'index' ? 'index' : 'fund',
      cond_type: condType,
      cond_value: String(value),
      direction,
      valid_type: $('ruleValid').value,
      note: $('ruleNote').value.trim()
    });
    closeRuleModal();
    await loadAlerts();
    toast('提醒已保存到本机');
    checkAlerts(false);
  } catch (err) {
    toast('保存失败：' + (err?.message || '数据库不可用'));
  }
}

async function toggleRule(id) {
  const rule = (S.rules || []).find((r) => Number(r.id) === Number(id));
  if (!rule) return;
  try {
    await setAlertRuleEnabled(rule.id, !Number(rule.enabled));
    await loadAlerts();
  } catch (err) { toast('更新失败：' + (err?.message || '数据库不可用')); }
}

async function deleteRule(id) {
  if (!confirm('删除这条盯盘规则？本机触发历史会保留。')) return;
  await deleteAlertRule(id);
  await loadAlerts();
  toast('已删除规则');
}

async function enableNotifications() {
  const r = await requestNotificationPermission();
  S.notify = r;
  renderNotifyState();
  toast(r.text || (r.granted ? '通知权限已开启' : '通知权限未开启'));
}

function applyTheme(choice) {
  S.theme = choice || 'system';
  if (S.theme === 'system') {
    document.body.removeAttribute('data-theme');
    $('themeLabel').textContent = '跟随系统';
  } else {
    document.body.dataset.theme = S.theme;
    $('themeLabel').textContent = S.theme === 'dark' ? '深色' : '浅色';
  }
  document.querySelectorAll('[data-theme-choice]').forEach((b) => {
    b.classList.toggle('btn-primary', b.dataset.themeChoice === S.theme);
  });
}

async function setTheme(choice) {
  applyTheme(choice);
  await setMeta('ui_theme', S.theme);
}


function switchView(name) {
  S.view = name;
  document.querySelectorAll('.view').forEach((v) => {
    v.classList.toggle('active', v.dataset.view === name);
  });
  document.querySelectorAll('.tab').forEach((b) => {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  window.scrollTo(0, 0);
  if (name === 'home') loadMarket();
  if (name === 'news') {
    // 切换 Tab 只读本次启动已抓取的事件，避免把“最近刷新”改成更晚的时间。
    if (!S.news.length && !S.newsLoading) loadNews(false, S.lastRefresh || S.newsAt);
    else renderNews();
  }
  if (name === 'alerts') loadAlerts().then(() => checkAlerts(false));
  if (name === 'me') loadAlerts();
}

/* ===== 组合体检（独立 View，方案 A） ===== */
function openAnalysis(from) {
  S.analysisFrom = from || S.view;
  renderAnalysis(buildReport(S.holdings));
  switchView('analysis');
}

function backAnalysis() {
  switchView(S.analysisFrom || 'home');
}

function pctWidth(pctScaled) {
  if (pctScaled == null) return 0;
  return Math.max(0, Math.min(100, pctScaled / 10000));
}

function riskCardHtml(r) {
  return '<div class="pa-risk">' +
    '<h3>' + esc(r.title) + '<span class="rstatus">' + esc(r.status) + '</span></h3>' +
    '<p>' + esc(r.intro) + '</p>' +
    '<h4>' + esc(r.history_title) + '</h4>' +
    '<p>' + esc(r.history_intro) + '</p>' +
    '<ul>' + r.missing_metrics.map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul>' +
    '<div class="full"><b>' + esc(r.full_title) + '</b><br>' + esc(r.full_note) + '</div>' +
    '</div>';
}

function renderAnalysis(report) {
  const box = $('analysisBody');
  if (report.empty) {
    box.innerHTML = '<div class="empty"><b>还没有可分析的持仓</b>' + esc(report.message) + '</div>' +
      riskCardHtml(report.recent_risk);
    return;
  }
  const s = report.summary;

  // 数据日前置说明（客观陈述：截止日期 + 距今天数；不冒充最新、不发明「过期」阈值）
  let banner;
  if (!s.nav_dates || s.nav_dates.length === 0) {
    banner = '数据截至 —（本机暂无已公布净值，下方市值与收益暂无法计算）。';
  } else if (s.data_age_days == null) {
    banner = '数据截至 ' + esc(s.data_as_of) + '。';
  } else if (s.data_age_days <= 0) {
    banner = '数据截至 ' + esc(s.data_as_of) + '（当日净值）。';
  } else {
    banner = '数据截至 ' + esc(s.data_as_of) + '（距今 ' + s.data_age_days +
      ' 天）。下方指标基于已保存的净值，未包含此后可能已公布的净值。';
  }
  if (s.nav_dates && s.nav_dates.length && !s.nav_date_uniform) {
    banner += ' 各基金按最新已公布净值计算，明细处标注各自日期。';
  }
  if (s.complete === false && s.known_note) {
    banner += ' ' + esc(s.known_note);
  }

  let html = '<div class="pa-banner">' + banner + '</div>' +
    '<div class="pa-kpi">' +
    '<div class="cell wide"><div class="l">总市值（' + s.fund_count + ' 只）</div>' +
      '<div class="v num">' + formatMoney(s.total_value_cents) + '</div></div>' +
    '<div class="cell"><div class="l">累计盈亏</div>' +
      '<div class="v num ' + trendClass(s.total_profit_cents) + '">' + formatMoney(s.total_profit_cents, true) + '</div></div>' +
    '<div class="cell"><div class="l">累计收益率</div>' +
      '<div class="v num ' + trendClass(s.total_profit_cents) + '">' + formatPctScaled(s.total_profit_pct_scaled) + '</div></div>' +
    '<div class="cell"><div class="l">最大持仓占比</div>' +
      '<div class="v num">' + formatPctScaled(s.largest_holding_weight_pct_scaled, false) + '</div></div>' +
    '<div class="cell"><div class="l">前三大集中度</div>' +
      '<div class="v num">' + formatPctScaled(s.top3_concentration_pct_scaled, false) + '</div></div>' +
    '</div>';

  // 部分持仓缺净值：给「已知部分小计」并明确它不是完整组合
  if (s.complete === false && s.known_value_cents != null) {
    html += '<div class="card"><p class="pa-sub" style="margin:0;">已取得净值的 ' +
      s.valued_count + ' 只合计 ' + formatMoney(s.known_value_cents) +
      '（已知部分小计，不是完整组合总资产）；' + s.missing_nav_count +
      ' 只暂无净值：' + esc((s.missing_nav_codes || []).join('、')) +
      '。完整组合总市值与收益率暂不可确定。</p></div>';
  }

  // 持仓结构
  html += '<div class="card"><div class="card-title">持仓结构</div>' +
    '<p class="pa-sub" style="margin:0 0 6px;">看看资金主要分布在哪些基金中。</p>' +
    report.holdings_structure.map((m) => {
      if (!m.has_nav) {
        return '<div class="pa-line"><div class="t"><b>' + esc(m.name) + '</b><small>' + esc(m.code) + '</small></div>' +
          '<div class="vv"><b class="flat">尚未获取净值</b></div></div>';
      }
      return '<div class="pa-line"><div class="t"><b>' + esc(m.name) + '</b><small>' + esc(m.code) +
        (m.fund_type ? ' · ' + esc(m.fund_type) : '') + '</small></div>' +
        '<div class="vv"><b class="num">' + formatMoney(m.value_cents) + '</b>' +
        '<small>' + formatPctScaled(m.weight_pct_scaled, false) + ' · 盈亏 ' +
        '<span class="' + trendClass(m.profit_cents) + '">' + formatPctScaled(m.profit_pct_scaled) + '</span>' +
        ' · ' + esc(m.latest_nav_date) + '</small></div></div>';
    }).join('') + '</div>';

  // 风险集中（权重条）
  const c = report.concentration;
  html += '<div class="card"><div class="card-title">风险集中<span class="hint">前三大 ' +
    formatPctScaled(c.top3_concentration_pct_scaled, false) + '</span></div>' +
    '<p class="pa-sub" style="margin:0 0 6px;">了解组合是否过度集中在少数几只基金。</p>' +
    (c.items.length
      ? c.items.map((m) => '<div style="margin-top:10px;"><div class="pa-line" style="padding:0 0 4px;border:0;">' +
        '<div class="t"><b>' + esc(m.name) + '</b></div>' +
        '<div class="vv"><b>' + formatPctScaled(m.weight_pct_scaled, false) + '</b></div></div>' +
        '<div class="pa-bar"><i style="width:' + pctWidth(m.weight_pct_scaled) + '%"></i></div></div>').join('')
      : '') +
    '<p class="pa-sub">' + esc(c.note) + '</p></div>';

  // 风格重复（诚实占位）
  html += '<div class="card"><div class="card-title">风格重复<span class="pa-badge">数据不足</span></div>' +
    '<p class="pa-sub" style="margin:0;">' + esc(report.style_overlap.message) + '</p></div>';

  // 影响最大
  const latest = report.impact.latest;
  const cumulative = report.impact.cumulative;
  html += '<div class="card"><div class="card-title">影响最大</div>' +
    '<p class="pa-sub" style="margin:0 0 6px;">找出对当前持仓盈亏影响最大的基金。</p>' +
    '<div class="card-title" style="font-weight:400;font-size:12px;margin:0 0 4px;">最新净值变动 · ' + esc(latest.label) + '</div>' +
    (latest.items.length === 0
      ? '<p class="pa-sub">本机暂无已公布净值，暂无可展示的净值变动数据。</p>'
      : latest.complete === false
        ? '<p class="pa-sub">部分基金缺少有效净值，不合并为「组合合计」；已取得部分合计 ' +
          formatMoney(latest.known_total_cents, true) + '（仅供参考）。</p>'
        : latest.total_cents !== null
          ? '<div class="pa-line" style="padding-top:4px;"><div class="t"><b>组合合计</b></div>' +
            '<div class="vv"><b class="num ' + trendClass(latest.total_cents) + '">' + formatMoney(latest.total_cents, true) + '</b></div></div>'
          : '<p class="pa-sub">各基金最新净值日期不统一（含 QDII），不合并为「今日收益」，逐只显示对应日期。</p>') +
    latest.items.map((m) => '<div class="pa-line"><div class="t"><b>' + esc(m.name) + '</b><small>' + esc(m.nav_date) + '</small></div>' +
      '<div class="vv"><b class="num ' + trendClass(m.contribution_cents) + '">' + formatMoney(m.contribution_cents, true) + '</b></div></div>').join('') +
    '<div class="card-title" style="font-weight:400;font-size:12px;margin:12px 0 4px;">累计浮盈亏</div>' +
    cumulative.items.map((m) => '<div class="pa-line"><div class="t"><b>' + esc(m.name) + '</b></div>' +
      '<div class="vv"><b class="num ' + trendClass(m.profit_cents) + '">' + formatMoney(m.profit_cents, true) + '</b>' +
      '<small>' + formatPctScaled(m.profit_pct_scaled) + '</small></div></div>').join('') +
    (cumulative.complete === false
      ? '<p class="pa-sub">部分基金缺少有效净值，上方为已取得净值部分的单只盈亏，暂不给出完整组合累计盈亏。</p>'
      : '') + '</div>';

  // 近期风险（能力说明，不计算）
  html += riskCardHtml(report.recent_risk);

  // 值得关注（客观数据，不评级）
  const o = report.watchlist.objective;
  html += '<div class="card"><div class="card-title">值得关注<span class="hint">客观数据 · 不评级</span></div>' +
    '<p class="pa-sub" style="margin:0 0 6px;">汇总当前组合中值得进一步查看的客观信息。</p>' +
    '<div class="pa-line"><div class="t"><b>最大单只持仓占比</b></div><div class="vv"><b>' + formatPctScaled(o.max_single_weight_pct_scaled, false) + '</b></div></div>' +
    '<div class="pa-line"><div class="t"><b>前三大集中度</b></div><div class="vv"><b>' + formatPctScaled(o.top3_concentration_pct_scaled, false) + '</b></div></div>' +
    '<div class="pa-line"><div class="t"><b>最新净值日期</b></div><div class="vv"><b>' + esc(o.latest_nav_dates.join('、') || '—') + '</b></div></div>' +
    '<p class="pa-sub">' + esc(report.watchlist.note) + '</p></div>';

  box.innerHTML = html;
}

function render() {
  const sum = summary();
  // 我的基金页资产卡：与首页同口径三态渲染（元素 id 单独映射）
  renderSummaryCard('funds', sum, {
    totalId: 'totalValue', profitId: 'totalProfit', todayId: 'todayProfit',
    countId: 'holdingCount', footId: 'summaryFoot'
  });

  $('lastRefresh').textContent = S.lastRefresh
    ? '最近刷新 ' + S.lastRefresh
    : '尚未刷新净值';
  $('listCount').textContent = sum.count ? '共 ' + sum.count + ' 只' : '';

  const box = $('holdings');
  if (!S.holdings.length) {
    box.innerHTML = '<div class="empty"><b>还没有基金</b>点上方「＋ 添加基金」，搜索真实基金并填写成本价、份额。</div>';
  } else {
    box.innerHTML = S.holdings.map(holdingHtml).join('');
  }

  const closed = S.closed || [];
  $('closedSection').hidden = !closed.length;
  $('closedCount').textContent = closed.length ? '共 ' + closed.length + ' 只' : '';
  $('closedList').innerHTML = closed.map(closedHtml).join('');
  renderHomeSummary(sum);
  renderHomeNews();
  renderHomeAlerts();
  setBanner(S.banner);
}

function holdingHtml(h) {
  const mv = h.nav_scaled == null ? null : marketValueCents(h.shares_scaled, h.nav_scaled);
  const profit = mv == null ? null : mv - (h.cost_cents || 0);
  const today = h.nav_scaled == null ? null : todayProfitCents(h.shares_scaled, h.nav_scaled, h.prev_nav_scaled);
  const pct = formatRatioPct(profit, h.cost_cents);
  const navLine = h.nav_scaled == null
    ? '尚未获取净值，请点「刷新数据」'
    : '净值 ' + formatNav(h.nav_scaled) + ' · ' + esc(h.nav_date || '') +
      (h.daily_pct_scaled == null ? '' : ' · ' + formatPctScaled(h.daily_pct_scaled));
  return '' +
    '<div class="holding" data-id="' + h.id + '">' +
      '<div class="top"><div><div class="name">' + esc(h.name) + '</div>' +
        '<div class="code">' + esc(h.fund_code) + (h.fund_type ? ' · ' + esc(h.fund_type) : '') + '</div></div>' +
        '<span class="tag">' + esc(h.tag === 'heavy' ? '重仓' : '持仓') + '</span></div>' +
      '<div class="metrics">' +
        '<div class="metric"><div class="l">市值</div><div class="v num">' + (mv == null ? '—' : formatMoney(mv)) + '</div></div>' +
        '<div class="metric"><div class="l">累计收益</div><div class="v num ' + trendClass(profit) + '">' +
          (profit == null ? '—' : formatMoney(profit, true)) + '</div></div>' +
        '<div class="metric"><div class="l">今日盈亏</div><div class="v num ' + trendClass(today) + '">' +
          (today == null ? '—' : formatMoney(today, true)) + '</div></div>' +
        '<div class="metric"><div class="l">投入成本</div><div class="v num">' +
          formatMoney(h.cost_cents) + '</div></div>' +
        '<div class="metric"><div class="l">成本价（元/份）</div><div class="v num">' +
          '¥' + formatCostPrice(h.cost_cents, h.shares_scaled) + '</div></div>' +
        '<div class="metric"><div class="l">份额</div><div class="v num">' +
          formatShares(h.shares_scaled) + '</div></div>' +
      '</div>' +
      '<div class="sub">' + navLine + ' · 收益率 ' + pct + '</div>' +
      '<div class="ops"><button class="btn btn-small" data-act="refresh">刷新</button>' +
        '<button class="btn btn-small btn-danger" data-act="clear">清仓</button>' +
        '<button class="btn btn-small btn-ghost" data-act="delete">删除</button></div>' +
    '</div>';
}

function closedHtml(h) {
  const profit = h.profit_cents == null ? null : h.profit_cents;
  return '' +
    '<div class="holding" data-id="' + h.id + '">' +
      '<div class="top"><div><div class="name">' + esc(h.name) + '</div>' +
        '<div class="code">' + esc(h.fund_code) + ' · ' + esc(h.closed_at || '') + '</div></div>' +
        '<span class="tag">已清仓</span></div>' +
      '<div class="metrics">' +
        '<div class="metric"><div class="l">投入成本</div><div class="v num">' + formatMoney(h.cost_cents) + '</div></div>' +
        '<div class="metric"><div class="l">卖出金额</div><div class="v num">' + formatMoney(h.sell_cents) + '</div></div>' +
        '<div class="metric"><div class="l">最终收益</div><div class="v num ' + trendClass(profit) + '">' +
          (profit == null ? '—' : formatMoney(profit, true)) + '</div></div>' +
      '</div>' +
      '<div class="ops"><button class="btn btn-small btn-ghost" data-act="delete">删除记录</button></div>' +
    '</div>';
}

async function loadAll() {
  const rows = await getHoldings();
  S.holdings = rows.filter((r) => r.status === 'holding');
  S.closed = rows.filter((r) => r.status === 'closed');
  S.lastRefresh = await getMeta('last_refresh_at');
  render();
}

async function refreshAll(quiet = false, at = null) {
  if (S.busy) return;
  const cycleAt = at || nowStr();
  if (!S.holdings.length) {
    if (!quiet) toast('还没有持仓基金');
    return;
  }
  S.busy = true;
  const btn = $('refreshBtn');
  btn.disabled = true;
  btn.textContent = '刷新中…';
  setBanner('');
  let ok = 0, failed = 0;
  for (const h of S.holdings) {
    try {
      const nav = await fetchLatestNav(h.fund_code);
      await updateNav(h.id, nav);
      ok++;
    } catch (err) {
      failed++;
      console.warn('NAV refresh failed', h.fund_code, err);
    }
  }
  if (ok > 0) {
    await setMeta('last_refresh_at', cycleAt);
  }
  await loadAll();
  S.busy = false;
  btn.disabled = false;
  btn.textContent = '刷新数据';
  if (failed) {
    setBanner('已更新 ' + ok + ' 只，' + failed + ' 只失败；失败基金继续显示上次真实净值。数据源：天天基金公开接口。');
  } else if (!quiet) {
    toast('净值已更新');
  }
}

async function refreshOne(h) {
  try {
    const nav = await fetchLatestNav(h.fund_code);
    await updateNav(h.id, nav);
    await setMeta('last_refresh_at', nowStr());
    await loadAll();
    toast('已更新 ' + h.name);
  } catch (err) {
    setBanner('刷新失败：' + (err && err.message ? err.message : '数据源暂不可用') + '；保留上次真实净值。');
  }
}

function openAdd() {
  S.selected = null;
  S.selectedNav = null;
  S.searchSeq++;
  $('searchInput').value = '';
  $('searchResults').innerHTML = '<div class="search-hint">输入关键词后搜索全市场基金（首次约需下载 3MB 名单）。</div>';
  $('selectedBox').hidden = true;
  $('costInput').value = '';
  $('sharesInput').value = '';
  $('calcPreview').hidden = true;
  $('confirmAddBtn').disabled = true;
  $('addModal').hidden = false;
  setTimeout(() => $('searchInput').focus(), 120);
}

function closeAdd() {
  $('addModal').hidden = true;
  S.searchSeq++;
}

let searchTimer = null;
async function doSearch(keyword) {
  const q = String(keyword || '').trim();
  if (!q) {
    $('searchResults').innerHTML = '<div class="search-hint">输入基金代码或名称关键词。</div>';
    return;
  }
  const seq = ++S.searchSeq;
  $('searchResults').innerHTML = '<div class="search-hint">正在搜索全市场基金…（首次需下载名单，约 3MB）</div>';
  try {
    if (!S.fundList) S.fundList = await getFundList();
    if (seq !== S.searchSeq) return;
    const hits = searchFunds(S.fundList, q, 20);
    if (!hits.length) {
      $('searchResults').innerHTML = '<div class="search-hint">没有找到匹配的基金，换个代码或名称试试。</div>';
      return;
    }
    $('searchResults').innerHTML = hits.map((f) =>
      '<div class="search-item" data-code="' + esc(f.code) + '" data-name="' + esc(f.name) +
      '" data-type="' + esc(f.fund_type) + '"><div class="n">' + esc(f.name) + '</div>' +
      '<div class="d">' + esc(f.code) + (f.fund_type ? ' · ' + esc(f.fund_type) : '') + '</div></div>'
    ).join('');
  } catch (err) {
    if (seq !== S.searchSeq) return;
    $('searchResults').innerHTML = '<div class="search-hint">基金名单获取失败：' +
      esc(err && err.message ? err.message : '网络不可用') + '。请检查网络后重试。</div>';
  }
}

async function selectFund(item) {
  S.selected = item;
  S.selectedNav = null;
  $('selectedBox').hidden = false;
  $('calcPreview').hidden = true;
  $('confirmAddBtn').disabled = true;
  $('navPreview').className = 'preview-box';
  $('navPreview').textContent = '正在获取最新真实净值…';
  try {
    const nav = await fetchLatestNav(item.code);
    if (!S.selected || S.selected.code !== item.code) return;
    S.selectedNav = nav;
    $('navPreview').textContent = '最新净值 ' + formatNav(nav.nav_scaled) +
      '（' + nav.nav_date + '）' + (nav.daily_pct_scaled == null ? '' : ' · ' + formatPctScaled(nav.daily_pct_scaled));
  } catch (err) {
    if (!S.selected || S.selected.code !== item.code) return;
    $('navPreview').className = 'preview-box err';
    $('navPreview').textContent = '净值暂时获取失败：' + (err && err.message ? err.message : '数据源不可用') +
      '。仍可添加，添加后再刷新净值。';
  }
  updateCalc();
}

function updateCalc() {
  const costScaled = parseScaled($('costInput').value, 4);
  const sharesScaled = parseScaled($('sharesInput').value, 4);
  const valid = Number.isFinite(costScaled) && costScaled > 0 &&
    Number.isFinite(sharesScaled) && sharesScaled > 0;
  const box = $('calcPreview');
  if (!valid) {
    box.hidden = true;
    $('confirmAddBtn').disabled = true;
    return;
  }
  const cost = costCents(costScaled, sharesScaled);
  if (S.selectedNav) {
    const mv = marketValueCents(sharesScaled, S.selectedNav.nav_scaled);
    const profit = mv - cost;
    box.hidden = false;
    box.className = 'preview-box';
    box.textContent = '预计投入 ' + formatMoney(cost) + ' · 按最新净值预计市值 ' +
      formatMoney(mv) + ' · 收益 ' + formatMoney(profit, true) +
      '（' + formatRatioPct(profit, cost) + '）';
  } else {
    box.hidden = false;
    box.className = 'preview-box err';
    box.textContent = '已填写成本/份额；当前没有可用的最新净值，添加后请点「刷新净值」。';
  }
  $('confirmAddBtn').disabled = false;
}

async function confirmAdd() {
  if (!S.selected) return;
  const costScaled = parseScaled($('costInput').value, 4);
  const sharesScaled = parseScaled($('sharesInput').value, 4);
  if (!Number.isFinite(costScaled) || costScaled <= 0 ||
      !Number.isFinite(sharesScaled) || sharesScaled <= 0) {
    toast('请填写大于 0 的成本价和份额');
    return;
  }
  const cost = costCents(costScaled, sharesScaled);
  const nav = S.selectedNav;
  const id = await addHolding({
    fund_code: S.selected.code,
    name: S.selected.name,
    fund_type: S.selected.fund_type,
    cost_cents: cost,
    shares_scaled: sharesScaled,
    open_date: todayStr(),
    nav_date: nav ? nav.nav_date : null,
    nav_scaled: nav ? nav.nav_scaled : null,
    prev_nav_scaled: nav ? nav.prev_nav_scaled : null,
    daily_pct_scaled: nav ? nav.daily_pct_scaled : null,
    fetched_at: nav ? nowStr() : null
  });
  if (nav) await setMeta('last_refresh_at', nowStr());
  const selected = S.selected;
  closeAdd();
  await loadAll();
  toast('已添加 ' + selected.name);
  if (!nav) {
    try {
      const latest = await fetchLatestNav(selected.code);
      await updateNav(id, latest);
      await setMeta('last_refresh_at', nowStr());
      await loadAll();
    } catch (_) {
      setBanner('已添加，但最新净值暂时获取失败；稍后点「刷新净值」重试。');
    }
  }
}

async function clearOne(h) {
  if (h.nav_scaled == null) {
    toast('请先刷新净值再清仓');
    return;
  }
  const mv = marketValueCents(h.shares_scaled, h.nav_scaled);
  const profit = mv - (h.cost_cents || 0);
  if (!confirm('按最新净值 ' + formatNav(h.nav_scaled) + ' 清仓？\n卖出金额 ' +
    formatMoney(mv) + '，最终收益 ' + formatMoney(profit, true) + '。')) return;
  await clearHolding(h.id, mv, profit);
  await loadAll();
  toast('已清仓，记录已保留');
}

async function deleteOne(h) {
  if (!confirm('删除「' + h.name + '」这条本地记录？删除后无法恢复。')) return;
  await deleteHolding(h.id);
  await loadAll();
  toast('已删除');
}

async function refreshEverything() {
  // 一次打开/手动刷新只使用同一个周期时间：
  // 净值检查、市场事件抓取、首页与事件页时间都写这一个值。
  const at = nowStr();
  S.newsAt = at;
  await refreshAll(true, at);
  await loadNews(true, at);
  await loadMarket(true);
  await setMeta('last_refresh_at', at);
  await setMeta('news_fetched_at', at);
  S.lastRefresh = at;
  render();
}

function bind() {
  $('refreshBtn').addEventListener('click', refreshEverything);
  $('homeRefreshBtn').addEventListener('click', refreshEverything);
  $('homeAddBtn').addEventListener('click', openAdd);
  $('homeFundsBtn').addEventListener('click', () => switchView('funds'));
  $('homeNewsAll').addEventListener('click', () => switchView('news'));
  $('homeAlertsMore').addEventListener('click', () => switchView('alerts'));
  $('homeNews').addEventListener('click', (e) => {
    const open = e.target.closest('[data-url], #homeNewsOpen');
    if (!open) return;
    if (open.id === 'homeNewsOpen' || !open.dataset.url) {
      switchView('news');
      return;
    }
    window.open(open.dataset.url, '_blank');
  });
  $('homeAlerts').addEventListener('click', (e) => {
    if (e.target.closest('#homeAlertOpen')) switchView('alerts');
  });
  $('fundsAnalysisBtn').addEventListener('click', () => openAnalysis('funds'));
  $('analysisBack').addEventListener('click', backAnalysis);
  $('tabbar').addEventListener('click', (e) => {
    const tab = e.target.closest('.tab');
    if (tab) switchView(tab.dataset.tab);
  });
  $('newsFilters').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    S.newsFilter = chip.dataset.filter || 'all';
    document.querySelectorAll('#newsFilters .chip').forEach((x) => {
      x.classList.toggle('active', x === chip);
    });
    renderNews();
  });
  $('newsRetry').addEventListener('click', () => loadNews(true, S.lastRefresh || S.newsAt));
  $('newsList').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const card = btn.closest('.news-card');
    const item = S.news.find((x) => x.id === (card && card.dataset.id));
    if (!item) return;
    const act = btn.dataset.act;
    if (act === 'open') {
      await toggleNewsState(item.id, { is_read: true });
      if (item.url) window.open(item.url, '_blank');
    } else if (act === 'read') {
      await toggleNewsState(item.id, { is_read: !Number(item.is_read) });
    } else if (act === 'fav') {
      await toggleNewsState(item.id, { is_favorite: !Number(item.is_favorite) });
    }
  });
  $('addAlertBtn').addEventListener('click', openRuleModal);
  $('checkAlertsBtn').addEventListener('click', () => checkAlerts(true));
  $('notifyPermissionBtn').addEventListener('click', enableNotifications);
  $('alertRules').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const card = btn.closest('.alert-rule');
    if (!card) return;
    const id = Number(card.dataset.id);
    if (btn.dataset.act === 'toggle') await toggleRule(id);
    else if (btn.dataset.act === 'delete') await deleteRule(id);
  });
  $('closeRuleBtn').addEventListener('click', closeRuleModal);
  $('cancelRuleBtn').addEventListener('click', closeRuleModal);
  $('ruleModal').addEventListener('click', (e) => { if (e.target === $('ruleModal')) closeRuleModal(); });
  $('saveRuleBtn').addEventListener('click', saveRule);
  $('ruleObject').addEventListener('change', updateRuleValueLabel);
  $('ruleCondType').addEventListener('change', updateRuleValueLabel);
  document.querySelectorAll('[data-theme-choice]').forEach((b) => {
    b.addEventListener('click', () => setTheme(b.dataset.themeChoice));
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && S.rules.length) checkAlerts(false);
  });
  $('homeMarket').addEventListener('click', (e) => {
    if (e.target.closest('#marketRetry')) loadMarket(true);
  });
  $('addBtn').addEventListener('click', openAdd);
  $('closeAddBtn').addEventListener('click', closeAdd);
  $('cancelAddBtn').addEventListener('click', closeAdd);
  $('addModal').addEventListener('click', (e) => { if (e.target === $('addModal')) closeAdd(); });

  $('searchInput').addEventListener('input', function () {
    clearTimeout(searchTimer);
    const value = this.value;
    searchTimer = setTimeout(() => doSearch(value), 350);
  });
  $('searchResults').addEventListener('click', (e) => {
    const item = e.target.closest('.search-item');
    if (!item) return;
    document.querySelectorAll('.search-item').forEach((x) => x.classList.toggle('selected', x === item));
    selectFund({ code: item.dataset.code, name: item.dataset.name, fund_type: item.dataset.type });
  });
  $('costInput').addEventListener('input', updateCalc);
  $('sharesInput').addEventListener('input', updateCalc);
  $('confirmAddBtn').addEventListener('click', confirmAdd);

  $('holdings').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const card = btn.closest('.holding');
    if (!card) return;
    const id = Number(card.dataset.id);
    const h = S.holdings.find((x) => x.id === id);
    if (!h) return;
    if (btn.dataset.act === 'refresh') await refreshOne(h);
    else if (btn.dataset.act === 'clear') await clearOne(h);
    else if (btn.dataset.act === 'delete') await deleteOne(h);
  });
  $('closedList').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act="delete"]');
    if (!btn) return;
    const card = btn.closest('.holding');
    const h = (S.closed || []).find((x) => x.id === Number(card && card.dataset.id));
    if (h) await deleteOne(h);
  });
}

// 版本展示：构建时由 vite 注入，唯一来源 android/version.properties；debug 渠道额外标注
const APP_VERSION = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.0-unknown';
const APP_CHANNEL = typeof __APP_CHANNEL__ !== 'undefined' ? __APP_CHANNEL__ : '';
const IS_DEV_BUILD = APP_CHANNEL === 'development-debug';

function applyVersionLabels() {
  const full = IS_DEV_BUILD ? APP_VERSION + '-' + APP_CHANNEL : APP_VERSION;
  const short = full.replace(/^0\.1\.0-preview-/, '');
  const top = $('topVersionBadge');
  if (top) top.textContent = 'PREVIEW ' + short;
  const about = $('aboutVersion');
  if (about) about.textContent = full;
  const badge = $('devBadge');
  if (badge) badge.hidden = !IS_DEV_BUILD;
  const note = $('devNote');
  if (note) note.hidden = !IS_DEV_BUILD;
}

async function init() {
  applyVersionLabels();
  bind();
  try {
    await initDb();
    const savedTheme = await getMeta('ui_theme');
    applyTheme(savedTheme || 'system');
    await loadAll();
    await loadAlerts();
    switchView('home');
    // 每次打开软件（进程启动）都刷新一次：净值 + 市场事件 + 行情同步走同一周期，
    // 首页和事件页显示同一个“最近刷新”时间。
    setTimeout(() => refreshEverything(), 350);
    setInterval(() => {
      if (document.visibilityState === 'visible' && S.rules.length) checkAlerts(false);
    }, 60 * 1000);
  } catch (err) {
    console.error(err);
    $('holdings').innerHTML = '<div class="empty"><b>本地数据库初始化失败</b>' +
      esc(err && err.message ? err.message : '请重新打开应用') + '</div>';
    setBanner('本地 SQLite 初始化失败，数据未保存；请重开应用或检查安装包。');
  }
}

init();
