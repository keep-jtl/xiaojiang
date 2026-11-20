# -*- coding: utf-8 -*-
"""小财通 MVP —— 程序入口

启动方式（项目根目录，注意必须用 -m 以支持包内相对导入）：
    .venv\\Scripts\\python -m app.main

做什么：初始化数据库 → 启动内置网页服务（指向 ui/ 目录）→ 打开 pywebview 窗口加载页面。
MVP 起步默认打开「我的基金」页（第一页改造），改造完成的页面依次接入。
"""
import ctypes
import ctypes.wintypes
import http.server
import os
import socketserver
import sys
import threading
import time

import webview

from . import db as dbm
from . import alert_engine
from . import refresher
from . import system_notify
from . import tray as tray_mod
from .api import Api


def _resource_dir():
    """源码运行 = 项目根目录；PyInstaller 目录版 = _internal（数据文件所在）。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


UI_DIR = os.path.join(_resource_dir(), "ui")
START_PAGE = "index.html"  # 2026-11-08 整改：首页优先，启动进入首页
APP_ID = "com.keepjtl.xiaolicaitong"
ICON_PATH = os.path.join(_resource_dir(), "assets", "brand", "xltc.ico")

# 图标句柄必须保持到进程结束，不能在设置后立即 DestroyIcon
_ICON_HANDLES = []


def _prepare_windows_identity():
    """让任务栏使用小财通自己的 AppUserModelID，而不是 python.exe 的默认身份。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            ctypes.c_wchar_p(APP_ID))
    except Exception:
        pass


def _apply_window_icon():
    """源码启动时 pywebview 仍可能用 Python 图标；窗口出现后补 WM_SETICON。"""
    if sys.platform != "win32" or not os.path.isfile(ICON_PATH):
        return
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.c_void_p
        hwnd = user32.FindWindowW(None, "小财通")
        if not hwnd:
            return
        user32.LoadImageW.restype = ctypes.c_void_p
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x0080
        small = user32.LoadImageW(None, ctypes.c_wchar_p(ICON_PATH), IMAGE_ICON,
                                  16, 16, LR_LOADFROMFILE)
        big = user32.LoadImageW(None, ctypes.c_wchar_p(ICON_PATH), IMAGE_ICON,
                                32, 32, LR_LOADFROMFILE)
        if small:
            user32.SendMessageW(hwnd, WM_SETICON, 0, ctypes.c_void_p(small))
            _ICON_HANDLES.append(small)
        if big:
            user32.SendMessageW(hwnd, WM_SETICON, 1, ctypes.c_void_p(big))
            _ICON_HANDLES.append(big)
    except Exception:
        pass


def _on_window_shown(*_args):
    # 稍等窗口句柄稳定，再补图标；不阻塞 UI 线程
    threading.Timer(0.25, _apply_window_icon).start()


# 运行期窗口/托盘引用（回调线程需要；不能放进局部变量被回收）
_WINDOW = None
_TRAY = None
_QUITTING = False


def _close_to_tray_enabled():
    try:
        conn = dbm.connect()
        try:
            value = dbm.get_setting(conn, "close_to_tray")
        finally:
            conn.close()
        return value != "0"
    except Exception:
        return True


def _show_main_window():
    global _WINDOW
    try:
        if _WINDOW is None:
            return
        _WINDOW.show()
        _WINDOW.restore()
        # 从托盘恢复时也做一次 WebView2 表面重合成；否则窗口可能是空白。
        threading.Timer(0.8, lambda: _nudge_repaint(_find_main_hwnd())).start()
    except Exception as exc:
        print(f"[main] 显示主窗口失败: {exc}")


def _quit_app():
    global _QUITTING, _TRAY
    _QUITTING = True
    try:
        if _TRAY is not None:
            _TRAY.stop()
    except Exception:
        pass
    try:
        if _WINDOW is not None:
            _WINDOW.destroy()
    except Exception:
        pass


def _hide_main_window():
    if _WINDOW is not None:
        _WINDOW.hide()


def _on_closing():
    """关闭窗口：默认隐藏到托盘；用户在设置里改成“直接退出”才真正结束。"""
    if _QUITTING:
        return True
    if _close_to_tray_enabled():
        try:
            _WINDOW.hide()
        except Exception as exc:
            print(f"[main] 隐藏到托盘失败: {exc}")
            return True
        return False
    return True


