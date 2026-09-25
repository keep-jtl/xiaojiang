/* ==========================================================
 * 首页「市场速览」数据驱动脚本（2026-09-05 改造）
 *
 * 真实数据来源（全部本机 Python 侧计算，界面只做展示）：
 *   get_market_sessions()   三市场交易状态（交易日历统一服务）
 *   get_market_quotes(码)   8 只真实指数行情（腾讯主源→新浪备用）
 *   get_holdings()          资产总览（真实持仓，SQLite）
 *   get_today_alerts()      今日提醒（真实触发记录；无则空态）
 *   maybe_auto_refresh(码)  打开首页时检查基金净值是否需更新（当天只联网一次）
 *
 * 设计口径（用户已确认）：
 *   - 所有指数必须是真实行情；某指数暂不可用 → 显示"暂不可用"
 *   - 交易中的市场排在最前（时段感知自动排序），其余按固定顺序
 *   - 状态徽标：交易中=品牌色、未开盘=橙色提醒、已收盘/休市=中性灰
 *   - 行情时间/获取时间/来源可悬停查看（title）
 *   - 行情与净值的**定时刷新统一由 Python 后台刷新器（app/refresher.py）负责**：
 *     本页不再自己开定时器，只在收到完成事件后重取一次（2026-09-25 用户确认）
 *   - 打开首页时自动检查基金净值（与我基金页同一套后端逻辑）；
 *     不新增界面元素：成功则卡片数字自然变新，
 *     失败/无网络静默保留旧数据，不弹提示
 * ========================================================== */
