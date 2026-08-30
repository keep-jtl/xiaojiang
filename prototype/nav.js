/* ==========================================================
   小财通 · 顶部导航统一管理（所有原型页面共用）
   新增页面时：只改下方 NAV 里对应的 href，所有页面自动同步。
   当前页高亮按文件名自动判断，无需手工维护。
   ========================================================== */
(function () {
  var NAV = [
    { href: 'index.html',  label: '首页' },
    { href: 'funds.html',  label: '我的基金' },
    { href: 'alerts.html', label: '盯盘提醒' },
    { href: '#',           label: '分析' },
    { href: '#',           label: '消息' },
    { href: '#',           label: '发现' },
    { href: '#',           label: '全球市场' },
    { href: '#',           label: '设置' }
  ];
  var cur = location.pathname.split('/').pop();
  var nav = document.getElementById('mainNav');
  if (!nav) return;
  nav.innerHTML = NAV.map(function (n) {
    return '<a href="' + n.href + '"' + (n.href === cur ? ' class="active"' : '') + '>' + n.label + '</a>';
  }).join('');
}());