def _start_tray():
    global _TRAY
    if sys.platform != "win32" or not os.path.isfile(ICON_PATH):
        return None
    try:
        _TRAY = tray_mod.TrayIcon(ICON_PATH, _show_main_window, _quit_app)
        ok = _TRAY.start()
        if not ok:
            print(f"[main] 托盘启动失败: {getattr(_TRAY, '_error', '未知错误')}")
            _TRAY = None
        return _TRAY
    except Exception as exc:
        print(f"[main] 托盘启动异常（不影响主程序）: {exc}")
        _TRAY = None
        return None


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """静态网页服务：只服务 ui/ 目录；不打印访问日志"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=UI_DIR, **kwargs)

    def log_message(self, *args):  # 静默
        pass


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _build_notifier():
    """Windows 用真实 Toast 适配器；其他平台用空通道（不伪装成功）。"""
    if sys.platform == "win32":
        return system_notify.WindowsNotifier()
    return None  # AlertEngine 内部默认 NullNotifier


def _find_main_hwnd():
    """按进程 ID + 窗口标题找 pywebview 主窗口；找不到返回 0。"""
    if os.name != "nt":
        return 0
    user32 = ctypes.windll.user32
    pid = os.getpid()
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _):
        proc_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value != pid:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if (buf.value or "").strip() == "小财通":
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(_enum, 0)
    return found[0] if found else 0


def _nudge_repaint(hwnd):
    """用 1px 尺寸变化触发 WebView2 重新合成，再强制重绘整窗。

    只改尺寸/位置，不改窗口状态；由后台线程在“启动完成”和“从最小化恢复”时调用。
    """
    if os.name != "nt" or not hwnd:
        return
    user32 = ctypes.windll.user32
    try:
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        x, y = rect.left, rect.top
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < 200 or h < 200:
            return
        flags = 0x0004 | 0x0010 | 0x0040  # NOZORDER | NOACTIVATE | SHOWWINDOW
        user32.SetWindowPos(hwnd, 0, x, y, w + 1, h + 1, flags)
        time.sleep(0.18)
        user32.SetWindowPos(hwnd, 0, x, y, w, h, flags)
        user32.InvalidateRect(hwnd, None, True)
        user32.RedrawWindow(hwnd, None, None,
                            0x0001 | 0x0080 | 0x0100 | 0x0400)  # INVALIDATE|ALLCHILDREN|UPDATENOW|FRAME
    except Exception as exc:
        print(f"[main] 窗口重绘失败（不影响页面加载）: {exc}")


def _watch_window_restores(hwnd):
    """窗口从最小化恢复时补一次重绘，避免恢复后只显示空白 WebView2 表面。"""
    if os.name != "nt" or not hwnd:
        return
    user32 = ctypes.windll.user32
    was_minimized = False
    while True:
        time.sleep(0.5)
        try:
            if not user32.IsWindow(hwnd):
                return
            iconic = bool(user32.IsIconic(hwnd))
            if iconic:
                was_minimized = True
            elif was_minimized:
                was_minimized = False
                _nudge_repaint(hwnd)
        except Exception:
            return


def _restore_window_once(show=True):
    """启动后把窗口拉回可见区域并请求重绘，避免最小化/移出屏幕后 WebView2 空白。

    只在启动时执行一次校正，不干预用户之后的正常最小化；--minimized 启动时保持隐藏。
    """
    if os.name != "nt":
        return
    for _ in range(6):
        time.sleep(1.0)
        hwnd = _find_main_hwnd()
        if hwnd:
            break
    else:
        print("[main] 未找到主窗口，跳过可见性恢复")
        return
    user32 = ctypes.windll.user32
    try:
        if show:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.ShowWindow(hwnd, 5)      # SW_SHOW
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
            vy = user32.GetSystemMetrics(77)
            vw = user32.GetSystemMetrics(78)
            vh = user32.GetSystemMetrics(79)
            w, h = rect.right - rect.left, rect.bottom - rect.top
            off = (rect.left < vx - 40 or rect.top < vy - 40 or
                   rect.left > vx + vw - 80 or rect.top > vy + vh - 80 or
                   w < 900 or h < 600)
            if off:
                user32.SetWindowPos(hwnd, 0, 100, 100, 1280, 860,
                                   0x0040 | 0x0004)  # SWP_SHOWWINDOW | SWP_NOZORDER（允许改尺寸）
            user32.SetForegroundWindow(hwnd)
        # 页面加载完成前先不触发；等 2 秒后用尺寸微调强制 WebView2 重合成。
        time.sleep(2.0)
        _nudge_repaint(hwnd)
        if show:
            threading.Thread(target=_watch_window_restores, args=(hwnd,),
                             daemon=True, name="xltc-window-visibility").start()
    except Exception as exc:
        print(f"[main] 主窗口恢复失败（不影响页面加载）: {exc}")


def main():
    dbm.init_db()  # 建库建表 + 种子（幂等）
    start_minimized = "--minimized" in sys.argv

    api = Api()
    # 盯盘提醒引擎必须先启动：它要在**第一次净值刷新之前**注册监听器，
    # 这样启动首刷只建基线、不补造关机期间错过的提醒（1B 契约）。
    try:
        alert_engine.start(notifier=_build_notifier(), ui_notify=api._notify_js)
    except Exception as e:
        print(f"[main] 提醒引擎启动失败（不影响主程序）: {e}")

    # 统一后台刷新器（2026-09-26 口径）：**启动立即刷新一次**（日历 + 净值 + 行情，
    # 强制执行、不受任何跳过规则限制），之后每 30 分钟一次；常驻守护线程，
    # 任何一步失败都不影响主程序启动。
    refresher.start(api, notify=api._notify_js)

    # 内置网页服务（127.0.0.1 随机端口，仅本机可访问）
    server = _Server(("127.0.0.1", 0), _QuietHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/{START_PAGE}"

    _prepare_windows_identity()
    window = webview.create_window(
        "小财通",
        url,
        js_api=api,
        width=1280,
        height=860,
        min_size=(960, 640),
        background_color="#f7f5f1",  # 与页面 --bg 一致，避免启动闪白/闪暖色
        hidden=start_minimized,
    )
    global _WINDOW
    _WINDOW = window
    api.hide_window = _hide_main_window
    api.exit_app = _quit_app

    # 启动后一次性校正窗口位置/尺寸并强制重绘：pywebview 在部分 Windows
    # 多显示器/最小化场景会把窗口放到屏幕外或缩成一条，WebView2 表面不重绘就成空白。
    if not start_minimized:
        threading.Thread(target=_restore_window_once, kwargs={"show": True},
                         daemon=True, name="xltc-window-restore").start()
    window.events.closing += _on_closing
    window.events.shown += _on_window_shown
    _start_tray()
    try:
        api.tray_running = bool(_TRAY)
    except Exception:
        pass
    try:
        webview.start(icon=ICON_PATH if os.path.isfile(ICON_PATH) else None)
    finally:
        if _TRAY is not None:
            _TRAY.stop()


if __name__ == "__main__":
    main()
