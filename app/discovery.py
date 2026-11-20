# -*- coding: utf-8 -*-
"""市场机遇（电脑端）——人工整理的细分赛道观察清单。

与手机端 mobile/src/discoveryThemes.js + discoveryApi.js 同口径：
- 赛道文字是“研究线索”，不是推荐，也不构成收益承诺；
- 板块代码来自东方财富公开概念板块，行情走 push2 批量接口；
- 相关基金在运行时从真实全市场名单中按名称匹配；
- 所有联网请求显式绕开系统代理（ProxyHandler({})），避免代理抖动。
"""
import datetime
import json
import re
import urllib.request

from .sources import fund_list as fund_list_src

BOARD_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "Chrome/120 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 分类（顺序即展示顺序）
DISCOVERY_CATEGORIES = [
    {"id": "all", "name": "全部"},
    {"id": "ai", "name": "AI 基础设施"},
    {"id": "chip", "name": "半导体"},
    {"id": "energy", "name": "新能源"},
    {"id": "制造", "name": "高端制造"},
]

# 14 个细分赛道（文字与手机端完全一致）
DISCOVERY_THEMES = [
    {
        "id": "liquid-cooling",
        "name": "液冷服务器",
        "boardCode": "BK1138",
        "category": "ai",
        "tag": "算力基础设施",
        "brief": "算力密度上升后，风冷逼近散热上限；冷板、浸没式液冷从“可选项”变成数据中心的基础设施。",
        "thesis": "AI 服务器功率密度持续提升，液冷的价值不在概念，而在渗透率、单机柜价值量和订单兑现。先看真实上架的数据中心，再看冷板、CDU、连接件等环节的收入占比。",
        "watch": ["海外云厂商与国内算力资本开支", "数据中心 PUE 政策与新建项目", "冷板 / 浸没式路线的量产良率", "订单是否转成收入，而不是只有框架协议"],
        "risk": "题材波动大；若算力资本开支放缓、项目验收延迟，估值可能先于业绩回落。",
        "fundKeywords": ["通信", "云计算", "数据中心"],
    },
    {
        "id": "compute-rental",
        "name": "算力租赁",
        "boardCode": "BK1792",
        "category": "ai",
        "tag": "算力基础设施",
        "brief": "算力从“自建机房”转向“按需租用”，上架率、利用率和客户结构比单纯规划更重要。",
        "thesis": "关注算力租赁商的实际上架率、单 P 算力价格、客户集中度和回款周期。规划很多不等于收入兑现，优先看已签约、已计费的机柜。",
        "watch": ["上架率与利用率", "算力价格和合同期限", "客户是否集中在少数大厂", "应收账款与现金流"],
        "risk": "算力价格下行、上架不及预期或客户集中，都会放大业绩波动。",
        "fundKeywords": ["算力"],
    },
    {
        "id": "aidc",
        "name": "智算中心（AIDC）",
        "boardCode": "BK1794",
        "category": "ai",
        "tag": "算力基础设施",
        "brief": "地方和运营商主导的智算中心建设，核心不是开工仪式，而是机房利用率与持续运营。",
        "thesis": "看招标、开工、上电、上架、利用率五个节点。AIDC 的前期资本开支很重，真正的分水岭是能否找到稳定的算力客户。",
        "watch": ["招标与中标金额", "上电和上架进度", "利用率与租赁价格", "地方补贴和电费政策"],
        "risk": "建设周期长、资本开支重；若利用率不足，折旧和电费会侵蚀利润。",
        "fundKeywords": ["算力", "云计算"],
    },
    {
        "id": "advanced-packaging",
        "name": "先进封装",
        "boardCode": "BK1101",
        "category": "chip",
        "tag": "半导体制造",
        "brief": "先进制程成本越来越高，Chiplet、2.5D/3D 封装成为提升算力芯片性能的关键环节。",
        "thesis": "先进封装不是单一设备故事，而是封装厂、设备、材料、载板共同受益。看客户验证、产能利用率和国产化进度，不只看规划产能。",
        "watch": ["先进封装产能利用率", "HBM / Chiplet 客户验证", "载板、设备和材料国产化", "资本开支与折旧压力"],
        "risk": "技术路线迭代快，产能爬坡和客户导入可能低于预期。",
        "fundKeywords": ["半导体", "芯片"],
    },
    {
        "id": "hbm",
        "name": "高带宽内存",
        "boardCode": "BK1152",
        "category": "chip",
        "tag": "半导体存储",
        "brief": "HBM 是 AI 训练卡的瓶颈之一，带宽、良率和供给决定算力释放速度。",
        "thesis": "HBM 的关注点不只是“缺货”，而是堆叠层数、良率、封装能力和客户认证。上游材料、设备和封装的弹性可能大于单一芯片环节。",
        "watch": ["HBM 扩产与良率", "AI 加速卡出货节奏", "先进封装配套能力", "国产替代验证进度"],
        "risk": "供给扩张过快或 AI 资本开支降速，都可能带来价格和估值压力。",
        "fundKeywords": ["半导体", "芯片"],
    },
    {
        "id": "storage-chip",
        "name": "存储芯片",
        "boardCode": "BK1137",
        "category": "chip",
        "tag": "半导体周期",
        "brief": "存储是强周期行业，AI 服务器需求与消费电子复苏叠加时，价格弹性最直接。",
        "thesis": "看现货价格、库存水位、原厂资本开支和合约价。存储的机会通常来自供给收缩与需求拐点共振，而不是单条消息。",
        "watch": ["DRAM / NAND 现货价格", "库存与合约价", "原厂资本开支", "AI 服务器与终端需求"],
        "risk": "周期反转快；价格下跌时盈利和估值可能同时收缩。",
        "fundKeywords": ["半导体", "芯片"],
    },
    {
        "id": "optical-module",
        "name": "光通信模块",
        "boardCode": "BK1136",
        "category": "ai",
        "tag": "AI 网络",
        "brief": "800G / 1.6T 光模块迭代，是 AI 集群互联升级中最直接的硬件环节之一。",
        "thesis": "关注云厂商招标、速率升级、硅光和 CPO 路线，以及订单能否转成出货。高速率产品的客户结构和良率，比概念名称更重要。",
        "watch": ["800G / 1.6T 出货节奏", "海外云厂商资本开支", "硅光 / CPO 路线进展", "良率、毛利率和客户集中度"],
        "risk": "技术路线变化、客户砍单或价格竞争，都可能影响盈利。",
        "fundKeywords": ["通信"],
    },
    {
        "id": "copper-connect",
        "name": "铜缆高速连接",
        "boardCode": "BK1168",
        "category": "ai",
        "tag": "AI 网络",
        "brief": "机柜内短距互联的性价比路线，和光模块不是替代关系，而是不同距离场景的分工。",
        "thesis": "看机柜内连接方案、线缆和连接器价值量、客户验证。铜缆的优势是成本和功耗，边界是距离和速率。",
        "watch": ["机柜内互联方案渗透", "高速线缆 / 连接器订单", "客户验证与量产", "与光方案的距离边界"],
        "risk": "技术路线被光互联替代、订单集中或放量不及预期。",
        "fundKeywords": ["通信"],
    },
    {
        "id": "composite-foil",
        "name": "复合集流体",
        "boardCode": "BK1113",
        "category": "energy",
        "tag": "电池材料",
        "brief": "用复合箔材替代传统金属箔，目标是提升电池安全性和降低材料成本。",
        "thesis": "关注量产良率、车企 / 电池厂验证、设备折旧和单平成本。实验室参数不等于量产能力，先看真实装车和订单。",
        "watch": ["量产良率与单平成本", "电池厂 / 车企验证", "设备投资与折旧", "订单是否形成规模收入"],
        "risk": "新材料导入周期长；良率和成本不达标时，主题容易停留在预期。",
        "fundKeywords": ["电池"],
    },
    {
        "id": "virtual-robot",
        "name": "虚拟机器人",
        "boardCode": "BK1190",
        "category": "制造",
        "tag": "具身智能",
        "brief": "人形机器人和具身智能的软件、仿真、控制环节，价值量不一定最大，但技术变化最快。",
        "thesis": "看真实场景订单、运动控制能力、数据闭环和零部件成本。演示视频不等于商业化，优先看客户复购和交付。",
        "watch": ["场景订单与复购", "运动控制 / 灵巧手进展", "数据采集与仿真闭环", "零部件成本和供应链"],
        "risk": "商业化早期、估值波动大；演示效果与量产能力可能差距很大。",
        "fundKeywords": ["机器人"],
    },
    {
        "id": "pumped-storage",
        "name": "抽水蓄能",
        "boardCode": "BK1003",
        "category": "energy",
        "tag": "新型电力系统",
        "brief": "新能源占比提升后，长时调节能力变得稀缺；抽水蓄能是最成熟的大规模调节方式之一。",
        "thesis": "看核准、开工、投产和电价机制。抽蓄的盈利依赖容量电价和调用频率，不能只看装机规划。",
        "watch": ["核准与开工节奏", "容量电价机制", "利用率和调用频率", "建设周期与资本开支"],
        "risk": "项目周期长、投资回收慢；电价机制变化会影响收益。",
        "fundKeywords": ["电力", "绿色电力"],
    },
    {
        "id": "vanadium-battery",
        "name": "钒电池",
        "boardCode": "BK1095",
        "category": "energy",
        "tag": "长时储能",
        "brief": "液流电池的长时储能路线，寿命和安全性是优势，初始成本与效率是约束。",
        "thesis": "关注项目招标、系统成本、电解液供应和循环效率。长时储能的场景是电网侧和大型工商业，不是所有储能项目。",
        "watch": ["电网侧 / 工商业项目招标", "系统成本与循环效率", "电解液和关键材料", "实际运行数据"],
        "risk": "成本下降不及预期、路线被其他长时储能方案替代。",
        "fundKeywords": ["储能电池"],
    },
    {
        "id": "fourth-gen-semiconductor",
        "name": "第四代半导体",
        "boardCode": "BK1121",
        "category": "chip",
        "tag": "半导体材料",
        "brief": "氧化镓、金刚石等超宽禁带材料，处于早期产业化阶段，想象力大但兑现节点少。",
        "thesis": "这是典型的早期主题：看研发进展、专利、晶体质量和首个产业化订单，不用短期业绩去要求它。",
        "watch": ["晶体质量和尺寸", "器件验证与专利", "首个量产 / 订单节点", "政策和研发资金"],
        "risk": "产业化时间可能很长，主题热度和真实进展容易脱节。",
        "fundKeywords": ["半导体"],
    },
    {
        "id": "ascend",
        "name": "华为昇腾",
        "boardCode": "BK0995",
        "category": "ai",
        "tag": "国产算力",
        "brief": "国产 AI 算力生态，核心是芯片供给、软件适配和行业客户落地。",
        "thesis": "看芯片出货、算子 / 框架适配、服务器伙伴和行业订单。生态不是单点芯片，软件适配和交付能力同样关键。",
        "watch": ["芯片供给与出货", "软件生态适配", "服务器 / 行业客户订单", "供应链国产化"],
        "risk": "供给受限、生态适配进度和订单兑现都可能低于预期。",
        "fundKeywords": ["人工智能", "AI"],
    },
]


