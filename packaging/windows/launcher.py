# -*- coding: utf-8 -*-
"""PyInstaller 入口：保持 `python -m app.main` 的包上下文。

启动前先做安装路径长度自检：Windows 下过长的安装路径会让 curl_cffi 的本地扩展
在导入阶段失败（WinError 206），用户只能看到英文异常框。这里在导入任何业务模块
之前用原生 MessageBoxW 给出中文提示并安全退出，属于使用体验兜底，不代表从底层
解决了 Windows 长路径兼容问题。
"""
from __future__ import annotations

import os
import sys

# 保守拦截阈值：r4 在本机实测的故障边界是 EXE 全路径 186/187 字符，
# 这里留出余量取 180，仅针对本打包版本，不代表 Windows 通用限制。
MAX_EXE_PATH_LEN = 180

_TITLE = "小财通无法从当前文件夹启动"
_MESSAGE = (
    "当前文件夹路径过长，可能导致部分运行文件无法加载。\n\n"
    "请将整个「小财通」文件夹移动到较短的路径，例如：\n"
    "D:\\XiaoLiCaiTong\n\n"
    "移动完成后，重新打开小财通即可。\n"
    "请移动整个文件夹，不要只移动 EXE 文件。"
)


def _show_native_message(title: str, message: str) -> None:
    """只用标准库 ctypes 调原生消息框，不引入任何第三方 UI 依赖。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x00000010)  # MB_ICONERROR
    except Exception:
        # 极端情况下弹窗不可用也不能再抛异常，让进程安静退出。
        pass


def _guard_install_path() -> None:
    if os.name != "nt":
        return
    if not getattr(sys, "frozen", False):
        return
    exe_path = os.path.abspath(sys.executable)
    if len(exe_path) > MAX_EXE_PATH_LEN:
        _show_native_message(_TITLE, _MESSAGE)
        raise SystemExit(1)


_guard_install_path()

from app.main import main  # noqa: E402  （必须放在路径自检之后）


if __name__ == "__main__":
    main()
