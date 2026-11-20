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
 * 当前口径（2026-11-17）：Windows 使用系统 Toast，并额外自绘 TopMost 桌面横幅
 * （系统免打扰时仍可显示）；桌面端市场事件/盯盘提醒后台自动检查已接通。
 * Android 已接 Capacitor 本地通知，但只在应用前台时按 60 秒检查，离开应用不保证持续盯盘。
 * 询问文案必须说清楚能力边界，不能让用户以为点了开启就能后台常驻。
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

    /* 旧版首次偏好询问（保留兼容；新引导走 askSystem） */
    askFirst: function (enabled) {
      return call('ask_notify_first', !!enabled).then(function (s) {
        if (s) { S = s; fire(); }
        return S;
      });
    },

    /* 首次系统通知引导：记录已询问；选开启时同时打开两类通知偏好 */
    askSystem: function (enabled) {
      return call('ask_notify_system', !!enabled).then(function (s) {
        if (s) { S = s; fire(); }
        return S;
      });
    },

    /* 真实系统通知测试（不是页面内模拟提示） */
    sendTest: function (kind) {
      return call('send_test_notification', kind || 'generic');
    },

    /* 真实通知能力（是否 Windows、通知总开关是否开启） */
    capabilities: function () {
      return call('get_notify_capabilities');
    },

    /* 打开 Windows 系统通知设置 */
    openSettings: function () {
      return call('open_notification_settings');
    }
  };
  window.xltcNotify = API;

  /* ---------- 首次系统通知引导（全软件只出现一次） ---------- */
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
        '<h3>🔔 开启系统通知</h3>' +
        '<p>小财通可以使用 <b>Windows 系统通知</b>提醒你。现在开启会立即发送一条' +
          '<b>真实测试通知</b>，你可以直接确认能不能收到。</p>' +
        '<p class="ntf-note">说明：Windows 下会同时使用系统 Toast 与应用自绘桌面横幅；' +
          '市场事件和盯盘提醒的<b>后台自动检查已接通</b>。Android 版已接本地通知，' +
          '但应用离开前台后不保证持续盯盘；以后可在「市场事件」、' +
          '「盯盘提醒」和「设置」里分别修改。</p>' +
        '<p class="ntf-note" id="xltcNotifyStatus"></p>' +
        '<div class="ntf-foot">' +
          '<button class="btn btn-ghost btn-sm" id="xltcNotifyNo">暂时不要</button>' +
          '<button class="btn btn-sm" id="xltcNotifyYes">开启并测试</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(m);

    var status = m.querySelector('#xltcNotifyStatus');

    /* 系统通知设置入口只保留一个：初始化、每次重试都复用同一个按钮，
       不再每点一次“再试一次”就追加一个新按钮（2026-11-17 人工验收修复）。 */
    function ensureOpenSettingsButton () {
      var foot = m.querySelector('.ntf-foot');
      var b = m.querySelector('#xltcNotifyOpen');
      if (!b) {
        b = document.createElement('button');
        b.className = 'btn btn-ghost btn-sm';
        b.id = 'xltcNotifyOpen';
        b.textContent = '打开系统通知设置';
        b.addEventListener('click', function () { API.openSettings(); });
        foot.insertBefore(b, m.querySelector('#xltcNotifyYes'));
      }
      return b;
    }

    /* 如果 Windows 通知总开关是关的，提前说清楚，并给出真实可点的设置入口 */
    API.capabilities().then(function (cap) {
      if (cap && cap.toast_enabled === false) {
        status.textContent = '应用内提醒偏好可以保存，但 Windows 系统通知总开关当前是关闭的，实际收不到 Toast。请先打开系统通知设置。';
        status.style.color = 'var(--tag-orange-ink, #b54708)';
        ensureOpenSettingsButton();
      }
    }).catch(function () { /* 能力读取失败不阻断引导 */ });

    function close () {
      m.classList.remove('open');
      m.remove();
    }

    function fail (text) {
      m.querySelectorAll('button').forEach(function (b) { b.disabled = false; });
      status.textContent = text;
      status.style.color = 'var(--tag-red-ink, #b42318)';
      if (!m.querySelector('#xltcNotifyClose')) {
        var b = document.createElement('button');
        b.className = 'btn btn-ghost btn-sm';
        b.id = 'xltcNotifyClose';
        b.textContent = '关闭';
        b.addEventListener('click', close);
        m.querySelector('.ntf-foot').appendChild(b);
      }
    }

    function finish (enabled) {
      m.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
      if (!enabled) {
        API.askSystem(false).then(close).catch(function () {
          fail('保存失败：本机数据服务暂时不可用，这次选择没有记住，下次启动会再问一次。');
        });
        return;
      }
      status.textContent = '正在保存偏好并发送真实测试通知…';
      status.style.color = '';
      API.askSystem(true).then(function () {
        return API.sendTest('generic');
      }).then(function (r) {
        if (r && r.ok && r.toast_enabled === false) {
          status.textContent = '应用内偏好已保存，但 Windows 系统通知总开关当前仍是关闭的，所以看不到 Toast。请打开系统通知设置后再试；这个入口只会保留一个。';
          status.style.color = 'var(--tag-orange-ink, #b54708)';
          m.querySelectorAll('button').forEach(function (b) { b.disabled = false; });
          m.querySelector('#xltcNotifyYes').textContent = '再试一次';
          ensureOpenSettingsButton();
        } else if (r && r.ok) {
          status.textContent = r.banner
            ? '已发送 Windows 通知；桌面横幅已弹出（免打扰下也可见）。'
            : '已提交到 Windows 通知中心；桌面横幅未能拉起，请检查系统通知设置。';
          status.style.color = '';
          m.querySelector('#xltcNotifyYes').textContent = '已发送';
          setTimeout(close, 2200);
        } else {
          fail('通知发送失败：' + ((r && r.error) || '未知原因') +
               '。偏好已保存，可稍后在「设置 → 通知」重试。');
        }
      }).catch(function (err) {
        fail('通知发送失败：' + (err && err.message ? err.message : '本机通知服务不可用') +
             '。偏好已保存，可稍后在「设置 → 通知」重试。');
      });
    }
    document.getElementById('xltcNotifyYes').addEventListener('click', function () { finish(true); });
    document.getElementById('xltcNotifyNo').addEventListener('click', function () { finish(false); });

    /* 故意不做"点遮罩就关掉"：首次引导需要一个明确选择。 */
    m.classList.add('open');
  }

  /* ---------- 启动：等 js_api 就绪 → 读 SQLite → 没问过才问 ---------- */
  var tries = 0;
  var timer = setInterval(function () {
    if (window.pywebview && window.pywebview.api) {
      clearInterval(timer);
      API.refresh().then(function (s) {
        /* 新键 system_asked：老用户也会看到一次“系统通知引导”，之后永不再自动弹 */
        if (!s || !on(s.system_asked)) showAsk();
      }).catch(function () { /* 读不到就静默，不打扰用户，下次启动再说 */ });
    } else if (++tries > 100) {
      clearInterval(timer);   /* 数据服务没就绪（例如直接用浏览器打开页面）：什么都不弹 */
    }
  }, 100);
}(window, document));
