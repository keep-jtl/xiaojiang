# -*- coding: utf-8 -*-
"""系统通知通道接口（1B 预留）。

当前只提供空通道；Windows-Toasts 适配器必须等豆包在独立环境验证
安装/导入/真实弹窗/应用身份后再接入，普通通知验证成功前不加入
requirements.txt，也不在正式库启用引擎。
"""
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class NotifyResult:
    """submitted=True 只表示“Windows 已接受发送请求”，不代表用户看见。"""
    submitted: bool
    error: Optional[str] = None


class Notifier(Protocol):
    def send(self, title: str, body: str) -> NotifyResult:
        ...


class NullNotifier:
    """未接通知通道时的安全占位；不会伪装成功。"""

    def send(self, title: str, body: str) -> NotifyResult:
        return NotifyResult(False, "通知通道尚未接通")
