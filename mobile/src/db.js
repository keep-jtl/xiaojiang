// Android 本地 SQLite（@capacitor-community/sqlite，原生 SQLite/SQLCipher 引擎）
import { CapacitorSQLite, SQLiteConnection } from '@capacitor-community/sqlite';

const DB_NAME = 'xiaolicaitong';
// v3：市场事件缓存增加中文译文字段；v2 已有持仓/提醒数据原地升级，不删除。
const DB_VERSION = 3;
const sqlite = new SQLiteConnection(CapacitorSQLite);
let db = null;

const SCHEMA = `
CREATE TABLE IF NOT EXISTS holdings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fund_code TEXT NOT NULL,
  name TEXT NOT NULL,
  fund_type TEXT,
  cost_cents INTEGER NOT NULL DEFAULT 0,
  shares_scaled INTEGER NOT NULL DEFAULT 0,
  open_date TEXT,
  status TEXT NOT NULL DEFAULT 'holding',
  tag TEXT NOT NULL DEFAULT 'normal',
  nav_date TEXT,
  nav_scaled INTEGER,
  prev_nav_scaled INTEGER,
  daily_pct_scaled INTEGER,
  fetched_at TEXT,
  closed_at TEXT,
  sell_cents INTEGER,
  profit_cents INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_holdings_status ON holdings(status);

CREATE TABLE IF NOT EXISTS alert_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obj_type TEXT NOT NULL,
  obj_code TEXT NOT NULL,
  obj_name TEXT NOT NULL,
  market_type TEXT NOT NULL DEFAULT 'index',
  cond_type TEXT NOT NULL,
  cond_value TEXT NOT NULL,
  direction TEXT NOT NULL,
  valid_type TEXT NOT NULL DEFAULT 'until_trigger',
  valid_until TEXT,
  note TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_state INTEGER NOT NULL DEFAULT 0,
  last_triggered_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alert_rules_enabled ON alert_rules(enabled);

CREATE TABLE IF NOT EXISTS alert_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id INTEGER,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  trigger_value TEXT,
  triggered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alert_events_time ON alert_events(triggered_at);

CREATE TABLE IF NOT EXISTS news_cache (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_name TEXT,
  title TEXT NOT NULL,
  summary TEXT,
  title_zh TEXT,
  summary_zh TEXT,
  translation_status TEXT,
  url TEXT,
  published_at TEXT,
  published_ts INTEGER,
  importance REAL,
  category TEXT,
  relation TEXT,
  relation_text TEXT,
  fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_news_cache_time ON news_cache(published_ts);

CREATE TABLE IF NOT EXISTS news_state (
  news_id TEXT PRIMARY KEY,
  is_read INTEGER NOT NULL DEFAULT 0,
  is_favorite INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS translation_cache (
  source_text TEXT PRIMARY KEY,
  translated_text TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT 'zh-CN',
  created_at TEXT NOT NULL
);
`;

export async function initDb() {
  try { await sqlite.checkConnectionsConsistency(); } catch (_) { /* web/dev 或首次启动 */ }
  const has = await sqlite.isConnection(DB_NAME, false);
  db = has.result
    ? await sqlite.retrieveConnection(DB_NAME, false)
    : await sqlite.createConnection(DB_NAME, false, 'no-encryption', DB_VERSION, false);
  const opened = await db.isDBOpen();
  if (!opened.result) await db.open();
  await db.execute(SCHEMA, true);
  // 旧 v2 库补列：SQLite 的 CREATE TABLE IF NOT EXISTS 不会改已存在的表。
  const info = await db.query('PRAGMA table_info(news_cache)');
  const cols = new Set(rows(info).map((c) => c.name));
  for (const col of ['title_zh', 'summary_zh', 'translation_status']) {
    if (!cols.has(col)) {
      await db.execute(`ALTER TABLE news_cache ADD COLUMN ${col} TEXT`, true);
    }
  }
}

function rows(result) {
  return (result && result.values) || [];
}

export async function getHoldings() {
  const q = await db.query(
    'SELECT * FROM holdings ORDER BY CASE status WHEN \'holding\' THEN 0 ELSE 1 END, id DESC'
  );
  return rows(q);
}