def theme_by_id(theme_id):
    for t in DISCOVERY_THEMES:
        if t["id"] == theme_id:
            return t
    return None


def _num(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n


def fetch_board_quotes(codes, timeout=15):
    """批量拉取东方财富 BK 板块行情。

    返回 {"at": ISO, "items": {boardCode: quote}}；失败时抛出异常，由调用方降级。
    f2/100=点位、f3/100=涨跌幅、f8/100=换手、f104=上涨家数、f105=下跌家数。
    """
    if not codes:
        return {"at": datetime.datetime.now().isoformat(timespec="seconds"), "items": {}}
    secids = ",".join("90." + c for c in codes)
    url = BOARD_URL + "?secids=" + urllib.parse.quote(secids) + \
        "&fields=f12,f14,f2,f3,f4,f8,f104,f105"
    req = urllib.request.Request(url, headers=HEADERS)
    raw = OPENER.open(req, timeout=timeout).read()
    data = json.loads(raw.decode("utf-8"))
    diff = ((data.get("data") or {}).get("diff")) or []
    if not diff:
        raise RuntimeError("板块行情暂未返回数据")
    items = {}
    for row in diff:
        code = str(row.get("f12") or "")
        if not code:
            continue
        price = _num(row.get("f2"))
        pct = _num(row.get("f3"))
        items[code] = {
            "code": code,
            "name": str(row.get("f14") or ""),
            "price": None if price is None else price / 100,
            "pct": None if pct is None else pct / 100,
            "turnover": None if _num(row.get("f8")) is None else _num(row.get("f8")) / 100,
            "up": row.get("f104"),
            "down": row.get("f105"),
        }
    return {"at": datetime.datetime.now().isoformat(timespec="seconds"), "items": items}


def _quote_for(theme, quotes):
    if not quotes:
        return None
    return quotes.get(theme["boardCode"])


def match_theme_funds(theme, limit=4):
    """按基金名称关键词匹配相关指数基金（对应手机端 matchThemeFunds）。

    只取「指数型-股票」、剔除 C 类与同组重复，按命中关键词数排序。
    名单读取失败时返回空列表，不阻断页面。
    """
    try:
        all_funds = fund_list_src.get_fund_list()
    except Exception:
        return []
    seen = set()
    hits = []
    for code, name, ftype in all_funds:
        if not code or not name or ftype != "指数型-股票":
            continue
        if re.search(r"C$", name):  # 同组优先 A 类，避免重复
            continue
        score = 0
        for kw in theme.get("fundKeywords", []):
            if kw in name:
                score += 1
        if not score:
            continue
        key = re.sub(r"[ACDE]$", "", name)
        if key in seen:
            continue
        seen.add(key)
        hits.append({"code": code, "name": name, "score": score})
    hits.sort(key=lambda x: (-x["score"], len(x["name"])))
    return hits[:limit]


def news_for_theme(theme, news_items, limit=3):
    """从真实事件中按赛道名/关键词匹配近期公开事件（对应手机端 newsForTheme）。"""
    words = [theme["name"]] + theme.get("fundKeywords", [])
    out = []
    for item in news_items:
        text = str((item.get("title_zh") or item.get("title") or "") + " " +
                   (item.get("summary_zh") or item.get("summary") or ""))
        if any(w and w in text for w in words):
            out.append(item)
    return out[:limit]


def build_overview():
    """市场机遇总览：14 赛道 + 板块行情（行情失败时赛道仍展示，行情字段为 None）。"""
    codes = [t["boardCode"] for t in DISCOVERY_THEMES]
    error = None
    try:
        quote_result = fetch_board_quotes(codes)
        quotes = quote_result["items"]
        at = quote_result["at"]
    except Exception as exc:  # 行情整体失败：降级为无行情，不影响赛道清单
        quotes, at, error = {}, None, "板块行情暂不可用，请检查网络后刷新（%s）" % exc
    themes = []
    for t in DISCOVERY_THEMES:
        item = dict(t)
        item["quote"] = _quote_for(t, quotes)
        themes.append(item)
    return {
        "categories": DISCOVERY_CATEGORIES,
        "themes": themes,
        "at": at,
        "error": error,
        "total": len(DISCOVERY_THEMES),
    }


def build_detail(theme_id, news_items=None):
    """单个赛道详情：赛道全字段 + 行情 + 相关基金 + 近期事件。"""
    theme = theme_by_id(theme_id)
    if theme is None:
        return {"ok": False, "error": "没有找到该赛道"}
    try:
        quote_result = fetch_board_quotes([theme["boardCode"]])
        quote = quote_result["items"].get(theme["boardCode"])
        at = quote_result["at"]
    except Exception:
        quote, at = None, None
    return {
        "ok": True,
        "theme": theme,
        "quote": quote,
        "at": at,
        "funds": match_theme_funds(theme),
        "events": news_for_theme(theme, news_items or []),
    }
