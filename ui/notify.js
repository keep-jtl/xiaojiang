/* ==========================================================
 * 小财通 · 通知偏好（2026-09-26 用户重新明确的产品逻辑）
 *
 * 真实来源唯一：SQLite settings 表（Python 侧 api.get_notify_settings /
 * set_notify_settings / ask_notify_first）。本文件**不把任何 localStorage
 * 当作通知状态**：旧的原型键（xltc-notify-master / xltc-notify-asked /
 * xltc-notify-*）自本日起不再读写。
 *
 * 两条规则：
 * 1) 全软件只问一次：启动后若 notify_first_asked 还是空的，就由本文件弹出询问
 *    （无论当时打开的是哪一页——这是"应用首次使用"级别，不是某一页的首次进入）；
 *    用户选过之后写进 SQLite，此后任何页面、任何一次重启都不再自动询问。
 * 2) 两个开关相互独立：市场事件通知（notify_news_enabled）与盯盘提醒通知
 *    （notify_alerts_enabled）各自保存；各页面只改自己那一个键，
 *    只有"首次询问"那一次会把两者设成同一个值。
 *
 * 诚实口径（2026-09-26 用户要求）：真正的 Windows / macOS 系统桌面通知尚未实现，
 * 所以询问文案必须说清楚"现在只是保存你的偏好"，不能让用户以为点了开启
 * 就马上能收到系统通知。
 *
 * 用法（页面侧）：
 *   xltcNotify.onChange(fn)              设置读到或变化时回调（含首次读取）
 *   xltcNotify.flag('news_enabled', false)  读一个开关；从没设置过则用给的默认值
 *   xltcNotify.set({ news_follow: true })   只写传进去的键，其余一律不动
 * ========================================================== */
