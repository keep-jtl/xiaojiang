# -*- coding: utf-8 -*-
"""小财通 MVP —— 程序入口

启动方式（项目根目录，注意必须用 -m 以支持包内相对导入）：
    .venv\\Scripts\\python -m app.main

做什么：初始化数据库 → 启动内置网页服务（指向 ui/ 目录）→ 打开 pywebview 窗口加载页面。
MVP 起步默认打开「我的基金」页（第一页改造），改造完成的页面依次接入。
"""
import http.server
import os
import socketserver
import threading

import webview

from . import db as dbm
from . import refresher
from .api import Api

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")
START_PAGE = "funds.html"  # 当前开发中的页面；全部完成后再做应用内起始页路由


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """静态网页服务：只服务 ui/ 目录；不打印访问日志"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=UI_DIR, **kwargs)

    def log_message(self, *args):  # 静默
        pass


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    dbm.init_db()  # 建库建表 + 种子（幂等）

    api = Api()
    # 统一后台刷新器（2026-09-26 口径）：**启动立即刷新一次**（日历 + 净值 + 行情，
    # 强制执行、不受任何跳过规则限制），之后每 30 分钟一次；常驻守护线程，
    # 任何一步失败都不影响主程序启动。
    refresher.start(api, notify=api._notify_js)

    # 内置网页服务（127.0.0.1 随机端口，仅本机可访问）
    server = _Server(("127.0.0.1", 0), _QuietHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/{START_PAGE}"

    webview.create_window(
        "小财通",
        url,
        js_api=api,
        width=1280,
        height=860,
        min_size=(960, 640),
        background_color="#f6f1e8",  # 浅色暖白（产品规划 19.5，先于页面主题避免闪白）
    )
    webview.start()


if __name__ == "__main__":
    main()
