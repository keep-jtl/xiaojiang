/* ==========================================================
   小财通 · 市场时段感知（原型演示）
   方案：分区固定 + 自动排序 —— 重点市场放大，其他市场保留
   原型用"时段模拟器"演示不同时间的首页变化；
   正式版按真实市场日历（节假日+夏令时）自动判断，无需手动选择。
   ========================================================== */

(function () {
  var btn = document.getElementById('sessionBtn');
  if (!btn) return;

  var SESSIONS = [
    { key: 'ashare',  label: 'A股交易中',   desc: '工作日 9:30-11:30 · 13:00-15:00' },
    { key: 'closed',  label: 'A股已收盘',   desc: '15:00 后 · 基金净值晚间更新' },
    { key: 'uspre',   label: '美股盘前',    desc: '美东 4:00-9:30（夏令时 16:00-21:30 北京时间）' },
    { key: 'usopen',  label: '美股交易中',  desc: '美东 9:30-16:00（夏令时 21:30-次日4:00 北京时间）' },
    { key: 'uspast',  label: '美股盘后',    desc: '美东 16:00-20:00（夏令时 4:00-8:00 北京时间）' },
    { key: 'weekend', label: '周末休市',    desc: '周六日 A股/港股/美股均休市' }
  ];

  /* 每个市场行在不同时段的状态（正式版由市场日历自动计算） */
  var STATUS = {
    ashare: { sh: ['live', '交易中'], sz: ['live', '交易中'], hk: ['live', '交易中'], us: ['', '未开盘'], fund: ['更新中', '今日净值 20:00 后更新，当前显示昨日净值'] },
    closed: { sh: ['', '已收盘'],     sz: ['', '已收盘'],     hk: ['', '已收盘'],     us: ['', '未开盘'], fund: ['done', '今日净值已更新 · 我的基金今日 +0.40%'] },
    uspre:  { sh: ['', '已收盘'],     sz: ['', '已收盘'],     hk: ['', '已收盘'],     us: ['warn', '盘前'], fund: ['done', '今日净值已更新'] },
    usopen: { sh: ['', '已收盘'],     sz: ['', '已收盘'],     hk: ['', '已收盘'],     us: ['live', '交易中'], fund: ['done', '今日净值已更新'] },
    uspast: { sh: ['', '已收盘'],     sz: ['', '已收盘'],     hk: ['', '已收盘'],     us: ['warn', '盘后'], fund: ['done', '今日净值已更新'] },
    weekend:{ sh: ['', '休市'],       sz: ['', '休市'],       hk: ['', '休市'],       us: ['', '休市'], fund: ['done', '今日为周末，净值以周五为准'] }
  };

  /* 每个时段放大的市场行（primary）：sh/sz → A股，us → 美股 */
  var PRIMARY = {
    ashare:  ['sh', 'sz'],
    closed:  ['sh', 'sz'],
    uspre:   ['us'],
    usopen:  ['us'],
    uspast:  ['us'],
    weekend: []
  };

  var heroText = {
    ashare:  'A股交易中 · 重点看这里',
    closed:  'A股已收盘 · 看收盘表现和基金净值',
    uspre:   '美股盘前 · 今晚重点',
    usopen:  '美股交易中 · 重点看这里',
    uspast:  '美股盘后 · 收尾阶段',
    weekend: '周末全球休市 · 看看周五收盘'
  };

  /* ---------- 构建时段模拟面板 ---------- */
  var panel = document.createElement('div');
  panel.className = 'appearance-panel sim-panel';

  var items = SESSIONS.map(function (s) {
    return '<button class="ap-mode sim-session" data-key="' + s.key + '" type="button">' + s.label +
      '<span class="ap-hint" style="display:block;margin-top:2px;">' + s.desc + '</span></button>';
  }).join('');

  panel.innerHTML =
    '<div class="ap-head">⏱ 模拟时段<span class="ap-head-note">原型演示 · 正式版自动判断</span></div>' +
    '<div class="ap-sec">选择要模拟的时段，查看首页变化</div>' +
    '<div class="ap-sim-list">' + items + '</div>';

  document.body.appendChild(panel);

  var sessionBtns = panel.querySelectorAll('.sim-session');

  /* ---------- 应用时段 ---------- */
  function currentKey () {
    return localStorage.getItem('xltc-session') || 'ashare';
  }

  function apply (key) {
    var st = STATUS[key];
    var rows = document.querySelectorAll('.mkt-row');
    rows.forEach(function (row) {
      var mkt = row.dataset.mkt;
      var p = PRIMARY[key].indexOf(mkt) !== -1;
      row.classList.toggle('primary', p);
      var stEl = row.querySelector('.mkt-status');
      if (stEl && st[mkt]) {
        stEl.className = 'mkt-status' + (st[mkt][0] ? ' ' + st[mkt][0] : '');
        stEl.textContent = st[mkt][1];
      }
    });
    var fund = document.querySelector('.fund-note');
    if (fund && st.fund) {
      var dot = fund.querySelector('.dot');
      var b = fund.querySelector('b');
      var p = fund.querySelector('p');
      dot.className = 'dot' + (st.fund[0] === 'done' ? ' done' : '');
      b.textContent = st.fund[0] === 'done' ? '净值已更新' : '净值更新中';
      p.textContent = st.fund[1];
    }
    var hero = document.getElementById('sessionHero');
    if (hero) hero.textContent = heroText[key];
    var label = document.getElementById('sessionLabel');
    if (label) label.textContent = '正在模拟：' + (SESSIONS.filter(function (s) { return s.key === key; })[0] || {}).label;
    localStorage.setItem('xltc-session', key);
    sessionBtns.forEach(function (b) { b.classList.toggle('active', b.dataset.key === key); });
    btn.textContent = '⏱ ' + (SESSIONS.filter(function (s) { return s.key === key; })[0] || {}).label;
  }

  /* ---------- 事件 ---------- */
  btn.addEventListener('click', function (e) {
    e.stopPropagation();
    panel.classList.toggle('open');
  });
  panel.addEventListener('click', function (e) {
    e.stopPropagation();
  });
  sessionBtns.forEach(function (b) {
    b.addEventListener('click', function () {
      apply(b.dataset.key);
      panel.classList.remove('open');
    });
  });

  apply(currentKey());
}());