(function () {
  'use strict';

  var MARKET_NAMES = { cn: 'A股', hk: '港股', us: '美股' };
  var MARKET_TIPS = {
    cn: 'A股 · 09:30–11:30 / 13:00–15:00',
    hk: '港股 · 09:30–12:00 / 13:00–16:10',
    us: '美股 · 北京时间 21:30 / 22:30 开盘（按夏令时自动）'
  };
  var STATUS_BADGE = {   // 状态 → 徽标样式类
    trading: 'live', preopen: 'warn', noon_break: 'warn',
    closed: '', holiday: '', no_data: ''
  };

  /* ---------- 调用 Python 接口（与我的基金页同一套） ---------- */
  function call (name) {
    var args = Array.prototype.slice.call(arguments, 1);
    var api = window.pywebview && window.pywebview.api;
    if (!api) return Promise.reject(new Error('本机数据服务尚未就绪，请稍等几秒再试。'));
    return api[name].apply(null, args);
  }

  /* ---------- 显示工具 ---------- */
  function money (c) {            // 无符号金额（分→'35,907.53'）
    if (c === null || c === undefined) return '—';
    var neg = c < 0;
    var a = Math.abs(c);
    var y = Math.floor(a / 100);
    var f = ('0' + (a % 100)).slice(-2);
    return (neg ? '-' : '') + y.toLocaleString('zh-CN') + '.' + f;
  }
  function moneySign (c) {        // 带符号：+23,456.70 / -7,092.47
    if (c === null || c === undefined) return '—';
    return (c >= 0 ? '+' : '-') + money(Math.abs(c));
  }
  function isPlus (s) {           // 兼容数字与字符串（纪律同 funds.html）
    if (typeof s === 'number') return s >= 0;
    return !s || s === '' || s.charAt(0) !== '-';
  }
  function upDown (s) { return isPlus(s) ? 'up' : 'down'; }
  function esc (s) { return String(s === null || s === undefined ? '' : s); }

  var state = { sessions: null, quotes: [], holdingCodes: [] };

  /* 首页 8 只真实指数（用户 2026-09-05 确认清单；恒生科技留作盯盘提醒预设，不进首页） */
  var HOME_CODES = ['sh000300', 'sh000001', 'sh000905', 'sz399006',
                    'hkHSI', 'usSPX', 'usNDX', 'usDJI'];

  /* ---------- 顶部问候（按时段变化；与数据库无关，纯本地时间） ---------- */
  (function greeting () {
    var h = new Date().getHours();
    var el = document.getElementById('greeting');
    if (h >= 5 && h < 12) el.textContent = '早上好 👋';
    else if (h >= 12 && h < 18) el.textContent = '下午好 👋';
    else el.textContent = '晚上好 👋';
  })();

  /* ---------- 渲染：三市场状态条 ---------- */
  function marketOf (q) {         // 指数行 → 所属市场
    if (q.market === 'cn' || q.market === 'hk' || q.market === 'us') return q.market;
    return String(q.code).startsWith('hk') ? 'hk'
      : String(q.code).startsWith('us') ? 'us' : 'cn';
  }

  function renderSessions () {
    var s = state.sessions;
    if (!s) return;
    var box = document.getElementById('mktSessions');
    box.innerHTML = s.order.map(function (m) {
      var x = s.markets[m];
      var cls = STATUS_BADGE[x.state] || '';
      return '<span class="mkt-session-chip ' + cls + '" title="' + MARKET_TIPS[m] + '">' +
        MARKET_NAMES[m] + ' · ' + x.label + '</span>';
    }).join('');
    var hero = document.getElementById('sessionHero');
    if (s.summary) hero.textContent = s.summary; else hero.textContent = '—';
    // 警告（如某年官方日历未公布）暂存，由脚注统一展示（不打断页面）
    state.warningMsg = (s.warnings && s.warnings.length) ? s.warnings[0] : '';
  }

  /* ---------- 渲染：8 只指数（按会话顺序 + 重点放大） ---------- */
  function tipFor (q) {
    if (!q.available) return '暂无可用行情：网络连接后自动更新';
    return '行情时间 ' + (q.quote_time || '—') + ' · 本机获取于 ' +
      (q.fetched_at || '—') + ' · 来源 ' + q.source_label;
  }

  function rowHTML (q, primary) {
    var val, pct;
    if (q.available) {
      val = '<b class="num ' + upDown(q.pct_str) + '">' + esc(q.price_str) + '</b>' +
        '<span class="num ' + upDown(q.pct_str) + '">' + esc(q.pct_str) + '%</span>';
    } else {
      val = '<b class="num">—</b><span>暂不可用</span>';
    }
    return '<div class="mkt-row' + (primary ? ' primary' : q.available ? '' : ' unavailable') + '">' +
      '<div class="mkt-name">' + esc(q.name) + '<small>' +
      (q.available ? '昨收 ' + esc(q.prev_str || '—') : '本机暂无行情记录') + '</small></div>' +
      '<div class="mkt-val">' + val + '</div>' +
      '<span class="mkt-status" title="' + tipFor(q) + '"></span>' +
      '</div>';
  }

  function renderQuotes () {
    var s = state.sessions;
    var qs = state.quotes;
    if (!qs.length) return;
    var order = s && s.order.length ? s.order : ['cn', 'hk', 'us'];
    // 分组：cn/hk/us → 组内保持原顺序（market_meta.display_order）
    var groups = { cn: [], hk: [], us: [] };
    qs.forEach(function (q) { var m = marketOf(q); if (groups[m]) groups[m].push(q); });
    var flat = [];
    order.forEach(function (m) { flat = flat.concat(groups[m] || []); });
    // 重点市场（排最前的市场）前 2 只放大显示
    var leadM = order[0];
    var leadSeen = 0;
    var html = flat.map(function (q) {
      var primary = q.market === leadM && leadSeen < 2;
      if (q.market === leadM) leadSeen++;
      return rowHTML(q, primary);
    }).join('');
    var list = document.getElementById('mktList');
    list.innerHTML = html;
    // 每行行尾补市场状态徽标（交易中=品牌色/未开盘=橙/休市收盘=中性）
    var mkts = s.markets;
    var rows = list.querySelectorAll('.mkt-row');
    for (var i = 0; i < rows.length; i++) {
      var st = mkts[marketOf(flat[i])];
      var tag = rows[i].querySelector('.mkt-status');
      if (st) {
        tag.textContent = st.label;
        var cls = STATUS_BADGE[st.state] || '';
        if (cls) tag.classList.add(cls);
        if (st.note) tag.title = (tag.title || '') + ' · ' + st.note;
      } else {
        tag.textContent = '—';
      }
    }
  }

  /* ---------- 渲染：底部数据时间（行情时刻 + 本机获取时间） ---------- */
  function renderFoot () {
    var s = state.sessions;
    var qs = state.quotes.filter(function (q) { return q.available; });
    if (!qs.length || !s) { document.getElementById('mktFoot').hidden = true; return; }
    var latest = qs.reduce(function (a, b) {
      return (!a || (b.quote_time && b.quote_time > a.quote_time)) ? b : a;
    }, null);
    var el = document.getElementById('mktFoot');
    el.hidden = false;
    document.getElementById('mktFootB').textContent =
      '最新行情 ' + (latest.quote_time || '—') + '（北京时间）';
    document.getElementById('mktFootP').textContent =
      '本机数据获取于 ' + (latest.fetched_at || '—') + ' · 休市时显示最近一次收盘数据 · 数据仅供参考' +
      (state.warningMsg ? ' · ' + state.warningMsg : '');
  }

  /* ---------- 渲染：资产总览（真实持仓汇总）
     视觉与已验收首页一致：资产卡收益用普通字色（asset-hero 不红绿），
     红涨绿跌只用于市场行情行。金额全在 Python 侧算好，界面只显示。 ---------- */
  function renderHero (summary, holdingCount, lastFetchAt) {
    var none = summary.total_cents === null || summary.total_cents === undefined;
    document.getElementById('heroTotal').textContent = none ? '—' : '¥ ' + money(summary.total_cents);
    var today = document.getElementById('heroToday');
    if (summary.today_profit_cents === null || summary.today_profit_cents === undefined) {
      today.textContent = '—';
    } else {
      today.textContent = moneySign(summary.today_profit_cents) +
        (summary.today_pct_str ? '（' + summary.today_pct_str + '%）' : '');
    }
    document.getElementById('heroProfit').textContent =
      summary.profit_cents === null || summary.profit_cents === undefined
        ? '—' : moneySign(summary.profit_cents);
    document.getElementById('heroRate').textContent =
      summary.profit_pct_str ? summary.profit_pct_str + '%' : '—';
    document.getElementById('heroCount').textContent = holdingCount + ' 只';
    // 底部提示行：无持仓 → 引导添加；有持仓 → 显示最近一次真实净值获取时间
    var foot = document.getElementById('heroFoot');
    if (!holdingCount) {
      foot.innerHTML = '还没有持仓：<a href="funds.html">去添加你持有的基金</a>';
      return;
    }
    foot.textContent = lastFetchAt ? '净值数据更新于 ' + lastFetchAt : '';
  }

  /* ---------- 渲染：今日提醒（真实空态） ---------- */
  function renderAlerts (d) {
    var box = document.getElementById('alertList');
    if (!d.today.length) {
      box.innerHTML =
        '<div class="alert-empty"><b>' +
        (d.enabled_count ? '今天没有触发的提醒' : '还没有设过提醒') + '</b>' +
        '<p>达到你设置的条件才会提醒你，平时保持安静' +
        (d.enabled_count ? '。' : '：去「盯盘提醒」设一个试试。') + '</p></div>';
      return;
    }
    box.innerHTML = d.today.map(function (a) {
      var text = a.obj_name ? esc(a.obj_name) + ' ' : '';
      text += a.note ? esc(a.note) : '达到了你设置的条件';
      return '<div class="alert-item"><span class="alert-dot orange"></span>' +
        '<div class="t"><b>' + text + '</b>' +
        '<small class="time">触发于 ' + esc(a.triggered_at || '') + '</small></div></div>';
    }).join('');
  }

  /* ---------- 刷新行情（按钮 / 自动）
     后端在后台线程抓数，完成后发 marketQuotesUpdated 事件 → __xltcOnData → load()。
     这里兜底等待：约 20 秒仍未收到完成事件（如事件桥异常）则强制重取一次。 ---------- */
  var refreshing = false;
  var evtCount = 0;   // 收到的 marketQuotesUpdated 事件数
  function refreshQuotes (quiet) {
    if (refreshing) return Promise.resolve(false);
    refreshing = true;
    var btn = document.getElementById('mktRefreshBtn');
    if (btn) btn.classList.add('busy');
    var c0 = evtCount;
    return call('refresh_market_quotes').then(function () {
      return new Promise(function (resolve) {
        var waited = 0;
        var poll = setInterval(function () {
          waited += 1000;
          if (evtCount > c0 || waited >= 20000) {  // 事件已到 或 超时兜底
            clearInterval(poll);
            resolve(true);
          }
        }, 1000);
      });
    }).catch(function () {
      return false;
    }).then(function (ok) {
      refreshing = false;
      if (btn) btn.classList.remove('busy');
      if (!ok) { if (!quiet) noteErr(); return false; }
      if (evtCount === c0) load().catch(function () {});  // 事件未到（超时兜底）才主动取数
      return true;
    });
  }
  function noteErr () {
    var w = document.getElementById('mktFoot');
    w.hidden = false;
    document.getElementById('mktFootB').textContent = '行情刷新失败';
    document.getElementById('mktFootP').textContent = '网络可能不可用：本机仍保留最近一次成功获取的行情，可稍后重试。';
  }

  /* ---------- 取数 ---------- */
  function load () {
    return Promise.all([
      call('get_market_sessions'),
      call('get_market_quotes', HOME_CODES),
      call('get_holdings'),
      call('get_today_alerts')
    ]).then(function (rs) {
      state.sessions = rs[0];
      state.quotes = rs[1].items;
      state.holdingCodes = (rs[2].holdings || []).map(function (h) { return h.fund_code; });
      renderSessions();
      renderQuotes();
      renderFoot();
      renderHero(rs[2].summary || {}, (rs[2].holdings || []).length, rs[2].last_fetch_at);
      renderAlerts(rs[3]);
    });
  }

  /* ---------- 打开首页时的净值自动检查（2026-09-24 新增）
     与我基金页同一套后端逻辑：后端用 app_meta 全库共享"今天已自动更新过"
     标记，当天只联网一次，不会重复请求；单只基金失败不影响其他基金，
     失败时库里旧净值原样保留（后端已保证），失败静默不打扰用户。
     只在页面刚打开时检查一次，防止事件重取时来回触发。 ---------- */
  var autoChecked = false;
  function autoRefreshNavIfNeeded () {
    if (autoChecked) return;
    autoChecked = true;
    if (!state.holdingCodes.length) return;   // 没有持仓：无需联网
    call('maybe_auto_refresh', state.holdingCodes).catch(function () {});
  }

  /* ---------- 事件：数据更新（Python 完成刷新后通知） ----------
     事件到达 → 重取一次；refreshQuotes 的 then 只在超时兜底时补取，
     两者互补，不会双跑（事件驱动 = 主路径，超时 = 保险丝）。 */
  window.__xltcOnData = function (d) {
    if (!d) return;
    if (d.type === 'marketQuotesUpdated') {
      evtCount++;
      load().catch(function () {});
    } else if (d.type === 'dataUpdated') {
      // 基金净值更新完成（打开首页自动触发）：重取一次，资产卡数字同步变新。
      // 失败（d.ok === false）也重取：拿到的仍是库中保留的旧净值，显示不变。
      load().catch(function () {});
    }
  };

  /* ---------- 事件：刷新按钮 ----------
     页面上不再开定时器：行情的定时刷新由 Python 后台刷新器统一负责
     （交易时段内每 10 分钟一次），完成后发 marketQuotesUpdated 事件 → 本页重取。
     这里只保留"用户手动点刷新"这一条路径。 ---------- */
  document.getElementById('mktRefreshBtn').addEventListener('click', function () {
    refreshQuotes(false);
  });

  /* ---------- 启动：等 pywebview 的 js_api 就绪（与我的基金页同一套） ---------- */
  var readyTries = 0;
  var readyTimer = setInterval(function () {
    if (window.pywebview && window.pywebview.api) {
      clearInterval(readyTimer);
      load().then(function () {
        autoRefreshNavIfNeeded();   // 先出画面，再检查净值是否需要更新（不阻塞首屏）
      }).catch(function () {
        var w = document.getElementById('mktFoot');
        w.hidden = false;
        document.getElementById('mktFootB').textContent = '读取数据失败';
        document.getElementById('mktFootP').textContent = '本机数据库可能尚未初始化，请重启小财通后重试。';
      });
    } else if (++readyTries > 100) {
      clearInterval(readyTimer);
      var list = document.getElementById('mktList');
      if (list) list.innerHTML =
        '<div class="alert-empty"><b>无法连接本机数据服务</b>' +
        '<p>请通过「启动小财通」运行本软件；若已在运行仍出现此提示，请关闭软件后重新打开。</p></div>';
    }
  }, 100);
}());
