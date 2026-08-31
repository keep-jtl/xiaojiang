/* ==========================================================
   小财通 · 顶部导航统一管理（所有原型页面共用）
   2026-08-31 按已确认的最终导航结构重写：
   - 顶部固定 5 个核心入口：首页 / 我的基金 / 消息 / 盯盘提醒 / 组合体检
   - 其余功能收进「更多」下拉，内部分组：
       研究工具：发现 / 赛道 / 基金经理 / 全球市场（默认不推送、主动进入）
       其他：设置
   - 投资记忆暂不占顶部，入口在「我的基金 → 持仓详情/历史持仓」
   - 窄窗口（≤860px）：核心只保留 首页/我的基金/盯盘提醒，其余进「更多」
   - 当前页高亮按文件名自动判断，无需手工维护
   新增页面时：只改下方 CORE / MORE 里对应的 href，所有页面自动同步。
   ========================================================== */
(function () {
  var CORE = [
    { href: 'index.html', label: '首页' },
    { href: 'funds.html', label: '我的基金' },
    { href: 'news.html', label: '消息' },
    { href: 'alerts.html', label: '盯盘提醒' },
    { href: 'analysis.html', label: '组合体检' }
  ];
  var MORE = [
    { href: '#', label: '设置', group: '其他' },
    { href: '#', label: '发现', group: '研究工具' },
    { href: '#', label: '赛道', group: '研究工具' },
    { href: '#', label: '基金经理', group: '研究工具' },
    { href: '#', label: '全球市场', group: '研究工具' }
  ];
  var cur = location.pathname.split('/').pop();
  var nav = document.getElementById('mainNav');
  if (!nav) return;

  function a (n) {
    return '<a href="' + n.href + '"' + (n.href === cur ? ' class="active"' : '') + '>' + n.label + '</a>';
  }

  function render () {
    var core = CORE.slice();
    var more = MORE.slice();
    var collapsed = [];
    if (window.innerWidth <= 860) {
      collapsed = core.splice(2); /* 窄窗口只保留 首页/我的基金/盯盘提醒 */
      more = more.concat(collapsed);
    }
    var html = core.map(a).join('');
    if (more.length) {
      var curHidden = more.some(function (n) { return n.href === cur; });
      html += '<div class="nav-more" id="navMore">' +
        '<a href="#" class="nav-more-btn' + (curHidden ? ' active' : '') + '">更多 ▾</a>' +
        '<div class="nav-dropdown">' + groups(more, collapsed) + '</div>' +
      '</div>';
    }
    nav.innerHTML = html;
  }

  function groups (list, collapsed) {
    var parts = [];
    if (collapsed.length) parts.push(['核心', collapsed]);
    parts.push(['研究工具', list.filter(function (n) { return n.group === '研究工具'; })]);
    parts.push(['其他', list.filter(function (n) { return n.group === '其他'; })]);
    var html = '';
    parts.forEach(function (g) {
      if (!g[1].length) return;
      html += '<div class="nav-grp">' + g[0] + '</div>' + g[1].map(a).join('');
    });
    return html;
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
  window.addEventListener('resize', function () { render(); });
}());
