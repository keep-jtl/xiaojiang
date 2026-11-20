# -*- coding: utf-8 -*-
"""Windows 真实系统通知通道（零新增 Python 依赖）。

做法：调用系统自带 Windows PowerShell 5.1 + WinRT Windows.UI.Notifications 发 Toast。
- 不安装 winotify / windows-toasts / BurntToast，不改 requirements.txt；
- 运行时不需要联网；
- 用 -EncodedCommand（UTF-16LE Base64）传脚本，标题/正文通过环境变量传入，
  避免中文、引号、换行导致的命令注入或解析问题；
- 失败时返回真实错误，不吞错、不假装成功。

已知限制：未注册开始菜单快捷方式/应用身份时，通知来源可能显示
“Windows PowerShell”或 AUMID，而不是“小财通”。本轮如实记录，不伪造。
"""
import base64
import os
import re
import subprocess
import sys

from .notify_channel import NotifyResult

CHANNEL = "windows-toast"
APP_ID = "com.keepjtl.xiaolicaitong"
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$title = [System.Security.SecurityElement]::Escape($env:XLTC_TOAST_TITLE)
$body = [System.Security.SecurityElement]::Escape($env:XLTC_TOAST_BODY)
$xmlText = "<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$body</text></binding></visual></toast>"
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($xmlText)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
try {
  $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:XLTC_TOAST_AUMID)
  $notifier.Show($toast)
  Write-Output 'APPID'
} catch {
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier().Show($toast)
  Write-Output 'FALLBACK'
}
"""

# 应用自绘桌面横幅：微信/QQ 这类桌面即时通讯不走系统 Toast 横幅，
# 而是在桌面画一个置顶小窗；系统「请勿打扰」不会拦截置顶窗口。
# 这里用 Windows 自带 PowerShell + WinForms，不增加第三方依赖。
_BANNER_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = 'None'
$form.StartPosition = 'Manual'
$form.ShowInTaskbar = $false
$form.TopMost = $true
$form.BackColor = [System.Drawing.Color]::FromArgb(32, 32, 32)
$form.Width = 430
$form.Height = 118
$area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$form.Left = $area.Right - $form.Width - 16
$form.Top = $area.Bottom - $form.Height - 16
$title = New-Object System.Windows.Forms.Label
$title.Text = $env:XLTC_BANNER_TITLE
$title.ForeColor = [System.Drawing.Color]::White
$title.Left = 18
$title.Top = 14
$title.Width = 394
$title.Height = 26
$body = New-Object System.Windows.Forms.Label
$body.Text = $env:XLTC_BANNER_BODY
$body.ForeColor = [System.Drawing.Color]::FromArgb(205, 205, 205)
$body.Left = 18
$body.Top = 46
$body.Width = 394
$body.Height = 54
$form.Controls.Add($title)
$form.Controls.Add($body)
$form.Add_Click({ $form.Close() })
$title.Add_Click({ $form.Close() })
$body.Add_Click({ $form.Close() })
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 8000
$timer.Add_Tick({ $timer.Stop(); $form.Close() })
$timer.Start()
$form.ShowDialog() | Out-Null
"""


def _clean(text, limit):
    text = _CTRL.sub("", str(text or "")).strip()
    return text[:limit]


def _decode(raw):
    """PowerShell 5.1 输出可能是 UTF-8 或系统 GBK；按序尝试，失败也不丢真实错误。"""
    if not raw:
        return ""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _powershell_path():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    p = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return p if os.path.isfile(p) else "powershell.exe"


def _show_banner(title, body):
    """启动一个置顶桌面横幅；返回是否成功拉起进程，不影响 Toast 发送。"""
    if sys.platform != "win32":
        return False
    encoded = base64.b64encode(_BANNER_PS_SCRIPT.encode("utf-16le")).decode("ascii")
    env = os.environ.copy()
    env["XLTC_BANNER_TITLE"] = title
    env["XLTC_BANNER_BODY"] = body
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    cmd = [_powershell_path(), "-NoProfile", "-NonInteractive",
           "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env, creationflags=flags,
                         close_fds=True)
        return True
    except Exception:
        return False


