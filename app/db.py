# -*- coding: utf-8 -*-
"""小财通本地数据库（SQLite）——schema v6 迁移基线（2026-11-06，1B）

表分类：
- 用户数据（将来云同步范围）：holdings / invest_events / alerts / alert_events / settings
- 数据源缓存（可重建，不同步）：fund_meta / fund_nav_daily / market_meta / market_quotes
- 程序自用：app_meta

关键约定（决策记录 2026-09-03）：
- 数值全缩放整数：金额×100（分）、净值/份额×10000、涨跌幅×100、点位×100
- 时间/日期统一 TEXT 本地时间 'YYYY-MM-DD HH:MM:SS' / 'YYYY-MM-DD'（不用 SQLite CURRENT_TIMESTAMP，它是 UTC）
- 提醒有效期四模式：once 仅下一交易日 / until_trigger 持续到触发 / ongoing 长期有效 / range 指定范围；
  triggered_at 仅属 until_trigger（其余模式由 CHECK 锁死为 NULL），ongoing 同日不重复完全由
  alert_events 的 UNIQUE(alert_id, trigger_date) 承担
- 触发三态原则："条件触发"≠"提醒成功"——alert_events.notify_state 是旧兼容字段（sent/skipped）；
  1B 起新增 notify_result（pending/submitted/failed/skipped）+ notify_error，新逻辑只看新字段；
  until_trigger 在触发事件成功写入后立即失效（不再等待通知送达/用户确认），acked 仍单独记录用户确认
- 业务表带 uuid（云同步全局键预留）+ deleted 软删标记（仅 holdings/alerts）
"""
import os
import sqlite3

from . import news_rules

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "xltc.db")

