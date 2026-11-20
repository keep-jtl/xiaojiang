# -*- coding: utf-8 -*-
"""Windows 登录自启动（HKCU Run，单用户；不写 HKLM，不需要管理员）。

只做两件事：查询/设置/取消当前用户的开机启动项。
- 打包版：写入 XiaoLiCaiTong.exe 的绝对路径 + --minimized；
- 源码版：用 cmd /c 切到项目根后用 pythonw -m app.main --minimized（仍可真实工作）。
"""
import os
import subprocess
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "XiaoLiCaiTong"


def _project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _command():
    if getattr(sys, "frozen", False):
        return '"%s" --minimized' % os.path.abspath(sys.executable)
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pyw):
        pyw = sys.executable
    root = _project_root()
    # 用 start /D 指定工作目录，避免 cmd /c 嵌套引号在注册表里被拆坏。
    return 'cmd /c start "" /D "%s" "%s" -m app.main --minimized' % (root, pyw)


def get_status():
    if sys.platform != "win32":
        return {"available": False, "enabled": False,
                "error": "当前平台不是 Windows，开机自启不可用。"}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            try:
                value, _ = winreg.QueryValueEx(key, VALUE_NAME)
                return {"available": True, "enabled": True, "command": value,
                        "mode": "exe" if getattr(sys, "frozen", False) else "source"}
            except FileNotFoundError:
                return {"available": True, "enabled": False, "command": None,
                        "mode": "exe" if getattr(sys, "frozen", False) else "source"}
    except Exception as exc:
        return {"available": False, "enabled": False,
                "error": "读取开机自启失败：%s" % exc}


def set_enabled(enabled):
    if sys.platform != "win32":
        return {"ok": False, "error": "当前平台不是 Windows，开机自启不可用。"}
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return {"ok": True, **get_status()}
    except Exception as exc:
        return {"ok": False, "error": "设置开机自启失败：%s" % exc}


def launch_command():
    """给托盘/设置页展示用（不执行）。"""
    return _command()