export async function addHolding(item) {
  const now = nowStr();
  await db.run(
    `INSERT INTO holdings
      (fund_code, name, fund_type, cost_cents, shares_scaled, open_date, status, tag,
       nav_date, nav_scaled, prev_nav_scaled, daily_pct_scaled, fetched_at, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, 'holding', 'normal', ?, ?, ?, ?, ?, ?, ?)`,
    [item.fund_code, item.name, item.fund_type || '', item.cost_cents,
     item.shares_scaled, item.open_date || todayStr(), item.nav_date || null,
     item.nav_scaled == null ? null : item.nav_scaled,
     item.prev_nav_scaled == null ? null : item.prev_nav_scaled,
     item.daily_pct_scaled == null ? null : item.daily_pct_scaled,
     item.fetched_at || null, now, now]
  );
  const r = await db.query('SELECT last_insert_rowid() AS id');
  return rows(r)[0] ? rows(r)[0].id : null;
}

export async function updateNav(id, nav) {
  await db.run(
    `UPDATE holdings SET nav_date=?, nav_scaled=?, prev_nav_scaled=?, daily_pct_scaled=?,
       fetched_at=?, updated_at=? WHERE id=?`,
    [nav.nav_date, nav.nav_scaled,
     nav.prev_nav_scaled == null ? null : nav.prev_nav_scaled,
     nav.daily_pct_scaled == null ? null : nav.daily_pct_scaled,
     nowStr(), nowStr(), id]
  );
}

export async function clearHolding(id, sellCents, profitCents) {
  await db.run(
    `UPDATE holdings SET status='closed', closed_at=?, sell_cents=?, profit_cents=?, updated_at=?
     WHERE id=? AND status='holding'`,
    [todayStr(), sellCents, profitCents, nowStr(), id]
  );
}

export async function deleteHolding(id) {
  await db.run('DELETE FROM holdings WHERE id=?', [id]);
}

export async function getMeta(key) {
  const r = await db.query('SELECT value FROM meta WHERE key=?', [key]);
  return rows(r)[0] ? rows(r)[0].value : null;
}

export async function setMeta(key, value) {
  await db.run(
    'INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?) ' +
    'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
    [key, String(value), nowStr()]
  );
}

/* ---------- v2：盯盘规则 ---------- */
export async function getAlertRules() {
  const r = await db.query('SELECT * FROM alert_rules ORDER BY enabled DESC, id DESC');
  return rows(r);
}

export async function addAlertRule(rule) {
  const now = nowStr();
  await db.run(
    `INSERT INTO alert_rules
      (obj_type, obj_code, obj_name, market_type, cond_type, cond_value, direction,
       valid_type, valid_until, note, enabled, last_state, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)`,
    [rule.obj_type, rule.obj_code, rule.obj_name, rule.market_type || 'index',
     rule.cond_type, String(rule.cond_value), rule.direction, rule.valid_type || 'until_trigger',
     rule.valid_until || null, rule.note || '', now, now]
  );
  const id = await db.query('SELECT last_insert_rowid() AS id');
  return rows(id)[0] ? rows(id)[0].id : null;
}

export async function setAlertRuleEnabled(id, enabled) {
  await db.run(
    'UPDATE alert_rules SET enabled=?, last_state=0, updated_at=? WHERE id=?',
    [enabled ? 1 : 0, nowStr(), id]
  );
}

export async function deleteAlertRule(id) {
  await db.run('DELETE FROM alert_rules WHERE id=?', [id]);
}

export async function markAlertRuleState(id, state, triggeredAt) {
  await db.run(
    'UPDATE alert_rules SET last_state=?, last_triggered_at=COALESCE(?, last_triggered_at), updated_at=? WHERE id=?',
    [state ? 1 : 0, triggeredAt || null, nowStr(), id]
  );
}

export async function addAlertEvent(event) {
  await db.run(
    `INSERT INTO alert_events (rule_id, title, body, trigger_value, triggered_at)
     VALUES (?, ?, ?, ?, ?)`,
    [event.rule_id, event.title, event.body, String(event.trigger_value || ''),
     event.triggered_at || nowStr()]
  );
}