SCHEMA_SQL = """
-- ============ 一、数据源缓存（可重建） ============

-- 1. 基金档案缓存
CREATE TABLE IF NOT EXISTS fund_meta (
    code        TEXT NOT NULL PRIMARY KEY,   -- 基金代码（6 位）
    name        TEXT NOT NULL,               -- 基金名称
    fund_type   TEXT,                        -- 类型：混合型/指数型/QDII/债券型/股票型
    manager     TEXT,                        -- 现任经理（MVP 单值简化，二期拆经理表）
    track_index TEXT,                        -- 跟踪指数（指数型）
    risk_level  TEXT,                        -- 风险等级
    source      TEXT NOT NULL,               -- 数据源标识（数据源抽象层要求）
    fetched_at  TEXT NOT NULL,               -- 抓取时间（本地时间）
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fund_meta_name ON fund_meta(name);

-- 2. 每日净值缓存（缩放整数：×10000）
CREATE TABLE IF NOT EXISTS fund_nav_daily (
    fund_code        TEXT NOT NULL,          -- 基金代码
    nav_date         TEXT NOT NULL,          -- 净值日期 YYYY-MM-DD
    unit_nav_scaled  INTEGER NOT NULL,       -- 单位净值 ×10000：2.4378 元 → 24378
    accum_nav_scaled INTEGER,                -- 累计净值 ×10000（可空，源缺失）
    daily_pct_scaled INTEGER,                -- 日增长率 ×10000：+1.82% → 18200、-0.70% → -7000
                                             -- （2026-09-03 真测修正：历史数据含 4 位小数如 -2.1586 → -21586，故与净值同容差）
    source           TEXT NOT NULL,          -- 数据源标识
    fetched_at       TEXT NOT NULL,          -- 本地抓取时间
    PRIMARY KEY (fund_code, nav_date),
    FOREIGN KEY (fund_code) REFERENCES fund_meta(code) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_nav_date ON fund_nav_daily(nav_date);

-- 7. 行情对象花名册（预置种子，真实行情代码）
CREATE TABLE IF NOT EXISTS market_meta (
    code          TEXT NOT NULL PRIMARY KEY, -- 行情代码（sh000300 等）
    name          TEXT NOT NULL,             -- 显示名
    mkt_type      TEXT NOT NULL CHECK (mkt_type IN ('index','sector','commodity')),  -- 指数/赛道/商品（黄金）
    market        TEXT NOT NULL CHECK (market IN ('cn','hk','us')),                  -- 所属市场（时段感知）
    display_order INTEGER NOT NULL DEFAULT 0, -- 首页速览基础排序
    is_preset     INTEGER NOT NULL DEFAULT 1, -- 1=出厂预置 0=用户自定义
    enabled       INTEGER NOT NULL DEFAULT 1, -- 1=启用 0=下架
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_market_meta_market ON market_meta(market, display_order);

-- 8. 行情快照（每对象一行，缩放整数：点位/昨收/涨跌幅 ×10000，与净值同容差）
CREATE TABLE IF NOT EXISTS market_quotes (
    mkt_code           TEXT PRIMARY KEY,     -- 行情代码
    price_scaled       INTEGER,              -- 最新点位 ×10000：4126.58 → 41265800
    change_pct_scaled  INTEGER,              -- 涨跌幅 ×10000（带符号）
    prev_close_scaled  INTEGER,              -- 昨收 ×10000
    quote_time         TEXT,                 -- 行情时间（数据源标注）
    source             TEXT NOT NULL,        -- 数据来源（tencent/sina/eastmoney 三源切换留痕）
    fetched_at         TEXT NOT NULL,
    FOREIGN KEY (mkt_code) REFERENCES market_meta(code) ON DELETE CASCADE
);

-- 11. 交易日历（三市场统一，2026-09-05 用户拍板的统一底层服务）
-- 判断依据 = 各交易所实际开市日（官方已公布），绝不按"工作日"猜测；
-- 存全量（含休市日）：查不到行 = 日历缺失 → 显式报错，绝不当作休市
CREATE TABLE IF NOT EXISTS market_calendar (
    market      TEXT NOT NULL,               -- 市场：cn=A股 / hk=港股 / us=美股（美股=美东日期）
    trade_date  TEXT NOT NULL,               -- 该市场本地日期 YYYY-MM-DD（美股勿用北京日期）
    is_open     INTEGER NOT NULL CHECK (is_open IN (0,1)),
                                              -- 1=交易日 0=休市日
    early_close TEXT,                        -- 提前收盘 HH:MM（本地口径）：港股半天='12:00'，
                                              -- 美股黑五='13:00'（美东）；NULL=正常收盘
    source      TEXT NOT NULL,               -- builtin=内置官方确认 / sina=新浪 / ics1823=香港政府
                                              -- / rule_nyse=纽交所规则生成 / manual=补丁
    fetched_at  TEXT NOT NULL,               -- 写入时刻（本地时间）
    PRIMARY KEY (market, trade_date)
);
CREATE INDEX IF NOT EXISTS ix_market_calendar ON market_calendar(market, trade_date);

-- ============ 二、用户数据（将来云同步范围） ============

-- 3. 我的持仓主表（持仓/仅关注/已清仓同表，status 区分；记录与复盘同处一行）
CREATE TABLE IF NOT EXISTS holdings (
    id             INTEGER NOT NULL PRIMARY KEY,
    uuid           TEXT    NOT NULL UNIQUE,  -- 全局唯一 ID（云同步预留）
    fund_code      TEXT    NOT NULL,         -- 基金代码
    status         TEXT    NOT NULL DEFAULT 'holding'
                   CHECK (status IN ('holding','watch','closed')),
                   -- holding=持有(算资产) / watch=纯关注(不算资产) / closed=已清仓(历史持仓可回看)
    tag            TEXT    NOT NULL DEFAULT 'normal'
                   CHECK (tag IN ('heavy','normal','watch')),
                   -- 重仓/普通/仅关注 三档标记（与 status 独立维度）
    cost_cents     INTEGER,                  -- 本金（分）：43000 元 → 4300000
    shares_scaled  INTEGER,                  -- 份额 ×10000：23924 份 → 239240000
    open_date      TEXT,                     -- 买入时间 YYYY-MM-DD（只记到月的存当月 1 日）
    closed_at      TEXT,                     -- 清仓日期（closed 时必填）
    sell_cents     INTEGER,                  -- 卖出金额（分，清仓时按最新净值自动算出）
    profit_cents   INTEGER,                  -- 清仓最终收益（分，自动 = 卖出金额 − 本金）
    buy_reason     TEXT,                     -- 持仓记录·买入理由
    buy_judge      TEXT,                     -- 持仓记录·当时判断
    watch_points   TEXT,                     -- 持仓记录·观察重点
    remark         TEXT,                     -- 持仓记录·个人备注
    clear_reason   TEXT,                     -- 清仓复盘·为什么清仓
    clear_judge    TEXT,                     -- 清仓复盘·当时判断
    clear_good     TEXT,                     -- 清仓复盘·哪里做得好
    clear_review   TEXT,                     -- 清仓复盘·哪里值得复盘
    deleted        INTEGER NOT NULL DEFAULT 0, -- 软删除（1=已删，云同步墓碑预留）
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (fund_code) REFERENCES fund_meta(code) ON DELETE RESTRICT,
    -- 状态自洽：三种状态各有且只有自己的字段组合
    CHECK (
        (status='holding' AND cost_cents IS NOT NULL AND shares_scaled IS NOT NULL
             AND closed_at IS NULL AND sell_cents IS NULL AND profit_cents IS NULL)
     OR (status='watch'   AND cost_cents IS NULL AND shares_scaled IS NULL
             AND closed_at IS NULL AND sell_cents IS NULL AND profit_cents IS NULL)
     OR (status='closed'  AND cost_cents IS NOT NULL AND shares_scaled IS NOT NULL
             AND closed_at IS NOT NULL AND sell_cents IS NOT NULL AND profit_cents IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS ix_holdings_status ON holdings(status);
CREATE INDEX IF NOT EXISTS ix_holdings_fund   ON holdings(fund_code);

-- 4. 持仓变动流水（买入/卖出/修正自动记一笔；二期多次买入/定投升级的落点）
CREATE TABLE IF NOT EXISTS invest_events (
    id            INTEGER NOT NULL PRIMARY KEY,
    uuid          TEXT    NOT NULL UNIQUE,
    holding_id    INTEGER NOT NULL,          -- 属于哪笔持仓
    event_type    TEXT    NOT NULL CHECK (event_type IN ('buy','sell','adjust')),
    event_date    TEXT    NOT NULL,          -- 发生日期 YYYY-MM-DD
    amount_cents  INTEGER,                   -- 金额（分）：买入投入/卖出金额/修正差额（可空）
    shares_scaled INTEGER,                   -- 涉及份额 ×10000（sell/adjust 时）
    note          TEXT,                      -- 备注（可空）
    created_at    TEXT NOT NULL,
    FOREIGN KEY (holding_id) REFERENCES holdings(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_invest_events_holding ON invest_events(holding_id, event_date);

-- 5. 盯盘提醒规则表（有效期四模式，2026-09-03 用户拍板）
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER NOT NULL PRIMARY KEY,
    uuid         TEXT    NOT NULL UNIQUE,
    obj_type     TEXT    NOT NULL CHECK (obj_type IN ('fund','mkt')),
                   -- fund=场外基金（收盘后按净值检查）/ mkt=指数（盘中检查）
    fund_code    TEXT,                       -- obj_type='fund' 时的基金代码
    mkt_code     TEXT,                       -- obj_type='mkt' 时的行情代码
    cond_type    TEXT    NOT NULL CHECK (cond_type IN ('pct','line')),
                   -- pct=涨跌幅条件(%) / line=数值线（基金盯净值元、指数盯点位）
    direction    TEXT    NOT NULL CHECK (direction IN ('up','down')),
                   -- up=涨超/涨破 down=跌超/跌破（中文文案由界面拼出）
    cond_value   TEXT    NOT NULL,           -- 条件数值原文（用户输入字面值，如 '3' '0.95' '4100'）
                   -- 存 TEXT 理由：缩放因子随条件类型变化易错，原文 + Decimal 比较最简且精确；
                   -- 不参与 SQL 数值查询
    valid_type   TEXT    NOT NULL CHECK (valid_type IN ('once','until_trigger','ongoing','range')),
                   -- 有效期四模式：仅下一交易日/持续到触发/长期有效/指定日期范围
    start_date   TEXT,                       -- 生效开始日；NULL=立即（仅 until_trigger/ongoing）
    end_date     TEXT,                       -- 生效结束日；NULL=无截止（仅 until_trigger/ongoing）
    triggered_at TEXT,                       -- 仅 until_trigger 使用：首次触发事件成功写入后即失效；
                   -- 其余模式恒 NULL（CHECK 强制），ongoing 的"今日已触发"绝不落此字段
    note         TEXT,                       -- 用户备注（仅自己可见，软件不解读）
    enabled      INTEGER NOT NULL DEFAULT 1, -- 1=启用 0=暂停
    deleted      INTEGER NOT NULL DEFAULT 0, -- 软删除
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    CHECK (
        (obj_type='fund' AND fund_code IS NOT NULL AND mkt_code IS NULL)
     OR (obj_type='mkt'  AND mkt_code IS NOT NULL AND fund_code IS NULL)
    ),
    CHECK (valid_type <> 'once'  OR (start_date IS NOT NULL AND end_date IS NOT NULL AND start_date = end_date)),
    CHECK (valid_type <> 'range' OR (start_date IS NOT NULL AND end_date IS NOT NULL AND end_date >= start_date)),
    CHECK (valid_type IN ('once','range') OR end_date IS NULL),
    CHECK (valid_type = 'until_trigger' OR triggered_at IS NULL),  -- 关键防线
    FOREIGN KEY (fund_code) REFERENCES fund_meta(code)    ON DELETE RESTRICT,
    FOREIGN KEY (mkt_code)  REFERENCES market_meta(code)  ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_alerts_scan ON alerts(enabled, start_date, end_date);
CREATE INDEX IF NOT EXISTS ix_alerts_obj  ON alerts(obj_type, fund_code);

-- 6. 提醒触发历史（"当天不重复"依据 + 触发三态记录）
CREATE TABLE IF NOT EXISTS alert_events (
    id            INTEGER NOT NULL PRIMARY KEY,
    uuid          TEXT    NOT NULL UNIQUE,
    alert_id      INTEGER NOT NULL,          -- 属于哪条提醒
    trigger_date  TEXT    NOT NULL,          -- 触发归属日（同日不重复判断键）
    triggered_at  TEXT    NOT NULL,          -- 实际触发时刻
    cond_snapshot TEXT    NOT NULL,          -- 触发时条件快照文案（提醒事后被编辑也能回看当时的样子）
    actual_value  TEXT,                      -- 触发时实际数值原文（如 '1.5009' '4126.58' '-3.12'）
    trigger_kind  TEXT    NOT NULL DEFAULT 'realtime'
                  CHECK (trigger_kind IN ('realtime','recover')),
                  -- realtime=实时检查触发 / recover=关机睡眠恢复后的补偿检查
    notify_state  TEXT    NOT NULL DEFAULT 'skipped'
                  CHECK (notify_state IN ('sent','skipped')),
                  -- 旧兼容字段：sent=通知开启且已提交 / skipped=未提交；新逻辑不使用本字段做判断
    notify_result TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (notify_result IN ('pending','submitted','failed','skipped')),
                  -- 1B 通知结果：pending=待尝试 / submitted=已提交系统 / failed=发送失败 / skipped=开关关闭或通道跳过
    notify_error  TEXT,                      -- 通知失败/跳过原因（可空）
    acked         INTEGER NOT NULL DEFAULT 0,
                  -- 用户是否在软件内看到并确认本次触发（三态原则：软件内确认也算"提醒成功"）
    created_at    TEXT NOT NULL,
    UNIQUE (alert_id, trigger_date),         -- 同日不重复的最后一道防线
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_alert_events_date ON alert_events(trigger_date);

-- 9. 用户设置（key-value，键名对应原型 xltc-* 语义）
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,                -- 值统一字符串（布尔存 '1'/'0'）
    updated_at TEXT NOT NULL
);

-- ============ 三、程序自用 ============

-- 10. 程序元数据（schema 版本等，升级迁移依据）
CREATE TABLE IF NOT EXISTS app_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ============ 四、市场事件缓存（可重建，不属于用户数据） ============

-- 11. 新闻数据源状态（每个源一行）
CREATE TABLE IF NOT EXISTS news_sources (
    source_key      TEXT NOT NULL PRIMARY KEY,   -- sec_edgar / fed_rss
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'official',
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_attempt_at TEXT,                        -- 最近一次尝试时间
    last_success_at TEXT,                        -- 最近一次成功时间
    last_error      TEXT,                        -- 最近一次错误（成功时清空）
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- 12. 基金画像（由当前持仓 + fund_meta 生成；缓存，可重建）
CREATE TABLE IF NOT EXISTS fund_profiles (
    fund_code    TEXT NOT NULL PRIMARY KEY,
    market       TEXT NOT NULL,                  -- cn / us / hk
    asset_class  TEXT NOT NULL,                  -- equity / bond / mixed / qdii
    track_index  TEXT,                           -- 跟踪指数（指数基金）
    keywords     TEXT NOT NULL DEFAULT '[]',     -- JSON：代码/公司/行业关键词
    source_note  TEXT,                           -- 关键词来源说明（阶段一为人工清单）
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (fund_code) REFERENCES fund_meta(code) ON DELETE CASCADE
);

-- 13. 新闻条目（缓存，可重建；保留全部抓到的条目，未匹配的也留着用于统计）
CREATE TABLE IF NOT EXISTS news_items (
    id              INTEGER NOT NULL PRIMARY KEY,
    uuid            TEXT NOT NULL UNIQUE,
    source          TEXT NOT NULL,               -- sec_edgar / fed_rss
    source_id       TEXT NOT NULL,               -- 源内唯一 ID
    news_kind       TEXT NOT NULL DEFAULT 'realtime'
                    CHECK (news_kind IN ('realtime','report')), -- 实时7天/财报14天
    title           TEXT NOT NULL,
    summary         TEXT,
    url             TEXT,
    published_at    TEXT,                        -- 本地时间 'YYYY-MM-DD HH:MM:SS'
    published_ts    INTEGER,                     -- epoch 秒（排序/过滤）
    fetched_at      TEXT NOT NULL,
    content_hash    TEXT NOT NULL,               -- 去重指纹
    duplicate_count INTEGER NOT NULL DEFAULT 0,  -- 同事件重复报道数
    importance      REAL NOT NULL DEFAULT 0,     -- 源内重要度（0~1）
    raw_json        TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS ix_news_published ON news_items(published_ts DESC);
CREATE INDEX IF NOT EXISTS ix_news_hash ON news_items(content_hash);

-- 14. 新闻-基金关联（缓存；一条新闻对一只基金最多一条）
CREATE TABLE IF NOT EXISTS news_item_funds (
    id            INTEGER NOT NULL PRIMARY KEY,
    news_id       INTEGER NOT NULL,
    fund_code     TEXT NOT NULL,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('direct','indirect')),
    score         REAL NOT NULL DEFAULT 0,
    evidence      TEXT,                          -- JSON：匹配证据（公司/指数/行业/权重）
    created_at    TEXT NOT NULL,
    UNIQUE(news_id, fund_code),
    FOREIGN KEY (news_id) REFERENCES news_items(id) ON DELETE CASCADE,
    FOREIGN KEY (fund_code) REFERENCES fund_meta(code) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_news_funds_fund ON news_item_funds(fund_code);
"""

