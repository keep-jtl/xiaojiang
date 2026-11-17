/* ==========================================================
   小财通 · 通知效果演示（原型演示）
   高仿 Windows 系统通知样式（右下角卡片式 toast）。
   正式版由操作系统原生通知显示，样式由系统决定（Windows /
   Mac 各不相同），本演示仅供查看通知"长什么样、装什么内容"。
   用法：window.showToast({ title, body, time, onClick })
   ========================================================== */
(function (window) {
  var stack = document.getElementById('toastStack');
  if (!stack) {
    stack = document.createElement('div');
    stack.className = 'toast-stack';
    stack.id = 'toastStack';
    document.body.appendChild(stack);
  }

  window.showToast = function (opts) {
    var t = document.createElement('div');
    t.className = 'toast';
    t.innerHTML =
      '<div class="toast-icon">财</div>' +
      '<div class="toast-main">' +
        '<div class="toast-app">小财通</div>' +
        '<div class="toast-title">' + opts.title + '</div>' +
        (opts.body ? '<div class="toast-body">' + opts.body + '</div>' : '') +
        '<div class="toast-time">' + (opts.time || '刚刚') + '</div>' +
      '</div>' +
      '<button class="toast-close" title="关闭">✕</button>';
    stack.appendChild(t);
    while (stack.children.length > 3) stack.removeChild(stack.firstChild);

    function close () { t.remove(); }
    t.querySelector('.toast-close').addEventListener('click', function (e) { e.stopPropagation(); close(); });
    t.addEventListener('click', function () {
      close();
      if (opts.onClick) opts.onClick(); /* 正式版：点击跳转到对应基金/提醒/消息详情 */
    });
    setTimeout(close, 8000); /* 8 秒后自动消失 */
  };
})(window);
