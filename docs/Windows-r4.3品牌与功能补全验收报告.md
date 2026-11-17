# 小财通 r4.3 品牌统一与两端功能补全验收报告

- 版本：`0.1.0-preview-r4.3`
- 日期：2026-11-17
- 交付形式：Windows 便携 ZIP + Android 可检查 APK（debug 签名）+ 本报告

## 一、本轮改了什么

### 1. 品牌统一为用户指定的 Python 文件图标

用户最终指定的 logo 是桌面文件 `本地白底品牌原图`（1559×1875，白底，纸页 + 蓝黄 Python 双蛇）。不是此前误判的金色圆环/¥ 金币方案。

已生成并落地的资产：

| 资产 | 路径 | 规格 |
|---|---|---|
| 透明底高清 logo | `assets/brand/xltc-logo.png` | 1301×1617，862,037 B |
| 方形品牌标志 | `assets/brand/xltc-mark.png` | 620×620，112,221 B |
| 256 图标 | `assets/brand/xltc-icon-256.png` | 256×256，26,989 B |
| 白底品牌图 | `assets/brand/brand-1024.png` | 1024×1024，235,585 B |
| Windows ICO | `assets/brand/xltc.ico` | 16/24/32/48/64/128/256，46,143 B |

已同步位置：

- 桌面 `ui/assets/brand/xltc-logo.png`、`xltc-mark.png`
- 手机 `mobile/public/brand/xltc-logo.png`、`xltc-mark.png`
- Android 全部 `mipmap-*/ic_launcher.png`、`ic_launcher_round.png`、`ic_launcher_foreground.png`
- Android 全部横竖屏 `drawable*/splash.png`
- 桌面 7 个页面页头：`<img class="logo-img" src="assets/brand/xltc-logo.png?v=r4.3">` + `<span class="logo-text">小财通</span>`
- 手机顶栏：品牌图 + “小财通”文字
- Windows EXE 图标：`xltc.ico`，打包后的 `_internal/assets/brand/xltc.ico` 已核对为 46,143 B

旧金色品牌资产已备份到 `本机临时备份目录`，未删除用户原文件。

### 2. 手机端“市场事件”“盯盘提醒”从占位页改为真实功能

- `mobile/index.html`：5 个 tab（首页 / 基金 / 市场事件 / 盯盘提醒 / 我的）
- 市场事件：`newsApi.js` 使用 CapacitorHttp 抓取美联储、BLS 官方 RSS；`db.js` DB v2 新增 `news_cache`、`news_state`，支持缓存、收藏、已读；网络失败显示缓存/明确提示，不编造新闻
- 盯盘提醒：`alerts.js` 对指数读 `quote.pct/price`，对基金读最新净值与日涨跌；`alert_rules`、`alert_events` 持久化；`notifications.js` 调 `@capacitor/local-notifications`，通知渠道 `xltc_alerts`
- `main.js`：前台每 60 秒检查一次，`visibilitychange` 回前台补检；不宣称后台常驻
- Android 清单包含 `POST_NOTIFICATIONS`、`SCHEDULE_EXACT_ALARM`、`RECEIVE_BOOT_COMPLETED`

### 3. 电脑端未开发/半开发界面补齐

- `ui/research.html`：发现 / 赛道·黄金 / 基金经理 / 全球市场四个 tab，真实数据来自 `get_research_overview()` 与本机 SQLite；无“开发中”占位
- `ui/news.html`：市场发现、收藏/忽略/已读持久化、筛选与统计卡可用；17 张真实卡片冒烟读取
- `ui/alerts.html`：14 个市场对象（9 指数 + 4 赛道 ETF + 1 黄金 ETF）、规则真实持久化、关闭到托盘/自启真实 API
- `ui/settings.html`：品牌预览、后台运行、关闭到托盘、Windows 登录自启的真实开关；`app/autostart.py` 写 HKCU Run，`app/tray.py` 原生托盘
- `ui/coming-soon.html`：直接跳转 `research.html`，不再摆占位页
- `app/main.py`、`app/refresher.py`、`app/news_service.py`、`app/alert_engine.py`、`app/api.py` 已接线

## 二、自动化验收结果

### 桌面 UI 无头冒烟（隐藏 pywebview，使用验收库副本）

