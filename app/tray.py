# -*- coding: utf-8 -*-
"""Windows 原生托盘图标（零新增 Python 依赖）。

pywebview 本身没有托盘 API。这里用 ctypes 调 Shell_NotifyIconW：
- 左键单击托盘：显示/置前主窗口；
- 右键菜单：显示小财通 / 退出；
- 关闭主窗口时若「关闭到托盘」开启，只隐藏窗口，不结束进程。
"""
import ctypes
import ctypes.wintypes as w
import os
import threading

WM_APP = 0x8000
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_QUIT = 0x0012
WM_COMMAND = 0x0111
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
MF_STRING = 0x00000000
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
GWLP_WNDPROC = -4


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("hWnd", w.HWND),
        ("uID", w.UINT),
        ("uFlags", w.UINT),
        ("uCallbackMessage", w.UINT),
        ("hIcon", w.HICON),
        ("szTip", w.WCHAR * 128),
        ("dwState", w.DWORD),
        ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256),
        ("uTimeoutOrVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64),
        ("dwInfoFlags", w.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", w.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", w.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON),
        ("hCursor", w.HANDLE),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hWnd", w.HWND),
        ("message", w.UINT),
        ("wParam", w.WPARAM),
        ("lParam", w.LPARAM),
        ("time", w.DWORD),
        ("pt", w.POINT),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
LRESULT = ctypes.c_ssize_t


def _bind_winapi():
    """显式声明参数类型：不声明时 ctypes 会把 64 位 HINSTANCE 当 32 位 int 截断，
    CreateWindowExW 因此会失败。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = w.UINT
    user32.CreateWindowExW.argtypes = [
        w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
    user32.CreateWindowExW.restype = w.HWND
    user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), w.HWND, w.UINT, w.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.TranslateMessage.restype = w.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
    user32.LoadImageW.restype = w.HANDLE
    user32.CreatePopupMenu.argtypes = []
    user32.CreatePopupMenu.restype = w.HMENU
    user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, w.UINT, w.LPCWSTR]
    user32.AppendMenuW.restype = w.BOOL
    user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, w.HWND, ctypes.c_void_p]
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
    user32.GetCursorPos.restype = w.BOOL
    user32.SetForegroundWindow.argtypes = [w.HWND]
    user32.SetForegroundWindow.restype = w.BOOL
    user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user32.PostMessageW.restype = w.BOOL
    user32.DestroyMenu.argtypes = [w.HMENU]
    user32.DestroyMenu.restype = w.BOOL
    user32.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]
    user32.PostThreadMessageW.restype = w.BOOL
    user32.DestroyWindow.argtypes = [w.HWND]
    user32.DestroyWindow.restype = w.BOOL
    user32.GetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.CallWindowProcW.argtypes = [ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user32.CallWindowProcW.restype = LRESULT
    shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = w.BOOL
    kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
    kernel32.GetModuleHandleW.restype = w.HINSTANCE
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = w.DWORD
    return user32, shell32, kernel32


class TrayIcon:
    def __init__(self, icon_path, on_show, on_quit, tooltip="小财通"):
        self.icon_path = icon_path
        self.on_show = on_show
        self.on_quit = on_quit
        self.tooltip = tooltip
        self.hwnd = None
        self.hicon = None
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._error = None
        self._wndproc = None
        self._old_proc = None
        self._nid = None

    def start(self):
        if os.name != "nt":
            self._error = "托盘仅支持 Windows"
            return False
        self._thread = threading.Thread(target=self._run, name="xltc-tray", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return self._error is None

    def stop(self):
        if self._thread_id:
            try:
                ctypes.WinDLL("user32").PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._nid is not None:
            try:
                ctypes.WinDLL("shell32").Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _run(self):
        try:
            user32, shell32, kernel32 = _bind_winapi()
            self._thread_id = kernel32.GetCurrentThreadId()
            hinst = kernel32.GetModuleHandleW(None)
            # 不注册自定义窗口类：直接创建一个 STATIC 隐藏窗口并子类化它的 WNDPROC。
            # 这样绕开不同 Python/Windows 组合下 RegisterClassW 的类名注册差异。
            self.hwnd = user32.CreateWindowExW(
                0, "STATIC", "XiaoLiCaiTongTray",
                0, 0, 0, 0, 0, None, None, hinst, None)
            if not self.hwnd:
                raise RuntimeError("CreateWindowExW 失败（错误码 %s）" % ctypes.get_last_error())
            self._wndproc = WNDPROC(self._proc)
            self._old_proc = user32.GetWindowLongPtrW(self.hwnd, GWLP_WNDPROC)
            setp = user32.SetWindowLongPtrW(
                self.hwnd, GWLP_WNDPROC,
                ctypes.cast(self._wndproc, ctypes.c_void_p).value)
            if not setp:
                raise RuntimeError("SetWindowLongPtrW 失败（错误码 %s）" % ctypes.get_last_error())
            self.hicon = user32.LoadImageW(
                None, self.icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            if not self.hicon:
                raise RuntimeError("托盘图标加载失败（错误码 %s）" % ctypes.get_last_error())
            self._nid = NOTIFYICONDATAW()
            self._nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            self._nid.hWnd = self.hwnd
            self._nid.uID = 1
            self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            self._nid.uCallbackMessage = WM_APP + 1
            self._nid.hIcon = self.hicon
            self._nid.szTip = self.tooltip[:127]
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid)):
                raise RuntimeError("Shell_NotifyIconW 添加失败（错误码 %s）" % ctypes.get_last_error())
            self._ready.set()
            msg = MSG()
            while True:
                got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self._error = str(exc)
            self._ready.set()
        finally:
            try:
                if self._nid is not None:
                    ctypes.WinDLL("shell32").Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:
                pass

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_APP + 1:
            if lparam == WM_LBUTTONUP:
                self._safe(self.on_show)
            elif lparam == WM_RBUTTONUP:
                self._menu()
            return 0
        if msg == WM_COMMAND:
            cmd = int(wparam) & 0xFFFF
            if cmd == 1:
                self._safe(self.on_show)
            elif cmd == 2:
                self._safe(self.on_quit)
            return 0
        if self._old_proc:
            # 隐藏 STATIC 窗口只需要承接托盘消息；未处理消息直接交给默认窗口过程，
            # 避免跨 ctypes 传 64 位旧 WNDPROC 时出现符号/位宽转换问题。
            return ctypes.WinDLL("user32").DefWindowProcW(hwnd, msg, wparam, lparam)
        return 0

    def _safe(self, fn):
        try:
            if fn:
                fn()
        except Exception:
            pass

    def _menu(self):
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            menu = user32.CreatePopupMenu()
            user32.AppendMenuW(menu, MF_STRING, 1, "显示小财通")
            user32.AppendMenuW(menu, MF_STRING, 2, "退出")
            pt = w.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(self.hwnd)
            cmd = user32.TrackPopupMenu(
                menu, TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, self.hwnd, None)
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            user32.DestroyMenu(menu)
            if cmd == 1:
                self._safe(self.on_show)
            elif cmd == 2:
                self._safe(self.on_quit)
        except Exception:
            pass
