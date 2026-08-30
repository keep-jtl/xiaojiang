/* ==========================================================
   小财通 · 外观预览面板（原型演示用）
   - 外观模式：浅色 / 深色（跟随系统/日出日落/自定义时间 → 正式版「设置 → 外观」）
   - 主题颜色：6 种预设强调色，点选立即预览
   选择结果用 localStorage 记住（仅原型演示用）
   ========================================================== */

(function () {
  var ACCENTS = [
    { key: 'blue',     name: '蓝色', color: '#2f6fed' },
    { key: 'mistblue', name: '雾蓝', color: '#5a8fd6' },
    { key: 'cyan',     name: '青色', color: '#14a3b0' },
    { key: 'purple',   name: '紫色', color: '#7a5cf0' },
    { key: 'orange',   name: '橙色', color: '#f07f2a' },
    { key: 'slate',    name: '灰蓝', color: '#5d7a9e' }
  ];

  var btn = document.getElementById('appearanceBtn');
  if (!btn) return;

  /* ---------- 构建面板 ---------- */
  var panel = document.createElement('div');
  panel.className = 'appearance-panel';

  var modes = ['light', 'dark'].map(function (m) {
    return '<button class="ap-mode" data-mode="' + m + '" type="button">' +
      (m === 'light' ? '☀️ 浅色' : '🌙 深色') + '</button>';
  }).join('');

  var swatches = ACCENTS.map(function (a) {
    return '<button class="ap-swatch" data-accent="' + a.key +
      '" style="background:' + a.color + '" title="' + a.name + '" type="button"></button>';
  }).join('');

  panel.innerHTML =
    '<div class="ap-head">外观预览<span class="ap-head-note">正式版在「设置 → 外观」</span></div>' +
    '<div class="ap-sec">外观模式</div>' +
    '<div class="ap-modes">' + modes + '</div>' +
    '<div class="ap-hint">跟随系统 / 跟随日出日落 / 自定义时间 → 正式版设置中提供</div>' +
    '<div class="ap-sec">主题颜色 <span class="ap-head-note">用于按钮、导航、链接、重点卡片</span></div>' +
    '<div class="ap-swatches">' + swatches + '</div>' +
    '<div class="ap-hint">涨跌颜色不受主题色影响：红=上涨，绿=下跌，橙黄=风险提醒</div>';

  document.body.appendChild(panel);

  var modeBtns = panel.querySelectorAll('.ap-mode');
  var swatchBtns = panel.querySelectorAll('.ap-swatch');

  /* ---------- 读取 / 应用 ---------- */
  function current () {
    return {
      theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light',
      accent: document.documentElement.dataset.accent || 'blue'
    };
  }

  function render () {
    var c = current();
    btn.textContent = (c.theme === 'dark' ? '🌙 深色' : '☀️ 浅色') + ' · 🎨 ' +
      (ACCENTS.filter(function (a) { return a.key === c.accent; })[0] || ACCENTS[0]).name;
    modeBtns.forEach(function (b) {
      b.classList.toggle('active', b.dataset.mode === c.theme);
    });
    swatchBtns.forEach(function (b) {
      b.classList.toggle('active', b.dataset.accent === c.accent);
    });
  }

  function setTheme (t) {
    document.documentElement.dataset.theme = t;
    localStorage.setItem('xltc-theme', t);
    render();
  }
  function setAccent (a) {
    document.documentElement.dataset.accent = a;
    localStorage.setItem('xltc-accent', a);
    render();
  }

  /* ---------- 事件 ---------- */
  btn.addEventListener('click', function (e) {
    e.stopPropagation();
    panel.classList.toggle('open');
  });
  document.addEventListener('click', function () {
    panel.classList.remove('open');
  });
  panel.addEventListener('click', function (e) {
    e.stopPropagation();
  });

  modeBtns.forEach(function (b) {
    b.addEventListener('click', function () { setTheme(b.dataset.mode); });
  });
  swatchBtns.forEach(function (b) {
    b.addEventListener('click', function () { setAccent(b.dataset.accent); });
  });

  render();
}());