| 页面 | 结果 |
|---|---|
| `index.html` | logo `src=assets/brand/xltc-logo.png?v=r4.3`，naturalWidth=1301，naturalHeight=1617，`logo-text=小财通`，无“开发中” |
| `settings.html` | 品牌 `xltc-mark.png?v=r4.3`，naturalWidth=620；托盘/自启状态真实返回；无“开发中” |
| `alerts.html` | 市场对象 14 个，关闭到托盘按钮可用，规则说明正确，无“开发中” |
| `news.html` | 17 张卡片，关注 14，市场发现 3，无“开发中” |
| `research.html` | 赛道 5、全球 5、经理 7、发现 3、四个 tab，无“开发中” |

> 本轮真实桌面截图工具持续返回 `Another Computer Use task or desktop transition is in progress`，且屏幕被 Chrome/豆包窗口覆盖，未能取得可信的新页头整机截图。因此以上结论来自隐藏 WebView 的真实 DOM/图片加载结果与打包资源核对，不写成“已整机视觉验收”。最终请以本报告附带的 EXE/APK 实机打开结果为准。

### 开机自启真实写入/撤销

- 初始：`available=True, enabled=False, mode='source'`
- `set_enabled(True)`：成功写入 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\XiaoLiCaiTong`，命令包含项目根目录、`pythonw.exe`、`-m app.main --minimized`
- `set_enabled(False)`：成功删除，恢复 `enabled=False`
- 结论：设置页“登录自启”不是演示按钮；未做跨重启登录验证，报告中如实标注。

### Windows 构建

- 构建脚本：`packaging/windows/build.ps1`
- 产物：`dist\XiaoLiCaiTong\XiaoLiCaiTong.exe`，初始构建 15,132,957 B；最终构建见第四节
- 打包内 `_internal/ui/index.html` 已含 `?v=r4.3` 与 `logo-text`
- 打包内 `_internal/ui/assets/brand/xltc-logo.png` 862,037 B；`_internal/assets/brand/xltc.ico` 46,143 B
- 已从 `XiaoLiCaiTong.exe` 提取 32×32 关联图标并目视核对，为新的纸页 + Python 双蛇标志，不是旧金色图标
- 从最终 ZIP 解压到临时目录后启动 EXE（`--minimized`）：进程保持运行，创建 `data/xltc.db` 405,504 B；库内 `schema_version=7`、`market_meta=14`、`news_items=3`，说明打包版能正常初始化 DB，不是只有壳文件
- 说明：PyInstaller 的 `webview.platforms.android` 收集警告属 Windows 构建预期，不影响 EXE。

### Android 构建

- 版本源：`android/version.properties` → `versionCode=4`，`versionName=0.1.0-preview-r4.3`
- JDK：Android Studio 自带 JBR 为 JDK 25，Gradle 8.14.3 不支持；已改用 Temurin JDK 21（`本机 Temurin JDK 21`）
- SDK：`本机 Android SDK`，已写 `android/local.properties`
- Release 构建失败原因：`keystore.properties` 指向的 `本机 release 签名文件` 已不存在，无法完成 release 签名
- 为保证仍有可安装、可检查的 APK，本轮改为构建 **debug 签名**版本：`assembleDebug` 成功，包名 `com.keepjtl.xiaolicaitong`，versionCode=4，versionName=`0.1.0-preview-r4.3-development-debug`，APK 14,606,309 B，v2 签名验证通过
- APK 内核对：`res/mipmap-xxxhdpi-v4/ic_launcher_foreground.png` 为新的 Python 文件图标；`assets/public/index.html` 含 `xltc-logo.png?v=r4.3`；应用名“小财通”；权限含 `POST_NOTIFICATIONS`、`SCHEDULE_EXACT_ALARM`、`RECEIVE_BOOT_COMPLETED`
- 该 debug APK 可用于本轮功能/界面检查，**不得作为正式商店包**；正式 release 包需要用户提供原签名 keystore 或另行确认新的签名身份

## 三、已知限制（必须如实说明）

- Windows 未签名 Preview，SmartScreen 可能提示未知发布者。
- SEC 源需要 `XLTC_SEC_CONTACT_EMAIL`；未配置时只有美联储源可用。
- Windows 系统通知总开关关闭时 Toast 不弹；应用自绘 TopMost 横幅不是系统推送通道，仍可能被全屏/桌面状态影响。
- 基金经理只显示源站现任经理，不提供历史业绩；赛道/黄金使用 ETF 代理，不是指数/现货本身。
- Android 盯盘只在应用前台按 60 秒检查；应用被杀死或长期后台不保证持续检查/按时通知。
- 手机端市场事件只覆盖美联储/BLS 官方 RSS，不是全市场新闻聚合。
- 本轮未连接真实 Android 设备，DB v2 迁移、RSS、规则计算与 `LocalNotifications.schedule` 只完成构建级验证；需在真机安装后验收。
- 托盘左键显示、右键菜单、关闭到托盘的鼠标点击行为未做本轮实际点击验证；托盘窗口已创建，`start()` 调用成功。
- 未做跨重启登录自启验证，只验证了注册表写入/撤销。
- 未取得新页头整机截图（原因见上），不以旧截图冒充新品牌。

## 四、交付与校验

| 产物 | 路径 | 备注 |
|---|---|---|
| Windows 便携 ZIP | `release\XiaoLiCaiTong-Windows-v0.1.0-preview-r4.3-portable.zip` | 解压后运行 `XiaoLiCaiTong.exe`；不含本机数据库 |
| Windows EXE | `dist\XiaoLiCaiTong\XiaoLiCaiTong.exe` | 15,145,661 B（最终构建） |
| Android APK（可检查） | `release\XiaoLiCaiTong-Android-v0.1.0-preview-r4.3-development-debug.apk` | debug 签名，14,988,196 B；仅用于本轮检查 |
| SHA256 | `release\SHA256SUMS-r4.3.txt` | 已写入最终三项 |
| 本报告 | `docs\Windows-r4.3品牌与功能补全验收报告.md` | 本文件 |

实际校验值（最终构建 2026-11-17 22:17）：

- Windows ZIP：`65,826,734 B`，SHA256 `C2B3B581A9856D071D9D31D863A5308BAAB5F722CA6639B688E56BE17BF30F92`
- Windows EXE：`15,145,661 B`，SHA256 `352BF56C5773BE58C4551D662E87A1094498CAFB639A1BD753D311E157B4C4C2`
- Android debug APK：`14,988,196 B`，SHA256 `FA3BB275D300BA276335F765DDAFB74B9A3B909B0242D60B375824A020D1F698`

验收建议：

1. 解压 Windows ZIP 到短路径，双击 EXE；确认窗口/任务栏/页头均为 Python 文件图标 + “小财通”。
2. 进入研究工具，确认四个 tab 均有真实数据且无“开发中”。
3. 进入盯盘提醒，确认 14 个市场对象与关闭到托盘/自启开关。
4. Android 安装 APK，允许通知权限，添加一条规则，确认前台 60 秒检查与系统通知；再锁屏/后台确认限制符合本报告描述。

## 五、21:40–22:18 最终修正复验

本节记录最后一轮“两端体验修正 + 时间口径统一 + 翻译备用接口”的实际结果，覆盖前面旧数字。

### Windows

- 最终 EXE：`dist\XiaoLiCaiTong\XiaoLiCaiTong.exe`，15,145,661 B，2026-11-17 22:16:16，SHA256 `352BF56C5773BE58C4551D662E87A1094498CAFB639A1BD753D311E157B4C4C2`；已包含 `app/translator.py` 的 MyMemory + clients5 双通道翻译
- 最终 ZIP：`release\XiaoLiCaiTong-Windows-v0.1.0-preview-r4.3-portable.zip`，65,826,734 B，2026-11-17 22:17:29，SHA256 `C2B3B581A9856D071D9D31D863A5308BAAB5F722CA6639B688E56BE17BF30F92`
- 最终 EXE 运行实测：使用验收库副本启动后，`app_meta.last_refresh_at = 2026-11-17 22:18:04`；BLS 行 id 71 在 22:18:25 写入 `title_zh = 主要经济指标最新数据`，与净值刷新同一轮；`news_sources` 含 `sec_edgar / fed_rss / bls_rss`
- 市场事件页：顶部固定显示“最近刷新（与净值刷新同周期）”，数据源说明已含 BLS，`economic_data` 映射为“经济数据”；BLS 卡片优先中文标题并标“自动翻译”+英文原文，来源“美国劳工统计局”
- 基金页恢复实拍：`本机临时目录 `xltc_restore_test.png`；最小化→恢复后页面完整渲染，空白窗口问题已复验
- BLS 接入：`app/sources/news_bls.py`，使用 `XiaoLiCaiTong/0.1 (Android personal app)` UA 访问 `https://www.bls.gov/feed/bls_latest.rss`，桌面实测 HTTP 200 / 5,331 B / `application/rss+xml`；默认/Chrome UA 仍 403
- 翻译链路：MyMemory 429 时自动切 `https://clients5.google.com/translate_a/t`（`client=dict-chrome-ex`），源码实测标题「主要经济指标最新数据」、CPI 摘要均返回中文；最终 EXE 已复验
- 统一刷新：`get_news_feed` / `get_holdings` 返回共同 `refresh_cycle_at`；桌面事件页、首页、基金页共享同一刷新周期。桌面事件页此前实拍见 `本机临时目录 `xltc_bls_news_scroll1.png`（BLS 中文卡片；该截图来自布局相同的上一构建，最终构建已更新右侧数据源文案）
- UI 按钮：`ui/funds.html` 与 `ui/news.html` 已改“刷新数据”；0 持仓时基金页会直接提示“还没有持仓基金，无需刷新”，不调用后端
- 打包核对：ZIP 内 `_internal/ui/funds.html`、`_internal/ui/news.html` 已含最终文案（BLS 数据源、`economic_data`→“经济数据”）；ZIP 内 `XiaoLiCaiTong.exe` 的 SHA256 与 `dist` 一致（`352BF56C...`）；ZIP 不含 `data/xltc.db`
- 图标核对：从最终 EXE 提取的关联图标为纸页 + Python 双蛇；窗口标题栏图标一致。用户截图中的“蓝色圆角方块 + 灰色小条”未在最新 EXE / APK 中复现，判断为旧快捷方式或任务栏图标缓存；从新 ZIP 重新解压/重新固定任务栏可刷新缓存，本轮未改品牌资产
- 已知限制：桌面中文翻译依赖公开免费接口，接口限流时会自动切换备用通道；两条通道都不可用时保留英文原文，界面标“自动翻译”但中文可能为空（本轮最终实测已译出）

### Android

- 最终 APK：14,988,196 B，2026-11-17 21:42:23，SHA256 `FA3BB275D300BA276335F765DDAFB74B9A3B909B0242D60B375824A020D1F698`
- 包名 `com.keepjtl.xiaolicaitong`，versionCode=4，versionName `0.1.0-preview-r4.3-development-debug`，debug 签名，`apksigner verify` 通过
- 已 `adb install -r` 到 `emulator-5554`，启动后实拍：
  - `本机临时目录 `xltc_final_home3.png`：首页，蓝色主题；顶部与首页按钮均为“刷新数据”；最近刷新 `2026-11-17 13:42:35`
  - `本机临时目录 `xltc_final_fund2.png`：基金页，标题“资产总览 · 完整组合”，最近刷新同为 `13:42:35`
  - `本机临时目录 `xltc_final_news3.png`：市场事件 5 条（BLS + 美联储），最近刷新同为 `13:42:35`，首条 BLS 中文译文 + “自动翻译” + 英文原文