export async function getAlertEvents(limit = 50) {
  const r = await db.query(
    'SELECT * FROM alert_events ORDER BY id DESC LIMIT ?', [limit]
  );
  return rows(r);
}

/* ---------- v2：市场事件缓存与阅读状态 ---------- */
export async function saveNewsCache(items) {
  const at = nowStr();
  for (const item of (items || [])) {
    await db.run(
      `INSERT INTO news_cache
        (id, source, source_name, title, summary, title_zh, summary_zh, translation_status,
         url, published_at, published_ts, importance, category, relation, relation_text, fetched_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         source_name=excluded.source_name, title=excluded.title, summary=excluded.summary,
         title_zh=excluded.title_zh, summary_zh=excluded.summary_zh,
         translation_status=excluded.translation_status,
         url=excluded.url, published_at=excluded.published_at, published_ts=excluded.published_ts,
         importance=excluded.importance, category=excluded.category, relation=excluded.relation,
         relation_text=excluded.relation_text, fetched_at=excluded.fetched_at`,
      [item.id, item.source, item.source_name || '', item.title, item.summary || '',
       item.title_zh || '', item.summary_zh || '', item.translation_status || '',
       item.url || '', item.published_at || '', item.published_ts || 0, item.importance || 0,
       item.category || '', item.relation || 'discover', item.relation_text || '', at]
    );
  }
}

export async function getNewsCache(limit = 40) {
  const r = await db.query(
    `SELECT c.*, COALESCE(s.is_read, 0) AS is_read, COALESCE(s.is_favorite, 0) AS is_favorite
     FROM news_cache c LEFT JOIN news_state s ON s.news_id = c.id
     ORDER BY c.published_ts DESC LIMIT ?`, [limit]
  );
  return rows(r);
}

/* ---------- v3：英文市场事件翻译缓存（同一条原文只请求一次） ---------- */
export async function getTranslationCache(sourceText) {
  const text = String(sourceText || '').trim();
  if (!text) return '';
  const r = await db.query(
    'SELECT translated_text FROM translation_cache WHERE source_text=? LIMIT 1', [text]
  );
  return rows(r)[0] ? String(rows(r)[0].translated_text || '') : '';
}

export async function saveTranslationCache(sourceText, translatedText, target = 'zh-CN') {
  const text = String(sourceText || '').trim();
  const out = String(translatedText || '').trim();
  if (!text || !out) return;
  await db.run(
    `INSERT INTO translation_cache (source_text, translated_text, target, created_at)
     VALUES (?, ?, ?, ?) ON CONFLICT(source_text) DO UPDATE SET
       translated_text=excluded.translated_text, target=excluded.target, created_at=excluded.created_at`,
    [text, out, target, nowStr()]
  );
}

export async function setNewsState(newsId, patch) {
  const current = await db.query(
    'SELECT is_read, is_favorite FROM news_state WHERE news_id=?', [newsId]
  );
  const old = rows(current)[0] || { is_read: 0, is_favorite: 0 };
  const isRead = Object.prototype.hasOwnProperty.call(patch, 'is_read')
    ? (patch.is_read ? 1 : 0) : (old.is_read || 0);
  const isFavorite = Object.prototype.hasOwnProperty.call(patch, 'is_favorite')
    ? (patch.is_favorite ? 1 : 0) : (old.is_favorite || 0);
  await db.run(
    `INSERT INTO news_state (news_id, is_read, is_favorite, updated_at)
     VALUES (?, ?, ?, ?)
     ON CONFLICT(news_id) DO UPDATE SET is_read=excluded.is_read,
       is_favorite=excluded.is_favorite, updated_at=excluded.updated_at`,
    [newsId, isRead, isFavorite, nowStr()]
  );
}

export function nowStr() {
  const d = new Date();
  return fmtDate(d) + ' ' + fmtTime(d);
}

export function todayStr() {
  return fmtDate(new Date());
}

function fmtDate(d) {
  const p = (x) => String(x).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

function fmtTime(d) {
  const p = (x) => String(x).padStart(2, '0');
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}