(function (window, document) {
  'use strict';

  /* ---------- 本文件自带弹层样式（不依赖各页面自己的 .modal 定义） ---------- */
  var STYLE_ID = 'xltcNotifyStyle';
  var CSS =
    '.ntf-mask{position:fixed;inset:0;background:rgba(15,15,20,.4);display:none;' +
      'align-items:center;justify-content:center;z-index:90;}' +
    '.ntf-mask.open{display:flex;}' +
    '.ntf-card{width:min(470px,92vw);background:var(--card,#fdfcf9);' +
      'border:1px solid rgba(0,0,0,.08);border-radius:14px;padding:20px 22px;' +
      'box-shadow:0 18px 50px rgba(0,0,0,.25);color:var(--ink,#2b2a26);' +
      'font-size:13.5px;line-height:1.75;}' +
    '.ntf-card h3{margin:0 0 10px;font-size:16px;}' +
    '.ntf-card p{margin:0 0 9px;color:var(--ink-2,#6b675e);}' +
    '.ntf-card p.ntf-note{color:var(--ink-3,#9c978c);font-size:12.5px;}' +
    '.ntf-foot{display:flex;justify-content:flex-end;gap:10px;margin-top:18px;}';

  var S = null;   /* 最近一次从 SQLite 读到的通知偏好（null = 还没读到） */
  var cbs = [];   /* 页面注册的回调 */

  function on (v) { return v === '1' || v === 1 || v === true; }

  function call (name) {
    var args = Array.prototype.slice.call(arguments, 1);
    var api = window.pywebview && window.pywebview.api;
    if (!api) return Promise.reject(new Error('本机数据服务尚未就绪'));
    return api[name].apply(null, args);
  }

  function fire () {
    cbs.forEach(function (f) {
      try { f(S); } catch (e) { /* 某个回调出错不影响其他回调 */ }
    });
  }

  var API = {
    on: on,

    /* 读一个开关：没设置过（null）就用页面给的默认值 —— Python 不替用户猜，默认值在界面层 */
    flag: function (key, dflt) {
      if (!S || S[key] === null || S[key] === undefined) return !!dflt;
      return on(S[key]);
    },

    state: function () { return S; },

    /* 设置读到或发生变化时回调（含首次读取）；同一页面可注册多个 */
    onChange: function (fn) {
      if (S) { try { fn(S); } catch (e) {} } else { cbs.push(fn); }
    },

    refresh: function () {
      return call('get_notify_settings').then(function (s) {
        S = s || {};
        fire();
        return S;
      });
    },

    /* 只写 patch 里出现的键 —— "一个页面改通知不会连带改另一个页面"靠这里 + Python 侧保证。
       先本地乐观更新（界面立刻响应），再用 Python 返回的全量状态校准（以库为准）。 */
    set: function (patch) {
      if (!S) S = {};
      Object.keys(patch).forEach(function (k) { S[k] = patch[k] ? '1' : '0'; });
      fire();
      return call('set_notify_settings', patch).then(function (s) {
        if (s) { S = s; fire(); }
        return S;
      });
    },

    /* 首次询问结果：唯一会把两个开关一起设成同一个值的地方 */
    askFirst: function (enabled) {
      return call('ask_notify_first', !!enabled).then(function (s) {
        if (s) { S = s; fire(); }
        return S;
      });
    }
  };
  window.xltcNotify = API;

  /* ---------- 首次询问弹层（全软件只出现一次） ---------- */
  function showAsk () {
    if (document.getElementById('xltcNotifyAsk')) return;
    if (!document.getElementById(STYLE_ID)) {
      var st = document.createElement('style');
      st.id = STYLE_ID;
      st.textContent = CSS;
      document.head.appendChild(st);
    }
    var m = document.createElement('div');
    m.className = 'ntf-mask';
    m.id = 'xltcNotifyAsk';
    m.innerHTML =
      '<div class="ntf-card">' +
        '<h3>🔔 消息提醒</h3>' +
        '<p>你想接收<b>市场事件</b>和<b>盯盘提醒</b>吗？开启后小财通会记住这个偏好，' +
          '以后可以在「市场事件」和「盯盘提醒」页面里分别单独修改。</p>' +
        '<p class="ntf-note">说明：真正的系统桌面通知能力还在开发中，现在选择只是保存你的偏好；' +
          '等通知功能接好之后，小财通才会按这里的设置提醒你。</p>' +
        '<div class="ntf-foot">' +
          '<button class="btn btn-ghost btn-sm" id="xltcNotifyNo">暂时不要</button>' +
          '<button class="btn btn-sm" id="xltcNotifyYes">开启</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(m);

    function finish (enabled) {
      var btns = m.querySelectorAll('button');
      btns.forEach(function (b) { b.disabled = true; });
      API.askFirst(enabled).then(function () {
        m.classList.remove('open');
        m.remove();
      }).catch(function () {
        /* 写库失败：不假装已保存，说清楚（下次启动会再问一次） */
        btns.forEach(function (b) { b.disabled = false; });
        if (!m.querySelector('.ntf-err')) {
          var p = document.createElement('p');
          p.className = 'ntf-note ntf-err';
          p.textContent = '保存失败：本机数据服务暂时不可用，这次选择没有记住，下次启动会再问一次。';
          m.querySelector('.ntf-card').appendChild(p);
        }
      });
    }
    document.getElementById('xltcNotifyYes').addEventListener('click', function () { finish(true); });
    document.getElementById('xltcNotifyNo').addEventListener('click', function () { finish(false); });

    /* 故意不做"点遮罩就关掉"：首次询问需要一个明确选择，
       否则用户每换一页都会再弹一次，反而更打扰（2026-09-26）。 */
    m.classList.add('open');
  }

  /* ---------- 启动：等 js_api 就绪 → 读 SQLite → 没问过才问 ---------- */
  var tries = 0;
  var timer = setInterval(function () {
    if (window.pywebview && window.pywebview.api) {
      clearInterval(timer);
      API.refresh().then(function (s) {
        if (!s || !on(s.first_asked)) showAsk();   /* 已问过 → 永不自动询问 */
      }).catch(function () { /* 读不到就静默，不打扰用户，下次启动再说 */ });
    } else if (++tries > 100) {
      clearInterval(timer);   /* 数据服务没就绪（例如直接用浏览器打开页面）：什么都不弹 */
    }
  }, 100);
}(window, document));
