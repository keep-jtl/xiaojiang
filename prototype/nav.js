/* ==========================================================
   小财通 · 顶部导航统一管理（所有原型页面共用）
   新增页面时：只改下方 NAV 里对应的 href，所有页面自动同步。
   当前页高亮按文件名自动判断，无需手工维护。

   窄窗口自适应（2026-08-30）：
   - 宽窗口（>1160px）：全部 8 项平铺（已确认的原布局）
   - 中等窗口（861~1160px）：保留 首页/我的基金/盯盘提醒/消息，其余收进「更多」
   - 窄窗口（≤860px）：保留 首页/我的基金/盯盘提醒，其余收进「更多」
   - 当前页被收进「更多」时，「更多」按钮高亮提示
   ========================================================== */
(function () {
  var NAV = [
    { href: 'index.html',  label: '首页',     tier: 1 },
    { href: 'funds.html',  label: '我的基金', tier: 1 },
    { href: 'alerts.html', label: '盯盘提醒', tier: 1 },
    { href: 'news.html',   label: '消息',     tier: 2 },
    { href: '#',           label: '分析',     tier: 3 },
    { href: '#',           label: '发现',     tier: 3 },
    { href: '#',           label: '全球市场', tier: 3 },
    { href: '#',           label: '设置',     tier: 3 }
  ];
  var cur = location.pathname.split('/').pop();
  var nav = document.getElementById('mainNav');
  if (!nav) return;

  function maxTier () {
    var w = window.innerWidth;
    if (w <= 860) return 1;
    if (w <= 1160) return 2;
    return 3;
  }

  var lastTier = -1;

  function render () {
    var t = maxTier();
    var visible = NAV.filter(function (n) { return n.tier <= t; });
    var hidden = NAV.filter(function (n) { return n.tier > t; });
    var html = visible.map(function (n) {
      return '<a href="' + n.href + '"' + (n.href === cur ? ' class="active"' : '') + '>' + n.label + '</a>';
    }).join('');
    if (hidden.length) {
      var curHidden = hidden.some(function (n) { return n.href === cur; });
      html += '<div class="nav-more" id="navMore">' +
        '<a href="#" class="nav-more-btn' + (curHidden ? ' active' : '') + '">更多 ▾</a>' +
        '<div class="nav-dropdown">' + hidden.map(function (n) {
          return '<a href="' + n.href + '"' + (n.href === cur ? ' class="active"' : '') + '>' + n.label + '</a>';
        }).join('') + '</div>' +
      '</div>';
    }
    nav.innerHTML = html;
  }

  /* 「更多」下拉交互（事件委托，渲染重建后依然有效）：
     - 默认关闭，只有用户主动点击「更多」才展开，再点一次关闭
     - 点击菜单项后自动关闭；点击页面其他区域关闭；按 Esc 关闭
     - 不使用 hover 展开（桌面软件缩放/鼠标经过易误触） */
  nav.addEventListener('click', function (e) {
    var btn = e.target.closest('.nav-more-btn');
    if (btn) {
      e.preventDefault();
      btn.closest('.nav-more').classList.toggle('open');
      return;
    }
    if (e.target.closest('.nav-dropdown a')) {
      var openMenu = nav.querySelector('.nav-more.open');
      if (openMenu) openMenu.classList.remove('open');
    }
  });
  document.addEventListener('click', function (e) {
    var more = nav.querySelector('.nav-more.open');
    if (more && !more.contains(e.target)) more.classList.remove('open');
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      var more = nav.querySelector('.nav-more.open');
      if (more) more.classList.remove('open');
    }
  });

  render();
  window.addEventListener('resize', function () {
    var t = maxTier();
    if (t !== lastTier) { lastTier = t; render(); }
  });
}());
