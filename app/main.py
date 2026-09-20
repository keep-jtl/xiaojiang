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
from . import market_cal as mcal
from .api import Api
from .sources import market_quotes as mq

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


def _boot_background():
    """启动后台任务（不阻塞窗口打开）：
    1) 交易日历：内置已确认年份直接入库（保证离线也正确）；
       官方公布期（每年 11 月后）联网补次年，失败静默保留旧日历；
    2) 首轮市场行情：腾讯→新浪→入库，失败静默（下次打开再补），
       任何一步出错都不影响主程序启动。"""
    try:
        conn = dbm.connect()
        try:
            mcal.ensure_calendar(conn)  # 幂等：首次启动导入内置，此后只按需联网
        finally:
            conn.close()
        conn = dbm.connect()
        try:
            codes = [r["code"] for r in conn.execute(
                "SELECT code FROM market_meta WHERE enabled = 1").fetchall()]
            if codes:
                mq.refresh_market_quotes(conn, codes)
        finally:
            conn.close()
    except Exception as e:
        print(f"[main] 启动后台任务异常（不影响主程序）: {e}")


def main():
    dbm.init_db()  # 建库建表 + 种子（幂等）
    threading.Thread(target=_boot_background, daemon=True).start()

    # 内置网页服务（127.0.0.1 随机端口，仅本机可访问）
    server = _Server(("127.0.0.1", 0), _QuietHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/{START_PAGE}"

    webview.create_window(
        "小财通",
        url,
        js_api=Api(),
        width=1280,
        height=860,
        min_size=(960, 640),
        background_color="#f6f1e8",  # 浅色暖白（产品规划 19.5，先于页面主题避免闪白）
    )
    webview.start()


if __name__ == "__main__":
    main()