- 时间口径：`refreshEverything()` 一次打开只取一个 `at`，同时写 `last_refresh_at` 与 `news_fetched_at`；切换事件 Tab 只读缓存，不再二次刷新改时间；事件秒级时间已完整显示（此前统计卡截断已修）
- 主题：暖金色已移除，`--brand:#2f6fed`、`--brand-soft:#eaf1fd`；启动图 `#EAF1FD` 底；桌面端整窗截图与手机截图均为蓝色体系
- 图标核对：模拟器桌面图标为白色圆形底 + Python 文件图标；`xltc.ico` 的 16/32/256 帧均为纸页 + Python 双蛇，未出现“纯蓝方块”
- 已知限制：仍只有 debug APK，无签名 release；只在 `xltc_avd` 模拟器验收，未连接真机；后台持续盯盘仍未实现

### 最终交付

| 产物 | 路径 | 实际校验 |
|---|---|---|
| Windows 便携 ZIP | `release\XiaoLiCaiTong-Windows-v0.1.0-preview-r4.3-portable.zip` | 65,826,734 B；SHA256 `C2B3B581A9856D071D9D31D863A5308BAAB5F722CA6639B688E56BE17BF30F92` |
| Windows EXE | `dist\XiaoLiCaiTong\XiaoLiCaiTong.exe` | 15,145,661 B；SHA256 `352BF56C5773BE58C4551D662E87A1094498CAFB639A1BD753D311E157B4C4C2` |
| Android debug APK | `release\XiaoLiCaiTong-Android-v0.1.0-preview-r4.3-development-debug.apk` | 14,988,196 B；SHA256 `FA3BB275D300BA276335F765DDAFB74B9A3B909B0242D60B375824A020D1F698` |
| SHA256 清单 | `release\SHA256SUMS-r4.3.txt` | 已同步上述三项 |

> 以上三项哈希为 2026-11-17 22:17 最终重建后的实际文件哈希；`docs` 内旧截图若显示旧构建的刷新时间，以本节数据库实测与最终哈希为准。