# 预置：行情花名册种子（代码格式以数据源适配层约定为准，此处为唯一标识）
MARKET_SEEDS = [
    ("sh000300", "沪深300",    "index", "cn", 1),
    ("sh000001", "上证指数",    "index", "cn", 2),
    ("sh000905", "中证500",    "index", "cn", 3),
    ("sz399006", "创业板指",    "index", "cn", 4),
    ("hkHSI",    "恒生指数",    "index", "hk", 5),
    ("hkHSTECH", "恒生科技",    "index", "hk", 6),
    ("usSPX",    "标普500",    "index", "us", 7),
    ("usNDX",    "纳斯达克100", "index", "us", 8),
    ("usDJI",    "道琼斯",      "index", "us", 9),
]


def connect():
    """打开数据库连接（每次调用新建，调用方负责 commit/close）"""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def now_str():
    """本地时间戳 'YYYY-MM-DD HH:MM:SS'"""
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    """本地日期 'YYYY-MM-DD'"""
    import datetime
    return datetime.date.today().isoformat()


def _ensure_news_kind_v5(conn):
    """v4→v5：给 news_items 增加分类字段，并只做保守回填，不删除新闻。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(news_items)")}
    if "news_kind" not in cols:
        conn.execute(
            "ALTER TABLE news_items ADD COLUMN news_kind TEXT NOT NULL DEFAULT 'realtime'"
        )
    # 旧 6-K 没有 docDescription 证据 → 保持 realtime；旧 10-K/10-Q/8-K 2.02 回填 report。
    for r in conn.execute("SELECT id, source, raw_json FROM news_items").fetchall():
        if news_rules.kind_from_raw(r["source"], r["raw_json"]) == "report":
            conn.execute(
                "UPDATE news_items SET news_kind = 'report' WHERE id = ?", (r["id"],)
            )


def _ensure_alert_events_v6(conn):
    """v5→v6：给 alert_events 增加通知结果字段和 triggered_at 索引。

    只做 ADD COLUMN + 回填，不重建表、不删除任何触发记录。
    旧 notify_state 映射：sent→submitted，其余→skipped；新事件默认 pending。
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(alert_events)")}
    added_result = False
    if "notify_result" not in cols:
        try:
            conn.execute(
                "ALTER TABLE alert_events ADD COLUMN notify_result TEXT NOT NULL "
                "DEFAULT 'pending' CHECK (notify_result IN ('pending','submitted','failed','skipped'))"
            )
        except sqlite3.OperationalError:
            # 个别 SQLite 版本对 ADD COLUMN + CHECK 兼容性不同；退化为普通列，写入值由代码校验。
            conn.execute(
                "ALTER TABLE alert_events ADD COLUMN notify_result TEXT NOT NULL DEFAULT 'pending'"
            )
        added_result = True
    if "notify_error" not in cols:
        conn.execute("ALTER TABLE alert_events ADD COLUMN notify_error TEXT")
    if added_result:
        conn.execute(
            "UPDATE alert_events SET notify_result = CASE "
            "WHEN notify_state = 'sent' THEN 'submitted' ELSE 'skipped' END "
            "WHERE notify_result = 'pending'"
        )
    # 索引必须在列已存在后创建；不能写进 SCHEMA_SQL（旧库 executescript 时还没有新列）。
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_alert_events_triggered_at "
        "ON alert_events(triggered_at)"
    )


