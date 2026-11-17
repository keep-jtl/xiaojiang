# 小财通 v0.1.0-preview 第三方组件与许可证（摘要）

> 本文件是 Preview 的许可证摘要，不是完整法律意见。发布前应将各组件随附的 LICENSE/NOTICE 原文一并保留在安装包或发布页。

## Windows 版（Python）

| 组件 | 用途 | 许可证 |
|---|---|---|
| Python 3.14 | 运行时 | PSF License |
| pywebview | 桌面窗口 / WebView 桥 | BSD-3-Clause |
| pythonnet | pywebview 的 Windows/.NET 桥 | MIT |
| akshare | 基金净值 / 基金名单 | MIT |
| pandas | 表格处理 | BSD-3-Clause |
| numpy | 数值计算 | BSD-3-Clause |
| python-dateutil | 日期计算 | Apache-2.0 / BSD（双许可） |
| mini-racer | 交易日历 JS 解码（内置 V8） | ISC 风格许可；V8 为 BSD 风格 |
| requests / urllib3 | HTTP | Apache-2.0 / MIT |
| bottle / proxy_tools | pywebview 依赖 | MIT |
| PyInstaller | 仅构建工具，不随程序运行 | GPLv2-or-later + 特殊例外（允许分发非自由程序） |

## Android 版

| 组件 | 用途 | 许可证 |
|---|---|---|
| Capacitor Core / Android / CLI | Android 容器与构建 | MIT |
| @capacitor-community/sqlite | 本地 SQLite | MIT |
| SQLCipher（随社区 SQLite 插件） | SQLite 加密引擎（本版数据库未启用加密） | BSD 风格；使用方需自行评估美国加密出口自分类要求 |
| AndroidX（appcompat、core、activity、fragment、webkit 等） | Android UI/运行时 | Apache-2.0 |
| Vite | 仅 Web 构建工具 | MIT |
| jeep-sqlite / sql.js（Web 回退依赖） | 插件 Web 端 | MIT / MIT |

## 数据来源

- 基金净值/基金名单：天天基金公开接口
- 指数行情：腾讯财经（主）、新浪财经（备）
- 市场事件：SEC EDGAR、美联储 RSS
- 交易日历：新浪财经、香港 1823、NYSE 规则

这些是免费公开接口，不附带 SLA；使用时应控制请求频率，遵守数据源条款。数据仅供个人研究参考，不构成投资建议。

## 注意

- 未发现当前已安装 Python 依赖中包含 GPL/AGPL 强 copyleft 组件；若后续新增依赖，需重新审查。
- 本文件不包含 Python/Node 的完整传递依赖清单；正式发行前应使用许可证扫描工具生成完整清单，并随包附带各许可证原文。