def toast_enabled():
    """读取 Windows 通知总开关；读不到时返回 None（不冒充已开启）。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\PushNotifications")
        value, _ = winreg.QueryValueEx(key, "ToastEnabled")
        return bool(value)
    except Exception:
        return None


def capabilities():
    available = sys.platform == "win32" and (
        os.path.isfile(_powershell_path()) or _powershell_path() == "powershell.exe")
    return {
        "platform": sys.platform,
        "channel": CHANNEL if available else None,
        "available": bool(available),
        "toast_enabled": toast_enabled(),
        "auto_push": bool(available),
        "note": ("Windows 通知通道可用；市场事件与盯盘提醒的后台自动检查已接通。"
                 "系统免打扰会挡住原生 Toast 横幅，应用会另绘置顶桌面横幅（与微信/QQ 同类做法）。"
                 if available else "当前平台不是 Windows，系统通知通道未接通。"),
    }


def send(title, body, timeout=15):
    """发一条真实 Windows Toast。返回 {ok, submitted, channel, app_id, error, detail}。"""
    title = _clean(title, 120) or "小财通"
    body = _clean(body, 400)
    if sys.platform != "win32":
        return {"ok": False, "submitted": False, "channel": None, "app_id": None,
                "error": "当前平台不是 Windows，系统通知通道未接通。", "detail": None}

    encoded = base64.b64encode(_PS_SCRIPT.encode("utf-16le")).decode("ascii")
    banner = _show_banner(title, body)
    env = os.environ.copy()
    env["XLTC_TOAST_TITLE"] = title
    env["XLTC_TOAST_BODY"] = body
    env["XLTC_TOAST_AUMID"] = APP_ID
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    cmd = [_powershell_path(), "-NoProfile", "-NonInteractive",
           "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
           "-EncodedCommand", encoded]
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              timeout=timeout, env=env, creationflags=flags)
    except subprocess.TimeoutExpired:
        return {"ok": False, "submitted": False, "channel": CHANNEL, "app_id": APP_ID,
                "error": "发送系统通知超时。", "detail": None}
    except Exception as exc:
        return {"ok": False, "submitted": False, "channel": CHANNEL, "app_id": APP_ID,
                "error": f"发送系统通知失败：{exc}", "detail": None}

    out = _decode(proc.stdout).strip()
    err = _decode(proc.stderr).strip()
    if proc.returncode != 0:
        return {"ok": False, "submitted": False, "channel": CHANNEL, "app_id": APP_ID,
                "error": "Windows 拒绝了通知请求。",
                "detail": (err or out or f"exit={proc.returncode}")[-600:]}
    return {
        "ok": True,
        "submitted": True,
        "channel": CHANNEL,
        "app_id": APP_ID if "APPID" in out else "powershell",
        "banner": banner,
        "error": None,
        "detail": None if "APPID" in out else "已回退到 PowerShell 通知身份",
        "toast_enabled": toast_enabled(),
    }


def open_settings():
    """打开 Windows 系统通知设置；失败返回错误，不假装已打开。"""
    if sys.platform != "win32":
        return {"ok": False, "error": "当前平台不是 Windows。"}
    try:
        os.startfile("ms-settings:notifications")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"打开系统通知设置失败：{exc}"}


# 模块级真实发送函数别名，供下方适配器显式调用（避免与方法名 send 混淆）。
_send_toast = send


class WindowsNotifier:
    """把本模块真实 Toast 包装成 notify_channel.Notifier 接口。

    安全边界：本适配器不写注册表、不创建开始菜单快捷方式、不安装任何依赖；
    仅在引擎确认 notify_alerts_enabled=1 后才被调用。
    submitted=True 只代表 Windows 已接受发送请求，不代表用户看到。
    """

    def send(self, title, body):
        try:
            outcome = _send_toast(title, body)
        except Exception as exc:
            return NotifyResult(False, f"系统通知发送异常：{exc}")
        if outcome.get("submitted"):
            return NotifyResult(True, None)
        error = outcome.get("error") or outcome.get("detail") or "系统通知发送失败"
        return NotifyResult(False, error)