def init_db():
    """建库建表 + 种子数据（幂等，可重复调用）"""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")  # 读写互不阻塞
        conn.executescript(SCHEMA_SQL)
        now = now_str()
        # 程序元数据种子（schema_version 升级：v3 → v4 新增 4 张市场事件缓存表；
        # 表结构由上方 executescript 的 IF NOT EXISTS 自动补齐，无需逐库迁移脚本）
        cur_ver = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
        ver = int(cur_ver["value"]) if cur_ver else 0
        if ver < 4:
            conn.execute(
                "INSERT INTO app_meta (key, value, updated_at) VALUES ('schema_version', '4', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = '4', updated_at = excluded.updated_at",
                (now,),
            )
        if ver < 5:
            # v4 → v5：新闻分类字段；只回填，不清理任何新闻。
            _ensure_news_kind_v5(conn)
            conn.execute(
                "INSERT INTO app_meta (key, value, updated_at) VALUES ('schema_version', '5', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = '5', updated_at = excluded.updated_at",
                (now,),
            )
        if ver < 6:
            # v5 → v6：提醒通知结果字段 + 首页 triggered_at 索引；不重建表、不删事件。
            _ensure_alert_events_v6(conn)
            conn.execute(
                "INSERT INTO app_meta (key, value, updated_at) VALUES ('schema_version', '6', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = '6', updated_at = excluded.updated_at",
                (now,),
            )
        conn.execute(
            "INSERT OR IGNORE INTO app_meta (key, value, updated_at) VALUES (?, ?, ?)",
            ("db_created_at", now, now),
        )
        # 行情花名册种子
        for code, name, mkt_type, market, order in MARKET_SEEDS:
            conn.execute(
                "INSERT OR IGNORE INTO market_meta "
                "(code, name, mkt_type, market, display_order, is_preset, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?)",
                (code, name, mkt_type, market, order, now, now),
            )
        # 市场事件数据源种子（阶段一：SEC EDGAR + 美联储 RSS）
        for key, name, kind in (("sec_edgar", "SEC EDGAR（美国证监会）", "official"),
                                ("fed_rss", "美联储新闻稿 RSS", "official")):
            conn.execute(
                "INSERT OR IGNORE INTO news_sources "
                "(source_key, name, kind, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (key, name, kind, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now_str()),
    )


def get_app_meta(conn, key, default=None):
    """程序元数据读取（app_meta 表，程序自用不上云）"""
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_app_meta(conn, key, value):
    conn.execute(
        "INSERT INTO app_meta (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now_str()),
    )
