# -*- coding: utf-8 -*-
"""
工银积存金价格监控（桌面右下角弹窗提醒）

数据来源：中国工商银行公开行情接口（无需登录）
- 积存金：POST /servlet/AsynGetDataServlet  tranCode=A00622
- 账户黄金 / Au99.99 / 如意金积存：同一接口不同 tranCode（见 SOURCES）

提醒规则：价格每向下穿越一个整数档位（默认 10 元一档，如 900→890→880），
在屏幕右下角弹出提醒窗口；价格回升重新跌破时会再次提醒。
"""

import json
import math
import os
import ssl
import sys
import threading
import time
import traceback
import calendar
import urllib.request
import urllib.parse
import webbrowser
from datetime import datetime, timedelta

import tkinter as tk
from tkinter import ttk, messagebox

# ---------- 全局常量 ----------

APP_NAME = "积存金价格监控"
# 打包成 exe（PyInstaller frozen）后，__file__ 指向临时解压目录，配置写那里会丢；
# 故冻结时改用 exe 所在目录，使 config.json / price_history.json 与 exe 放在一起。
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
# 每日 22:20 日结价（支付宝/工行积存金收益口径）：交易 9:10-22:20 截止那一刻的
# 银行自报价（以 Au(T+D)/境外价为参考，非任何公开盘中价），本地逐日积累
SETTLE_PATH = os.path.join(BASE_DIR, "settle_prices.json")
# 本地分时行情（工行无公开历史分时接口，只能程序运行期间自行采集累积）
HISTORY_PATH = os.path.join(BASE_DIR, "price_history.json")
# 崩溃日志：windowed（无控制台）打包后异常会静默退出，统一落盘便于排查
ERROR_PATH = os.path.join(BASE_DIR, "error.log")
ERROR_LOG_MAX = 512 * 1024  # error.log 超过 512KB 则滚动保留前半段
HISTORY_KEEP_DAYS = 15      # 本地分时最多保留的交易日数
HISTORY_MAX_POINTS = 900    # 每个交易日最多点数（按分钟去重，全天约 800 个）

ICBC_SERVLET = "https://icbcphp.icbc.com.cn/servlet/AsynGetDataServlet"
PAPI_PRODUCT_LIST = "https://papi.icbc.com.cn/wapDynamicPage/goldMarket/productList"

# 在线趋势数据：东方财富公开行情（免鉴权）。上金所黄金9999（Au99.99）与积存金同源，
# 走势基本重合（点差通常 0~1 元/克），提供当日分时与多年日K，无需本机常驻采集。
EM_TRENDS_URL = ("https://push2his.eastmoney.com/api/qt/stock/trends2/get"
                 "?secid=118.AU9999&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
                 "&fields2=f51,f52,f53&iscr=0&iscca=0&ndays=1")
EM_KLINE_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
                "?secid=118.AU9999&fields1=f1,f2,f3,f4,f5,f6"
                "&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=0"
                "&beg={beg}&end={end}&lmt=1000000")
EM_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
              "Referer": "https://quote.eastmoney.com/"}
EM_TRENDS_TTL = 60     # 分时缓存秒数（交易时段内 1 分钟刷新一次即可）
EM_KLINE_TTL = 1800    # 日K缓存秒数

# 财经热搜弹窗：百度热搜·财经榜（免鉴权公开接口），按榜单排名/热度展示
BAIDU_FIN_URL = "https://top.baidu.com/api/board?platform=pc&tab=finance"
NEWS_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/120 Safari/537.36",
                "Referer": "https://top.baidu.com/board?tab=finance"}
NEWS_REFRESH_SEC = 600   # 热榜刷新间隔（10 分钟，榜单本身更新不频繁）
WIN_W = 470              # 主窗口固定宽（轻奢米金版加宽，留白更舒展）


# 工行老站点使用旧版 TLS 重协商，OpenSSL 3 默认拒绝，需要显式放开
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.options |= 0x4  # ssl.OP_LEGACY_SERVER_CONNECT

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://icbcphp.icbc.com.cn/icbc/newperbank/perbank3/gold/frame_acgold_index_out.jsp",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

# 可选监控品种：key -> (显示名, 取数函数名, 价格字段说明)
# price_field 由取数结果决定，此处仅声明界面可选字段
SOURCES = {
    "jicun_active": {"name": "积存金 · 主动积存买入价", "unit": "元/克"},
    "jicun_sell":   {"name": "积存金 · 赎回价",       "unit": "元/克"},
    "ruyi_active":  {"name": "如意金积存 · 买入价",   "unit": "元/克"},
    "ruyi_sell":    {"name": "如意金积存 · 卖出价",   "unit": "元/克"},
    "account_buy":  {"name": "人民币账户黄金 · 买入价", "unit": "元/克"},
    "account_sell": {"name": "人民币账户黄金 · 卖出价", "unit": "元/克"},
    "au9999":       {"name": "上金所 Au99.99",        "unit": "元/克"},
}

DEFAULT_CONFIG = {
    "source": "jicun_active",   # 监控品种
    "step": 10.0,               # 档位间隔（元）
    "interval": 10,             # 轮询间隔（秒），积存金官方约 3 秒刷新
    "sound": True,              # 提醒时播放提示音
    "autostart": False,         # 开机自启（Windows）
    "last_price": None,         # 上次价格（内部用）
    # 我的持仓（工银积存金，按赎回价估值）；分发版本默认空，首次使用在“编辑”中填写
    "hold_grams": 0.0,          # 持有克数
    "hold_cost": 0.0,           # 累计投入成本（元）
    # 补仓明细（工银App交易记录，买入价=金额/克数由程序倒算）。
    # 每日盈亏不再依赖本机滚动的“今日基准价”（停电/关机易脏），
    # 而是每日用东方财富黄金9999历史日K收盘价 + 这些交易记录直接重算。
    "trades": [],
    "privacy": False,           # 隐私模式：隐藏持仓克数/金额/盈亏（小眼睛）
    "alert_up": True,           # 上涨穿越档位也提醒（回本/起飞一起 happy）
    # 补仓计划：[{"price": 目标买入价, "grams": 计划克数}]，
    # 买入价下穿目标价时弹窗提醒按计划补仓，走势图上以蓝色虚线标记
    "plans": [],
    "theme": "light",           # 界面主题：light（轻奢米金·昼，主打）/ dark（轻奢米金·夜）
    "settings_open": False,     # 监控设置卡片是否展开（默认折叠，窗口更短）
    # 微信推送（公众平台测试号 template/send）：桌面端直连 api.weixin.qq.com，
    # 无需公网服务器/备案域名；仅在破档/补仓到价时推送价格信息，不含持仓金额
    "wx_enabled": False,        # 总开关
    "wx_appid": "",             # 测试号 appID
    "wx_secret": "",            # 测试号 appsecret
    "wx_openid": "",            # 关注测试号后得到的 openid
    "wx_template_id": "",       # 测试模板 ID（模板内容需含 {{content.DATA}}）
}

# 微信公众平台测试号（sandbox）：个人主体也能调模板消息，纯服务端 HTTPS 调用
WX_SANDBOX_URL = "https://mp.weixin.qq.com/debug/cgi-bin/sandbox?t=sandbox/login"
WX_API_BASE = "https://api.weixin.qq.com"

# 个人积存金赎回（卖出变现）手续费率：赎回金额的 0.5%（工行 2017-03-01 起，
# 2026-08-28 阶段性优惠公告明确该费率保持 0.5% 不变）
REDEEM_FEE_RATE = 0.005

# ---------- 主题：黑金暗色 / 日间浅色 ----------
# 每个主题给出同一套语义色键；模块级 C_* 是当前主题的全局别名，
# 切主题时 apply_theme() 统一刷新，业务代码全部引用 C_* 即可。
THEMES = {
    # 轻奢米金·夜：暖墨底色 + 香槟金，去掉生硬描边，靠背景深浅分层
    "dark": {
        "C_BG":       "#161310",   # 窗口底色（暖墨）
        "C_CARD":     "#201c17",   # 卡片底色（略亮暖咖）
        "C_CARD_2":   "#2a251e",   # 输入框/次级控件底色
        "C_LINE":     "#383128",   # 分隔线（柔，弱对比）
        "C_GOLD":     "#d4af6a",   # 主金色（香槟金）
        "C_GOLD_HI":  "#f3ddaa",   # 高亮金（大数字/标题）
        "C_GOLD_DIM": "#a9854a",   # 暗金（弱提示/选中底）
        "C_TXT":      "#f2ece0",   # 主文字（暖白）
        "C_TXT_2":    "#b3a894",   # 次要文字
        "C_TXT_3":    "#7d7362",   # 弱化文字
        "C_UP":       "#f26a5c",   # 涨/盈利（珊瑚红）
        "C_DOWN":     "#34c98c",   # 跌/亏损（翡翠绿）
        "C_CHART":    "#1b1814",   # 走势图画布底色
        "C_UP_BG":    "#3a211d",   # 涨徽章底色
        "C_DOWN_BG":  "#163026",   # 跌徽章底色
        "C_ROAST_GOLD_BG": "#2c2417",
        "C_ROAST_UP_BG":   "#36201d",
        "C_ROAST_DN_BG":   "#152c24",
        # 金底按钮上的压字色
        "C_ON_GOLD":  "#221a0c",
        "C_ON_UP":    "#221a0c",
        # 控件悬停/按下
        "C_HOVER":    "#322b22",
        "C_ACTIVE":   "#3f372c",
        "C_PRESSED":  "#382f25",
        "C_SEL":      "#4a3c22",   # 列表选中底
        # Canvas 走势图
        "C_GRID":     "#2a251e",
        "C_GRID_AX":  "#332c23",
        "C_AREA":     "#33281a",
        "C_REF_DASH": "#5a5142",
        "C_BASE_BG":  "#2e2618",   # 补仓基准行底色
        "C_TOTAL_BG": "#282218",   # 合计行底色
        "C_ICON_DISK": "#201c17",  # 应用图标盘心色
        "C_COST":     "#f0b368",   # 持仓成本线标注（琥珀）
        "C_COST_LINE": "#df9a4c",  # 持仓成本虚线
        "C_PLAN":     "#8ab4ef",   # 补仓计划价标注（雾蓝）
        "C_PLAN_LINE": "#6a97d8",  # 补仓计划虚线
    },
    # 轻奢米金·昼（主打）：奶白卡面 + 墨金标题 + 翡绿/朱红，大量留白
    "light": {
        "C_BG":       "#f6f1e7",   # 窗口底色（奶油象牙）
        "C_CARD":     "#fffdf8",   # 卡片白（微暖）
        "C_CARD_2":   "#f1ebe0",   # 输入框/次级控件（米色）
        "C_LINE":     "#e7ddcb",   # 分隔线（柔金米）
        "C_GOLD":     "#a87d2f",   # 主金色（古铜金，浅底下压深可读）
        "C_GOLD_HI":  "#7c5a17",   # 大数字墨金
        "C_GOLD_DIM": "#c7a866",   # 暗金/香槟
        "C_TXT":      "#33291d",   # 主文字（暖墨褐）
        "C_TXT_2":    "#7c6f5b",   # 次要文字
        "C_TXT_3":    "#a99d88",   # 弱化文字
        "C_UP":       "#d8483a",   # 涨/盈利（朱红）
        "C_DOWN":     "#0f9d6d",   # 跌/亏损（翡绿）
        "C_CHART":    "#fdfbf6",
        "C_UP_BG":    "#fbe9e5",
        "C_DOWN_BG":  "#e0f4eb",
        "C_ROAST_GOLD_BG": "#faf1dd",
        "C_ROAST_UP_BG":   "#fbe9e5",
        "C_ROAST_DN_BG":   "#e0f4eb",
        "C_ON_GOLD":  "#fffdf8",
        "C_ON_UP":    "#fffdf8",
        "C_HOVER":    "#f0e7d4",
        "C_ACTIVE":   "#e8ddc6",
        "C_PRESSED":  "#e0d3b8",
        "C_SEL":      "#f2e4c4",
        "C_GRID":     "#eee6d6",
        "C_GRID_AX":  "#e4dac6",
        "C_AREA":     "#f5e9cd",
        "C_REF_DASH": "#c3b59a",
        "C_BASE_BG":  "#f6ecd2",
        "C_TOTAL_BG": "#f2ecdf",
        "C_ICON_DISK": "#fffdf8",
        "C_COST":     "#b5701c",
        "C_COST_LINE": "#c2812a",
        "C_PLAN":     "#2f6fbf",
        "C_PLAN_LINE": "#2f6fbf",
    },
}

# 当前主题（启动时由 config 决定）
THEME_NAME = "dark"


def apply_theme(name):
    """切换全局 C_* 别名到指定主题；需在控件创建/重建前调用。"""
    global THEME_NAME
    THEME_NAME = name if name in THEMES else "dark"
    g = globals()
    for _k, _v in THEMES[THEME_NAME].items():
        g[_k] = _v


# 先用暗色占位，__init__ 里会按配置 apply_theme
apply_theme("dark")

FONT       = "Microsoft YaHei UI"


def _hex_rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _seg_dist(px, py, ax, ay, bx, by):
    """点到线段的距离。"""
    dx, dy = bx - ax, by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _png_rgba(size, rows):
    import zlib
    import struct
    import base64

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(
            ">I", zlib.crc32(c) & 0xffffffff)

    raw = b"".join(rows)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    return base64.b64encode(png)


def _make_icon_photo(size=64):
    """运行时绘制黑金风应用图标：金色圆环 + 深色盘心 + 亮金 ¥，
    替掉 tk 默认的红蓝羽毛标（标题栏 / 任务栏统一品牌感）。"""
    try:
        cx = cy = (size - 1) / 2.0
        R = size * 0.46
        gold, gold_hi, gold_dk = _hex_rgb(C_GOLD), _hex_rgb(C_GOLD_HI), _hex_rgb(C_GOLD_DIM)
        inner_bg = _hex_rgb(C_ICON_DISK)
        strokes = [(-0.50, -0.58, 0.0, -0.08),   # ¥ 左上臂
                   (0.50, -0.58, 0.0, -0.08),    # ¥ 右上臂
                   (0.0, -0.20, 0.0, 0.62),      # 竖线
                   (-0.44, 0.04, 0.44, 0.04),    # 上横
                   (-0.44, 0.28, 0.44, 0.28)]    # 下横
        half = 0.075
        rows = []
        for iy in range(size):
            row = bytearray([0])  # PNG filter: None
            for ix in range(size):
                u = (ix - cx) / R
                v = (iy - cy) / R
                d = math.hypot(u, v)
                if d > 1.0:
                    row += bytes((0, 0, 0, 0))
                    continue
                alpha = 255 if d <= 0.965 else int(255 * (1.0 - d) / 0.035)
                if d >= 0.78:
                    col = _lerp(gold_dk, gold_hi, (d - 0.78) / 0.22)
                else:
                    col = inner_bg
                if d < 0.76 and any(
                        _seg_dist(u, v, *s) <= half for s in strokes):
                    col = gold_hi
                row += bytes((col[0], col[1], col[2], max(0, min(255, alpha))))
            rows.append(bytes(row))
        return tk.PhotoImage(data=_png_rgba(size, rows))
    except Exception:
        return None


def _dark_title(win, dark=True):
    """让 Windows 窗口标题栏跟随深色/浅色模式（Win10 2004+ / Win11 生效，旧版静默忽略）。"""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        dwm = ctypes.windll.dwmapi
        # 不声明 restype/argtypes 时 HWND 按 int 传递，64 位句柄高位会被截断导致静默失败
        user32.GetParent.restype = wintypes.HWND
        user32.GetParent.argtypes = [wintypes.HWND]
        dwm.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        hwnd = user32.GetParent(win.winfo_id()) or wintypes.HWND(win.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        # Win11/较新 Win10 用 20，旧版 Win10 2004 用 19，都试一遍
        for attr in (20, 19):
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val),
                                      ctypes.sizeof(val))
        # 仅设属性时标题栏往往不会立刻重绘（实测 Win11 26300），
        # 必须带 FRAMECHANGED 触发一次非客户区刷新，深色标题栏才马上生效。
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT]
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_FRAMECHANGED = 0x0020
        user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED)
    except Exception:
        pass


def dark_checkbutton(parent, text, variable, bg=None, fg=None,
                     command=None, on_color=None):
    """黑金风自绘复选框：未选中暗色空心框，选中金底黑勾。
    避开 clam 主题 ttk.Checkbutton 在高 DPI 下选中态渲染异常的问题。
    颜色默认 None，调用时才解析当前主题（默认参数在模块加载时求值会被冻结成暗色）。"""
    if bg is None:
        bg = C_BG
    if fg is None:
        fg = C_TXT
    if on_color is None:
        on_color = C_GOLD
    box = tk.Frame(parent, bg=bg, cursor="hand2")
    mark = tk.Label(box, text="✓", width=2, height=1,
                    font=(FONT, 9, "bold"), bd=0, padx=0, pady=0,
                    highlightthickness=1)

    def redraw(_=None):
        if variable.get():
            mark.configure(bg=on_color, fg=C_ON_GOLD, highlightbackground=on_color)
        else:
            mark.configure(bg=C_CARD_2, fg=C_CARD_2, highlightbackground=C_LINE)

    mark.pack(side="left")
    lab = tk.Label(box, text=text, bg=bg, fg=fg, font=(FONT, 9))
    lab.pack(side="left", padx=(6, 0))

    def toggle(_=None):
        variable.set(not variable.get())
        redraw()
        if command:
            command()

    for w in (mark, lab, box):
        w.bind("<Button-1>", toggle)
    variable.trace_add("write", lambda *_: redraw())
    redraw()
    return box


def apply_dark_style(root):
    """全局 ttk 深色样式（clam 主题最听配色的话，vista 主题会强塞系统白皮）。"""
    style = ttk.Style()
    style.theme_use("clam")
    style.configure(".", background=C_BG, foreground=C_TXT,
                    bordercolor=C_LINE, focuscolor="", lightcolor=C_LINE,
                    darkcolor=C_LINE)
    style.configure("TFrame", background=C_BG)
    style.configure("TButton", padding=(16, 8), background=C_CARD_2,
                    foreground=C_TXT, bordercolor=C_LINE, focusthickness=0,
                    font=(FONT, 9))
    style.map("TButton",
              background=[("active", C_HOVER), ("pressed", C_PRESSED)],
              foreground=[("active", C_GOLD_HI)])
    style.configure("Gold.TButton", background=C_GOLD, foreground=C_ON_GOLD,
                    bordercolor=C_GOLD, font=(FONT, 9, "bold"), padding=(16, 8))
    style.map("Gold.TButton", background=[("active", C_GOLD_HI),
                                          ("pressed", C_GOLD_DIM)],
              foreground=[("active", C_ON_GOLD)])
    style.configure("TEntry", fieldbackground=C_CARD_2, foreground=C_TXT,
                    insertcolor=C_TXT, bordercolor=C_LINE, padding=3)
    style.configure("TCombobox", fieldbackground=C_CARD_2, background=C_CARD_2,
                    foreground=C_TXT, arrowcolor=C_GOLD, bordercolor=C_LINE,
                    selectbackground=C_GOLD_DIM, selectforeground=C_ON_GOLD,
                    insertcolor=C_TXT, padding=3)
    style.map("TCombobox",
              fieldbackground=[("readonly", C_CARD_2)],
              foreground=[("focus", C_TXT), ("readonly", C_TXT)],
              selectbackground=[("focus", C_GOLD_DIM)])
    style.configure("TSpinbox", fieldbackground=C_CARD_2, foreground=C_TXT,
                    arrowcolor=C_GOLD, bordercolor=C_LINE, insertcolor=C_TXT)
    style.configure("TCheckbutton", background=C_BG, foreground=C_TXT,
                    indicatorcolor=C_CARD_2, bordercolor=C_LINE)
    style.map("TCheckbutton",
              background=[("active", C_BG)],
              indicatorcolor=[("selected", C_GOLD)])
    style.configure("Card.TCheckbutton", background=C_CARD, foreground=C_TXT,
                    indicatorcolor=C_CARD_2)
    style.map("Card.TCheckbutton",
              background=[("active", C_CARD)],
              indicatorcolor=[("selected", C_GOLD)])
    style.configure("Vertical.TScrollbar", background=C_CARD_2,
                    troughcolor=C_BG, arrowcolor=C_TXT_2, bordercolor=C_BG)
    style.map("Vertical.TScrollbar", background=[("active", C_ACTIVE)])
    style.configure("Treeview", background=C_CARD, fieldbackground=C_CARD,
                    foreground=C_TXT, bordercolor=C_LINE, borderwidth=0,
                    rowheight=26, font=(FONT, 9))
    style.configure("Treeview.Heading", background=C_CARD_2, foreground=C_GOLD,
                    relief="flat", font=(FONT, 9, "bold"), padding=(4, 5))
    style.map("Treeview.Heading", background=[("active", C_PRESSED)])
    style.map("Treeview",
              background=[("selected", C_SEL)],
              foreground=[("selected", C_GOLD_HI)])
    # Combobox 弹出的下拉列表是独立 Tk 列表控件，只能走 option 这条路
    root.option_add("*TCombobox*Listbox.background", C_CARD_2)
    root.option_add("*TCombobox*Listbox.foreground", C_TXT)
    root.option_add("*TCombobox*Listbox.selectBackground", C_GOLD_DIM)
    root.option_add("*TCombobox*Listbox.selectForeground", C_ON_GOLD)
    root.option_add("*TCombobox*Listbox.font", (FONT, 9))


# ---------- 数据抓取 ----------

def _post_trancode(tran_code, extra=None, timeout=10):
    data = {"tranCode": tran_code}
    if extra:
        data.update(extra)
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(ICBC_SERVLET, data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gbk"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise RuntimeError("行情返回内容无法解析")


def fetch_prices():
    """
    返回 dict：各品种当前价，以及附加信息（最高/最低/时间）。
    任何网络异常向上抛出，由调用方处理。
    """
    result = {"time": datetime.now().strftime("%H:%M:%S")}

    # 1) 积存金 A00622
    try:
        d = _post_trancode("A00622")
        rf = (d.get("rf") or [{}])[0]
        result["jicun_active"] = float(rf.get("ActivePrice") or 0) or None
        result["jicun_sell"] = float(rf.get("SellPrice") or 0) or None
        result["high"] = float(rf.get("HighPrice") or 0) or None
        result["low"] = float(rf.get("LowPrice") or 0) or None
        result["date"] = rf.get("RegDate", "")
    except Exception:
        pass

    # 2) 如意金积存 A00505（价格单位 0.01 元）
    try:
        d = _post_trancode("A00505")
        p = (d.get("pronoinfo") or [{}])[0]
        bp = float(p.get("buyprice") or 0)
        sp = float(p.get("sellprice") or 0)
        result["ruyi_active"] = bp / 100.0 if bp else None
        result["ruyi_sell"] = sp / 100.0 if sp else None
    except Exception:
        pass

    # 3) 人民币账户黄金 A00500
    try:
        d = _post_trancode(
            "A00500",
            {"Area_code": "0200", "trademode": "1", "proIdsIn": "", "isFirstTime": "1"},
        )
        market = d.get("market") or []
        for m in market:
            if m.get("metalname") == "人民币账户黄金":
                result["account_buy"] = float(m.get("buyprice") or 0) or None
                result["account_sell"] = float(m.get("sellprice") or 0) or None
                break
    except Exception:
        pass

    # 4) 上金所 Au99.99（m 站公开接口）
    try:
        url = PAPI_PRODUCT_LIST + "?type=0"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": HEADERS["User-Agent"],
                "Referer": "https://m.icbc.com.cn/",
            },
        )
        with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        for it in d.get("data") or []:
            if it.get("id") == "Au99.99":
                result["au9999"] = float(it.get("newPrice") or 0) or None
                break
    except Exception:
        pass

    return result


# ---------- 微信推送（公众平台测试号） ----------

class WeChatPusher:
    """测试号模板消息推送：access_token 内存缓存（有效期 7200 秒，提前 5 分钟刷新）。
    所有方法均为阻塞式网络调用，调用方需自行放到后台线程。"""

    def __init__(self):
        self._token = ""
        self._token_exp = 0.0
        self._lock = threading.Lock()

    def configured(self, cfg):
        return all(str(cfg.get(k) or "").strip() for k in
                   ("wx_appid", "wx_secret", "wx_openid", "wx_template_id"))

    def _get_token(self, cfg):
        with self._lock:
            if self._token and time.time() < self._token_exp:
                return self._token
            url = (f"{WX_API_BASE}/cgi-bin/token?grant_type=client_credential"
                   f"&appid={urllib.parse.quote(cfg['wx_appid'].strip())}"
                   f"&secret={urllib.parse.quote(cfg['wx_secret'].strip())}")
            req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            if "access_token" not in d:
                raise RuntimeError(f"获取 access_token 失败：{d.get('errmsg') or d}（errcode={d.get('errcode')}）")
            self._token = d["access_token"]
            self._token_exp = time.time() + int(d.get("expires_in", 7200)) - 300
            return self._token

    def send(self, cfg, content_or_dict):
        """发送一条模板消息。第二个参数可以是 str（单变量 content，向后兼容）或 dict（多变量）。"""
        if not self.configured(cfg):
            return "微信推送参数不完整（appID/appsecret/openid/模板ID 都要填）"
        try:
            token = self._get_token(cfg)
            if isinstance(content_or_dict, dict):
                # 多变量模板：dict 的键就是模板里的 {{xxx.DATA}} 变量名（不含 .DATA 后缀）
                data = {}
                for k, v in content_or_dict.items():
                    data[k] = {"value": str(v) if v is not None else ""}
            else:
                # 单变量 content（旧模板兜底）
                data = {"content": {"value": str(content_or_dict), "color": "#9c6b1f"}}
            payload = {
                "touser": cfg["wx_openid"].strip(),
                "template_id": cfg["wx_template_id"].strip(),
                "data": data,
            }
            url = f"{WX_API_BASE}/cgi-bin/message/template/send?access_token={token}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8",
                         "User-Agent": HEADERS["User-Agent"]},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            if d.get("errcode", 0) != 0:
                # token 失效时清缓存，下次发送自动重新获取
                if d.get("errcode") in (40001, 40014, 42001):
                    with self._lock:
                        self._token, self._token_exp = "", 0.0
                return f"微信接口返回错误：{d.get('errmsg')}（errcode={d.get('errcode')}）"
            return None
        except Exception as e:
            return f"微信推送失败：{e}"


# ---------- 配置 ----------

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ---------- 本地分时行情采集 ----------

def load_history():
    """读取本地分时数据：{交易日: [["HH:MM", 价格], ...]}，按交易日升序。"""
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {d: [[t, float(p)] for t, p in pts]
                for d, pts in sorted(data.items()) if isinstance(pts, list)}
    except (OSError, ValueError, TypeError):
        return {}


def save_history(hist):
    """按日期裁剪保留最近 N 个交易日后落盘。"""
    trimmed = dict(sorted(hist.items())[-HISTORY_KEEP_DAYS:])
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, ensure_ascii=False)
    except OSError:
        pass


# ---------- 每日日结价（支付宝/工行积存金收益口径：每晚 22:20 截止自报价） ----------

def load_settles():
    """读取日结价：{"YYYY-MM-DD": {"price": 价, "src": icbc|alipay|est}, ...}。
    兼容旧版仅存裸数值的写法；按日期升序返回。src=icbc 为程序22:20自动锁定，
    alipay 为按支付宝截图反推的人工精确价，est 为日K近似。"""
    out = {}
    try:
        with open(SETTLE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for d, v in data.items():
            try:
                if isinstance(v, dict):
                    out[d] = {"price": float(v["price"]), "src": str(v.get("src") or "")}
                else:
                    out[d] = {"price": float(v), "src": ""}
            except (TypeError, ValueError, KeyError):
                continue
    except (OSError, ValueError):
        pass
    return dict(sorted(out.items()))


def save_settles(settles):
    try:
        with open(SETTLE_PATH, "w", encoding="utf-8") as f:
            json.dump(settles, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def append_history_point(hist, day, hm, price):
    """追加一个分时点：同分钟去重（保留最新价），同日限长，返回是否发生变更。"""
    if not day or not price:
        return False
    pts = hist.setdefault(day, [])
    if pts and pts[-1][0] == hm:
        if pts[-1][1] != price:
            pts[-1][1] = price
            return True
        return False
    pts.append([hm, float(price)])
    if len(pts) > HISTORY_MAX_POINTS:
        del pts[:len(pts) - HISTORY_MAX_POINTS]
    return True


def in_trade_session(hm):
    """积存金交易时段：周一至周五 9:10-22:20（节假日无法本地判断，由接口有无报价兜底）。"""
    return "09:10" <= hm <= "22:20"


def trade_minutes():
    """交易时段总分钟数（9:10-22:20 连续计时，与支付宝分时横轴一致）。"""
    return (22 * 60 + 20) - (9 * 60 + 10)


# ---------- 在线趋势行情（东方财富 · 上金所黄金9999，免鉴权） ----------

def _get_em_json(url, timeout=10, retries=6):
    """取东财 JSON。该行情域边缘节点对高频访问会直接掐 TCP（RemoteDisconnected，
    属 ConnectionResetError），多节点轮询下时好时坏（实测约 1/8 放行），
    故对连接类错误做几次短退避重试；HTTP 错误响应（403/429 等）不重试。"""
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=EM_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except (ConnectionError, TimeoutError) as e:
            last_err = e
            if i < retries - 1:
                time.sleep(0.35 * (i + 1))
    raise last_err


def fetch_em_trends():
    """
    黄金9999最新会话分时（东财交易日=前一夜盘20:00-次日2:30 + 当日白盘，
    周一的会话还含周五夜盘）。
    返回 {"date": 东财交易日(夜盘起始日), "pre_close": 昨收,
          "points": [["YYYY-MM-DD HH:MM", 价格], ...]}（保留完整时间戳，
          由渲染方按自然日与积存金时段 9:10-15:30/20:00-22:20 自行过滤）。
    只保留价格大于 0 的分钟点（集合竞价/无成交时段会出现 0 值）。
    """
    d = (_get_em_json(EM_TRENDS_URL) or {}).get("data") or {}
    pts = []
    for row in d.get("trends") or []:
        seg = row.split(",")
        if len(seg) < 2:
            continue
        try:
            p = float(seg[1])  # f52 分钟成交价（seg[2] f53 为分钟均价）
        except (TypeError, ValueError):
            continue
        if p > 0 and len(seg[0]) >= 16:
            pts.append([seg[0][:16], p])
    date = d.get("date") or (d.get("trends") or [""])[0][:10]
    return {"date": date, "pre_close": d.get("preClose"),
            "name": d.get("name") or "黄金9999", "points": pts}


def fetch_em_daily(beg, end):
    """
    黄金9999历史日K，beg/end 为 "YYYYMMDD"。
    返回 [["YYYY-MM-DD", 开, 收, 高, 低], ...]（均为 float）。
    """
    d = (_get_em_json(EM_KLINE_URL.format(beg=beg, end=end)) or {}).get("data") or {}
    out = []
    for row in d.get("klines") or []:
        seg = row.split(",")
        if len(seg) < 6:
            continue
        try:
            out.append([seg[0]] + [float(x) for x in seg[1:5]])  # 开/收/高/低
        except ValueError:
            continue
    return out


# ---------- 财经热搜（百度热搜·财经榜，免鉴权公开接口） ----------

def _format_hot(score):
    """hotScore 原始整数转可读热度：4842720 -> '484万'，过亿 -> '1.2亿'。"""
    try:
        n = int(score)
    except (TypeError, ValueError):
        return ""
    if n >= 100000000:
        return "%g亿" % round(n / 100000000, 1)
    if n >= 10000:
        return "%d万" % round(n / 10000)
    return str(n)


def fetch_finance_hot():
    """拉取百度热搜·财经榜（约 30 条）。
    返回 [{"rank": 排名, "word": 标题, "url": 链接(rawUrl), "hot": 热度整数}, ...]，
    按榜单 index 升序。失败抛异常。"""
    req = urllib.request.Request(BAIDU_FIN_URL, headers=NEWS_HEADERS)
    with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
        d = json.loads(resp.read().decode("utf-8", "ignore"))
    cards = ((d.get("data") or {}).get("cards")) or []
    content = cards[0].get("content") if cards else None
    if not content:
        raise RuntimeError("百度财经榜返回为空")
    items = []
    for r in content:
        word = (r.get("word") or "").strip()
        if not word:
            continue
        items.append({
            "rank": r.get("index", len(items)) + 1,
            "word": word,
            "url": r.get("rawUrl") or r.get("url") or "",
            "hot": r.get("hotScore") or 0,
            "desc": (r.get("desc") or "").strip(),       # 一句话简介（财经榜通常为空）
            "change": str(r.get("hotChange", "")).strip().lower(),  # up / down / same
            "tag": str(r.get("hotTag", "")).strip(),     # "1" 新 / "3" 沸 / "0" 无
        })
    items.sort(key=lambda x: x["rank"])
    return items



def daily_pl_rows(settles, est_daily, trades, total_grams,
                  live_sell=None, today=None, now_hm=None):
    """根据每晚 22:20 日结价与买入明细，逐日重算每日盈亏（支付宝同口径）。

    settles:  {"YYYY-MM-DD": 日结价} 精确价（工行22:20截止自报价/支付宝截图反推）
    est_daily: [["YYYY-MM-DD", 开, 收, 高, 低], ...] 黄金9999日K，仅用于无日结价的
               早期日期近似（凌晨收盘口径，误差较大，行上标 est=True）
    trades:   [{"dt": "YYYY-MM-DD HH:MM", "grams": g, "amount": a}, ...]（均为买入）
    total_grams: 当前总持仓克数；底仓 = 总持仓 − 明细合计（早于所有记录交易的存量）
    live_sell: 最新工行积存金赎回价。盘中（now_hm < 22:20）且 today 已有日结基准时，
               “今日”一行按实时盈亏计算（昨夜日结价→实时赎回价）；22:20 后日结价
               已生成，则按日结价显示“收盘”。
    today:    实时盈亏所属交易日（"YYYY-MM-DD"），默认取序列最后一日
    now_hm:   当前 "HH:MM"，用于判断今日是否已收盘；默认 "23:59"（视为已收盘）
    返回 {日期: dict(close, prev, mark, grams_open, buy_g, buy_cost, pl,
                    is_today, est)}，按日期升序。

    口径（支付宝式，已用 10-26 盘中两时点证实）：
      当日盈亏 = 当日结算持仓克数 × (今日日结价 − 上一交易日日结价)
      当日结算持仓 = 开盘存量 + 当日买入克数（当日买入即计入，按上一交易日
      日结价给新买入计当日盈亏；买入价只影响成本，不进入日盈亏公式）。
    日结价优先取 settles（工行22:20截止自报价，与支付宝同源）；程序未运行而
    漏记的日期，用黄金9999日K收盘价近似并标记为估算。
    """
    est_close = {r[0]: float(r[2]) for r in (est_daily or [])}
    close = {}
    est_flag = {}
    for d, p in (settles or {}).items():
        close[d] = float(p)
        est_flag[d] = False
    for d, p in est_close.items():
        if d not in close:
            close[d] = p
            est_flag[d] = True
    if not close:
        return {}
    dates = sorted(close)
    if today and today not in close:
        dates2 = sorted(set(dates + [today]))
    else:
        dates2 = dates

    # 买入按交易日归集；非交易日（周末/节假日）成交的，顺延计入之后首个有行情日
    buy_g, buy_a = {}, {}
    for t in trades:
        g = float(t.get("grams") or 0)
        a = float(t.get("amount") or 0)
        if g <= 0:
            continue
        ds = str(t.get("dt", ""))[:10]
        settle = next((d for d in dates2 if d >= ds), dates2[-1])
        buy_g[settle] = buy_g.get(settle, 0.0) + g
        buy_a[settle] = buy_a.get(settle, 0.0) + a

    detail_g = sum(buy_g.values())
    base_g = max(float(total_grams or 0) - detail_g, 0.0)
    today = today or dates2[-1]
    now_hm = now_hm or "23:59"

    rows = {}
    grams_open = base_g  # 每个交易日开盘（昨日日结后）的存量
    prev_d, prev_c = None, None  # 上一有有效日结价的交易日（缺口日期不打断基准）
    for d in dates2:
        cur_c = close.get(d)
        is_today = bool(live_sell) and d == today and now_hm <= "22:20"
        if cur_c is None and not is_today:
            # 既无日结价又非盘中实时（如早期缺口/非交易日）：不产出，但买入仍计入存量
            grams_open += buy_g.get(d, 0.0)
            continue
        g_t, a_t = buy_g.get(d, 0.0), buy_a.get(d, 0.0)
        if prev_c is None:
            # 首个有效日之前没有日结基准，日价差盈亏无意义（今日盘中同理，需昨夜价）
            grams_open += g_t
            if cur_c is not None:
                prev_d, prev_c = d, cur_c
            continue
        mark = float(live_sell) if is_today else cur_c
        # 支付宝口径：当日买入即计入结算持仓，统一按上一交易日日结价起算
        pl = (grams_open + g_t) * (mark - prev_c)
        rows[d] = {
            "close": cur_c if cur_c is not None else mark, "prev": prev_c,
            "prev_day": prev_d, "mark": mark,
            "grams_open": grams_open, "buy_g": g_t, "buy_cost": a_t,
            "pl": pl, "is_today": is_today,
            "est": bool(est_flag.get(d) or est_flag.get(prev_d)),
        }
        grams_open += g_t
        if cur_c is not None:
            prev_d, prev_c = d, cur_c
    return rows


# ---------- 开机自启（Windows 注册表） ----------

def _run_key():
    import winreg
    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
    )


def set_autostart(enable):
    if not sys.platform.startswith("win"):
        return False
    import winreg
    try:
        key = _run_key()
        if enable:
            exe = sys.executable
            if getattr(sys, "frozen", False):
                cmd = f'"{exe}"'
            else:
                cmd = f'"{exe}" "{os.path.join(BASE_DIR, "gold_monitor.py")}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


# ---------- 档位检测 ----------

def crossed_tiers(prev_price, price, step):
    """
    返回价格从 prev_price 跌到 price 过程中向下穿越的所有档位线（降序）。
    档位线为 step 的整数倍：step=10 时即 ...900, 890, 880...
    """
    if prev_price is None or price is None or price >= prev_price:
        return []
    crossed = []
    k_hi = math.floor(prev_price / step)
    k_lo = math.ceil(price / step)
    # 严格穿越：档位线 t 满足 price <= t < prev_price
    for k in range(k_hi, k_lo - 1, -1):
        t = k * step
        if price <= t < prev_price:
            crossed.append(t)
    return crossed


def crossed_tiers_up(prev_price, price, step):
    """
    返回价格从 prev_price 涨到 price 过程中向上穿越的所有档位线（升序）。
    与 crossed_tiers 对称：严格穿越，档位线 t 满足 prev_price < t <= price。
    """
    if prev_price is None or price is None or price <= prev_price:
        return []
    crossed = []
    k_lo = math.floor(prev_price / step) + 1
    k_hi = math.floor(price / step)
    for k in range(k_lo, k_hi + 1):
        t = k * step
        if prev_price < t <= price:
            crossed.append(t)
    return crossed


# ---------- 右下角提醒弹窗 ----------

class ToastManager:
    WIDTH = 340
    HEIGHT = 150
    MARGIN = 16
    GAP = 12

    def __init__(self, root):
        self.root = root
        self.active = []  # 由下往上排列

    def _work_area(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        try:
            self.root.attributes("-alpha", 0)  # 确保窗口已映射以便取工作区
        except tk.TclError:
            pass
        return sw, sh

    def show(self, title, message, tag=None, duration_ms=12000, kind="down"):
        """在主线程调用。kind: "down" 红色顶条（跳水警报）/ "up" 金色顶条（上涨喜报）
        / "plan" 蓝色顶条（补仓计划到价）。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

        if kind == "up":
            bar, on_bar = C_GOLD, C_ON_GOLD
        elif kind == "plan":
            bar, on_bar = C_PLAN_LINE, "#ffffff"
        else:
            bar, on_bar = C_UP, C_ON_UP
        frame = tk.Frame(win, bg=C_CARD, highlightbackground=bar,
                         highlightthickness=1, bd=0)
        frame.pack(fill="both", expand=True)

        top = tk.Frame(frame, bg=bar, height=30)
        top.pack(fill="x")
        emoji = {"up": "🎉", "plan": "🛒"}.get(kind, "⚠")
        tk.Label(top, text=f"{emoji}  {title}",
                 bg=bar, fg=on_bar,
                 font=(FONT, 10, "bold")).pack(side="left", padx=10, pady=4)
        close = tk.Label(top, text="✕", bg=bar, fg=on_bar,
                         font=(FONT, 9), cursor="hand2")
        close.pack(side="right", padx=8)

        body = tk.Label(frame, text=message, bg=C_CARD, fg=C_TXT,
                        font=(FONT, 10), justify="left", anchor="w")
        body.pack(fill="both", expand=True, padx=12, pady=8)

        self.active.append(win)
        self._relayout(sw, sh)

        def fade_in(i=0):
            try:
                win.attributes("-alpha", min(1.0, i / 8))
            except tk.TclError:
                return
            if i < 8:
                win.after(15, lambda: fade_in(i + 1))

        def dismiss(_=None):
            if win not in self.active:
                return
            try:
                win.destroy()
            except tk.TclError:
                pass
            if win in self.active:
                self.active.remove(win)
            self._relayout(sw, sh)

        close.bind("<Button-1>", dismiss)
        win.bind("<Button-1>", dismiss)
        fade_in()
        win.after(duration_ms, dismiss)

    def _relayout(self, sw, sh):
        for idx, w in enumerate(self.active):
            x = sw - self.WIDTH - self.MARGIN
            y = sh - self.HEIGHT - self.MARGIN - idx * (self.HEIGHT + self.GAP)
            try:
                w.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
            except tk.TclError:
                pass


# ---------- 走势图标签防碰撞布局 ----------

class _ChartLabels:
    """走势图“点 + 文字标签”防碰撞布局。
    - 锚点横向邻近的拥挤标签归为一簇，统一拉到左/右边缘成列、纵向排开，
      用短直角（elbow）引线连回各自的点（金融图惯例）；
    - 稀疏标签就近贴在点旁，一条短引线连点；
    - 布局避让 reserve 预留区（最新价价签、持仓成本标签等）。
    用法：add() 依次加入标签锚点 -> flush() 统一绘制引线、胶囊。"""

    PAD_X, PAD_Y, GAP = 5, 3, 4  # 胶囊内边距 / 标签最小间距
    DOT_R = 6                    # 锚点圆点避让半径（点实际 3.6~4.5）
    EDGE_GAP = 6                 # 就近标签与锚点的间距
    NEAR_DX = 110                # 锚点横向距离小于此值的标签可能归入同一标注列

    def __init__(self, cv, L, R, T, B, reserve=None):
        self.cv, self.L, self.R, self.T, self.B = cv, L, R, T, B
        self.items = []
        self.placed = [tuple(b) for b in (reserve or [])]  # (x1, y1, x2, y2)

    def _measure(self, text, font):
        tid = self.cv.create_text(-999, -999, text=text, font=font)
        x1, y1, x2, y2 = self.cv.bbox(tid)
        self.cv.delete(tid)
        return x2 - x1, y2 - y1

    def add(self, mx, my, text, kind="buy"):
        """kind: buy=买点（橙） / gold=高低点（金）"""
        font = ("Microsoft YaHei UI", 8)
        tw, th = self._measure(text, font)
        w, h = tw + self.PAD_X * 2, th + self.PAD_Y * 2
        self.items.append({"mx": mx, "my": my, "text": text, "kind": kind,
                           "font": font, "w": w, "h": h,
                           "dot": (mx - self.DOT_R, my - self.DOT_R,
                                   mx + self.DOT_R, my + self.DOT_R)})

    @staticmethod
    def _overlap(a, b):
        return not (a[2] <= b[0] + _ChartLabels.GAP or b[2] <= a[0] + _ChartLabels.GAP
                    or a[3] <= b[1] + _ChartLabels.GAP or b[3] <= a[1] + _ChartLabels.GAP)

    def flush(self):
        # 1) 按锚点 x 单链接聚类：横向邻近（< NEAR_DX）的标签归为同一拥挤簇
        order_x = sorted(range(len(self.items)), key=lambda k: self.items[k]["mx"])
        clusters = []  # 每项是成员下标列表
        for k in order_x:
            if clusters and self.items[k]["mx"] - self.items[clusters[-1][-1]]["mx"] < self.NEAR_DX:
                clusters[-1].append(k)
            else:
                clusters.append([k])

        # 2) 成员 >=2 的簇拉到边缘成列；单点簇稍后就近排布
        crowded = [c for c in clusters if len(c) >= 2]
        singles = [c[0] for c in clusters if len(c) == 1]
        for members in sorted(crowded, key=lambda c: -len(c)):
            self._place_column(members)

        # 3) 单点（通常是区间高低点）就近贴点排布，避让已放列与预留区
        for k in sorted(singles,
                        key=lambda j: (self.items[j]["kind"] != "gold", self.items[j]["my"])):
            self._place_near(k)

        self._draw_leaders()
        # 锚点圆点画在引线之上、胶囊之下：落在标签带内的点会被不透明胶囊自然遮住
        for it in self.items:
            r, color = (4.5, "#ff9e4d") if it["kind"] == "buy" else (3.6, C_GOLD)
            mx, my = it["mx"], it["my"]
            self.cv.create_oval(mx - r, my - r, mx + r, my + r,
                                fill=color, outline=C_CHART, width=1.6)
        for it in self.items:
            x1, y1, x2, y2 = it["box"]
            if it["kind"] == "buy":
                fill, outline, tcolor = C_CHART, "#a85f22", "#ffb36b"
            else:
                fill, outline, tcolor = C_AREA, C_GOLD_DIM, C_GOLD_HI
            self.cv.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline)
            self.cv.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=it["text"],
                                fill=tcolor, font=it["font"])

    def _place_column(self, members):
        """拥挤簇：统一拉到左/右边缘成一列，纵向等距排开并避让预留区。"""
        members = sorted(members, key=lambda k: self.items[k]["my"])
        wmax = max(self.items[k]["w"] for k in members)
        total_h = sum(self.items[k]["h"] for k in members) + self.GAP * (len(members) - 1)
        mean_my = sum(self.items[k]["my"] for k in members) / len(members)
        mean_mx = sum(self.items[k]["mx"] for k in members) / len(members)
        side = "r" if mean_mx >= (self.L + self.R) / 2 else "l"

        # 列横向盒：右侧右对齐到 R-2，左侧左对齐到 L+2
        if side == "r":
            cx1, cx2 = self.R - 2 - wmax, self.R - 2
        else:
            cx1, cx2 = self.L + 2, self.L + 2 + wmax

        # 与列横交的预留区转为禁占 y 段
        banned = [(b[1], b[3]) for b in self.placed
                  if b[0] < cx2 - self.GAP and cx1 + self.GAP < b[2]]
        top = min(max(mean_my - total_h / 2, self.T), self.B - total_h)
        top = self._avoid_banned(top, total_h, banned)

        y = top
        boxes = []
        for k in members:
            h = self.items[k]["h"]
            x1 = cx2 - self.items[k]["w"] if side == "r" else cx1
            box = (x1, y, x1 + self.items[k]["w"], y + h)
            boxes.append(box)
            self.items[k]["box"] = box
            self.items[k]["col"] = side
            y += h + self.GAP
        self.placed.extend(boxes)

    def _avoid_banned(self, top, total_h, banned):
        """整列在 [T,B] 内上下平移，避开 banned 纵向禁段；无处可让则维持（可压边界）。"""
        for _ in range(12):
            s, e = top, top + total_h
            hit = next((b for b in banned if s < b[1] - self.GAP and b[0] + self.GAP < e), None)
            if hit is None:
                return top
            up = hit[0] - total_h - self.GAP    # 整体提到禁段之上时的 top
            down = hit[1] + self.GAP            # 整体压到禁段之下时的 top
            cand = []
            if up >= self.T:
                cand.append((abs(up - top), up))
            if down + total_h <= self.B:
                cand.append((abs(down - top), down))
            if not cand:
                return min(max(top, self.T), self.B - total_h)
            top = min(cand)[1]
        return top

    def _place_near(self, k):
        """单点标签：贴在锚点上方（放不下改下方），纵向避让所有障碍。"""
        it = self.items[k]
        w, h, mx, my = it["w"], it["h"], it["mx"], it["my"]
        cx = min(max(mx, self.L + w / 2), self.R - w / 2)
        x1, x2 = cx - w / 2, cx + w / 2
        dots = [o["dot"] for j, o in enumerate(self.items)
                if j != k and "box" not in o]
        blockers = self.placed + dots

        def resolve(y_start, downward):
            top = y_start
            for _ in range(len(blockers) + 3):
                box = (x1, top, x2, top + h)
                hit = next((b for b in blockers if self._overlap(box, b)), None)
                if hit is None:
                    return box
                top = hit[3] + self.GAP if downward else hit[1] - h - self.GAP
            return (x1, top, x2, top + h)

        box = resolve(my - self.EDGE_GAP - h, False)
        if box[1] < self.T:
            box = resolve(my + self.EDGE_GAP, True)
            if box[3] > self.B:
                box = (x1, self.B - h, x2, self.B)
        it["box"] = box
        self.placed.append(box)

    def _draw_leaders(self):
        """引线规则：
        - 拥挤列：列外侧一条竖直“总线”，每个标签用短横挂到总线上（金融图标注惯例），
          绝不穿过同列胶囊；锚点被列胶囊遮住时不回连，其余从列外一侧水平挂到总线；
        - 单点标签：锚点在标签水平跨度内走竖直短线，在外侧走直角两段。
        预留区（成本胶囊/价签）均在列的另一侧，总线与回连线不会穿过它们。"""
        color = {"buy": "#ffb36b", "gold": "#d8bd7e"}
        col_items, singles = [], []
        for it in self.items:
            (col_items if it.get("col") else singles).append(it)
        if col_items:
            self._draw_column_leaders(col_items, color)
        for it in singles:
            self._draw_single_leader(it, color.get(it["kind"], "#ffb36b"))

    @staticmethod
    def _inside(mx, my, box, pad=0):
        x1, y1, x2, y2 = box
        return x1 - pad <= mx <= x2 + pad and y1 - pad <= my <= y2 + pad

    def _draw_column_leaders(self, items, color):
        """同一列共用一条外侧竖直总线；标签短横挂签，锚点仅在列外时水平回连。"""
        for side in ("r", "l"):
            grp = [it for it in items if it.get("col") == side]
            if not grp:
                continue
            # 列盒几何（同列所有胶囊 x 跨度一致）
            if side == "r":
                cx1 = min(it["box"][0] for it in grp)
                bus_x = cx1 - 7  # 列左缘（数据区一侧）留 7px 空槽走总线
            else:
                cx2 = max(it["box"][2] for it in grp)
                bus_x = cx2 + 7
            ys = sorted((b[1] + b[3]) / 2 for b in (it["box"] for it in grp))
            # 竖直总线：贯通最上与最下标签中心
            self.cv.create_line(bus_x, ys[0], bus_x, ys[-1],
                                fill=color["buy"], width=1)
            col_x1 = min(i["box"][0] for i in grp)
            col_x2 = max(i["box"][2] for i in grp)
            col_y1 = min(i["box"][1] for i in grp)
            col_y2 = max(i["box"][3] for i in grp)
            for it in grp:
                x1, y1, x2, y2 = it["box"]
                cy = (y1 + y2) / 2
                lc = color.get(it["kind"], "#ffb36b")
                # 标签短横：总线 → 列缘（标签中心高度）
                self.cv.create_line(bus_x, cy, x1 if side == "r" else x2, cy,
                                    fill=lc, width=1)
                mx, my = it["mx"], it["my"]
                in_x = col_x1 - self.GAP <= mx <= col_x2 + self.GAP
                if in_x and col_y1 - self.GAP <= my <= col_y2 + self.GAP:
                    continue  # 锚点被列胶囊遮住（含同日抖动偏移），无需回连
                if in_x:
                    # 锚点在整列上方/下方露出：先竖直、再水平入总线（穿胶囊段被胶囊遮住）
                    yc = min(max(my, ys[0]), ys[-1])
                    self.cv.create_line(mx, my, mx, yc, bus_x, yc,
                                        fill=lc, width=1)
                elif side == "r" and mx > bus_x or side == "l" and mx < bus_x:
                    # 锚点在总线外侧（右轴/左轴一侧）：水平挂到总线
                    self.cv.create_line(mx, my, bus_x, my, fill=lc, width=1)

    def _draw_single_leader(self, it, line_color):
        x1, y1, x2, y2 = it["box"]
        mx, my = it["mx"], it["my"]
        if self._inside(mx, my, it["box"]):
            return  # 点落在胶囊内，由胶囊遮住
        if x1 - self.GAP <= mx <= x2 + self.GAP:
            # 点在标签水平跨度内：竖直短线连最近边
            edge_y = y2 if my >= y2 else y1
            if abs(edge_y - my) > 2:
                self.cv.create_line(mx, edge_y, mx, my, fill=line_color, width=1)
            return
        # 点在标签外侧：直角两段，连到标签朝向点一侧的近角
        yy = y1 if my < (y1 + y2) / 2 else y2
        ex = x1 if mx < x1 else x2
        self.cv.create_line(mx, my, mx, yy, ex, yy, fill=line_color, width=1)


def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    """Canvas 上的小圆角矩形（多边形近似）。"""
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


# ---------- 主应用 ----------

class GoldMonitorApp:
    def __init__(self):
        self.cfg = load_config()
        apply_theme(self.cfg.get("theme", "dark"))
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("%dx700" % WIN_W)
        self.root.configure(bg=C_BG)
        self._icon = _make_icon_photo()
        if self._icon is not None:
            self.root.iconphoto(True, self._icon)
        apply_dark_style(self.root)
        self.root.after(200, lambda: _dark_title(self.root, THEME_NAME == "dark"))

        self.toasts = ToastManager(self.root)
        self.stop_event = threading.Event()
        self.last_price = self.cfg.get("last_price")  # 启动后首次成功取价会重置基线
        self.baseline_set = False
        self.failed_count = 0
        self._online = False  # 行情实时状态（驱动顶部呼吸点）
        self._latest_sell = None
        self._latest_high = 0.0
        self._latest_low = 0.0
        self._latest_data = {}
        self._trade_date = None  # 当前交易日（由行情接口给出，YYYY-MM-DD）
        self._trades_win = None  # 交易记录窗口（打开期间随行情刷新）
        self._chart_win = None   # 分时走势图窗口（打开期间随行情刷新）
        self._plans_win = None   # 补仓计划窗口
        self._plans_tree = None
        self._pl_win = None      # 每日盈亏日历窗口
        self._pl_cal_date = None # 日历当前展示的月份（date，取1号）
        self._pl_selected = None # 日历选中的日期（YYYY-MM-DD）
        self._hist = load_history()  # 本地分时行情 {交易日: [["HH:MM", 价格], ...]}
        self._hist_dirty = False
        self._last_hist_save = 0.0
        self._settles = load_settles()  # 本地日结价 {交易日: {"price","src"}}
        self._settle_recorded_day = None  # 本次运行已自动记录日结价的交易日（防重）
        # 补仓计划到价状态：{计划价: 是否已弹过提醒}；首个买入价建立基线，
        # 基线价之下的计划默认已触发状态（不打扰），价格涨回去后重新武装
        self._plans_fired = None
        # 在线趋势（东方财富黄金9999）缓存：key -> (数据, 取数时间戳)
        self._em_cache = {}
        self._em_loading = set()  # 正在后台拉取的 key，避免重复请求
        self._chart_seq = 0  # 走势图绘制代际令牌，丢弃切模式前在途的在线回调

        # 财经热搜弹窗（默认不主动拉取，打开弹窗才加载）
        self._news_items = []      # [{"rank","word","url","hot",...}] 排名升序
        self._news_loading = False
        self._news_list_win = None
        self._news_last_fetch = 0.0  # 上次成功拉取时间戳（弹窗内节流）
        self._news_last_ok = ""      # 上次成功拉取的 "HH:MM"（弹窗副标题展示）

        # 微信测试号推送：token 缓存挂在实例上，配置存 config.json
        self._wx_pusher = WeChatPusher()
        self._wx_busy = False        # 测试推送进行中（防连点）

        self._build_ui()
        self._fit_window()
        # 开机自启/隐藏启动时首次几何计算可能偏小，窗口真正显示后再拟合一次
        self.root.after(800, self._fit_window)
        self._build_tray()

        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.root.after(300, self._poll_once)
        self.root.after(1200, self._pulse_dot)

    # ---- UI ----
    def _fit_window(self):
        """窗口高度贴合内容并夹取到屏幕工作区内居中：固定宽 WIN_W，高随内容自适应，
        避免在低分辨率 / 高 DPI 下底部被裁切或留白。
        注意：tk geometry 的宽高即客户区宽高（外框标题栏由系统额外叠加），
        所以高度直接取 winfo_reqheight，不要再加标题栏高度。"""
        root = self.root
        root.update_idletasks()
        w = WIN_W
        content_h = root.winfo_reqheight()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        h = min(content_h, sh - 64)          # 64 给标题栏 + 边距余量
        chrome_h = 40                         # 居中估算用的外框高度（标题栏+边框）
        x = max(0, (sw - w) // 2)
        y = max(8, (sh - h - chrome_h) // 2)
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.minsize(w, h)

    def _pulse_dot(self):
        """行情在线时顶部状态点做心跳式明暗呼吸，取数失败时停在红色。"""
        if self._online:
            self._dot.configure(fg=C_GOLD_HI)
            self.root.after(650, lambda: self._online
                            and self._dot.configure(fg=C_GOLD))
        self.root.after(1900, self._pulse_dot)

    def _build_ui(self):
        PAD = 16  # 统一外边距节奏

        # 整个主界面都挂在 body 上，切换主题时整体销毁重建
        body = tk.Frame(self.root, bg=C_BG)
        body.pack(fill="both", expand=True)
        self.body = body

        # 顶部：深底金线 + 实时呼吸点，不搞大红大紫，主打一个低调搞钱
        header = tk.Frame(body, bg=C_BG, height=68)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Frame(header, bg=C_GOLD, height=2).pack(fill="x", side="top")
        hin = tk.Frame(header, bg=C_BG)
        hin.pack(fill="both", expand=True, padx=PAD)
        title_row = tk.Frame(hin, bg=C_BG)
        title_row.pack(fill="x", pady=(10, 0))
        tk.Label(title_row, text="金价盯盘小助手", bg=C_BG, fg=C_GOLD_HI,
                 font=(FONT, 16, "bold")).pack(side="left")
        # 日/夜主题切换
        self.theme_btn = tk.Button(
            title_row, text=("☀ 浅色" if THEME_NAME == "dark" else "☾ 暗色"),
            font=(FONT, 8), fg=C_GOLD, bg=C_BG, bd=0, cursor="hand2",
            activebackground=C_BG, activeforeground=C_GOLD_HI,
            command=self._toggle_theme)
        self.theme_btn.pack(side="right")
        sub = tk.Frame(hin, bg=C_BG)
        sub.pack(fill="x", pady=(1, 0))
        self._dot = tk.Label(sub, text="●", bg=C_BG, fg=C_TXT_3,
                             font=(FONT, 7))
        self._dot.pack(side="left")
        self.time_var = tk.StringVar(value="等待行情…")
        tk.Label(sub, textvariable=self.time_var, bg=C_BG, fg=C_TXT_2,
                 font=(FONT, 9)).pack(side="left", padx=(5, 0))

        # ---- 价格卡 ----
        card = tk.Frame(body, bg=C_CARD, highlightbackground=C_LINE,
                        highlightthickness=1)
        card.pack(fill="x", padx=PAD, pady=(4, 10))

        toprow = tk.Frame(card, bg=C_CARD)
        toprow.pack(fill="x", padx=18, pady=(14, 0))
        self.name_var = tk.StringVar(value=SOURCES[self.cfg["source"]]["name"])
        tk.Label(toprow, textvariable=self.name_var, bg=C_CARD, fg=C_TXT_2,
                font=(FONT, 9)).pack(side="left")
        # 涨跌徽章：彩色 pill，大数字永远金色，方向交给徽章
        self.chg_var = tk.StringVar(value="")
        self.chg_label = tk.Label(toprow, textvariable=self.chg_var, bg=C_CARD,
                                  fg=C_TXT_2, font=(FONT, 9, "bold"),
                                  padx=0, pady=0)
        self.chg_label.pack(side="right")

        prow = tk.Frame(card, bg=C_CARD)
        prow.pack(fill="x", padx=16, pady=(4, 0))
        self.price_var = tk.StringVar(value="--")
        self.price_label = tk.Label(prow, textvariable=self.price_var, bg=C_CARD,
                                    fg=C_GOLD_HI,
                                    font=(FONT, 34, "bold"))
        self.price_label.pack(side="left")
        tk.Label(prow, text="元/克", bg=C_CARD, fg=C_TXT_3,
                 font=(FONT, 9)).pack(side="left", anchor="s", pady=(0, 6),
                                       padx=(3, 0))

        meta = tk.Frame(card, bg=C_CARD)
        meta.pack(fill="x", padx=18, pady=(5, 14))
        self.hl_var = tk.StringVar(value="最高 --")
        self.ll_var = tk.StringVar(value="最低 --")
        tk.Label(meta, textvariable=self.hl_var, bg=C_CARD, fg=C_TXT_2,
                font=(FONT, 9)).pack(side="left")
        tk.Label(meta, textvariable=self.ll_var, bg=C_CARD, fg=C_TXT_2,
                font=(FONT, 9)).pack(side="left", padx=16)

        # ---- 小金库（按积存金赎回价估值）----
        pos = tk.Frame(body, bg=C_CARD, highlightbackground=C_LINE,
                       highlightthickness=1)
        pos.pack(fill="x", padx=PAD, pady=(0, 10))

        pos_head = tk.Frame(pos, bg=C_CARD)
        pos_head.pack(fill="x", padx=18, pady=(13, 0))
        tk.Frame(pos_head, bg=C_GOLD, width=3, height=14).pack(side="left", padx=(0, 8))
        tk.Label(pos_head, text="我的小金库",
                 bg=C_CARD, fg=C_TXT,
                 font=(FONT, 11, "bold")).pack(side="left")
        self.privacy_btn = tk.Button(
            pos_head, text=("👁 打码" if not self.cfg.get("privacy") else "🙈 亮牌"),
            font=(FONT, 8), fg=C_GOLD, bg=C_CARD, bd=0,
            cursor="hand2", activebackground=C_CARD_2, activeforeground=C_GOLD_HI,
            command=self._toggle_privacy)
        self.privacy_btn.pack(side="right")

        # 工具按钮：3x2 等宽网格，悬停微提亮
        tools = tk.Frame(pos, bg=C_CARD)
        tools.pack(fill="x", padx=14, pady=(8, 0))
        for i in range(3):
            tools.grid_columnconfigure(i, weight=1, uniform="tool")

        def tool_btn(r, c, text, cmd, cspan=1, accent=False):
            b = tk.Button(tools, text=text, font=(FONT, 9, "bold" if accent else "normal"),
                          fg=C_ON_GOLD if accent else C_TXT,
                          bg=C_GOLD if accent else C_CARD_2, bd=0, cursor="hand2",
                          activebackground=C_GOLD_HI if accent else C_ACTIVE,
                          activeforeground=C_ON_GOLD if accent else C_GOLD_HI,
                          relief="flat", padx=4, pady=7, command=cmd)
            b.grid(row=r, column=c, columnspan=cspan, sticky="ew", padx=3, pady=3)
            if not accent:
                b.bind("<Enter>", lambda _=None, w=b: w.configure(bg=C_HOVER))
                b.bind("<Leave>", lambda _=None, w=b: w.configure(bg=C_CARD_2))
            return b

        tool_btn(0, 0, "编辑持仓", self._edit_position)
        tool_btn(0, 1, "加仓小天才", self._avg_down_calculator)
        tool_btn(0, 2, "跑路计算器", self._sell_calculator)
        tool_btn(1, 0, "战绩", self._open_trades)
        tool_btn(1, 1, "韭历", self._open_pl_calendar)
        tool_btn(1, 2, "走势图", self._open_chart)
        tool_btn(2, 0, "财经热搜", self._open_news_list, cspan=3, accent=True)

        # 市值：小标签在上，大数字在下，层级清晰
        tk.Label(pos, text="实时市值", bg=C_CARD, fg=C_TXT_2,
                 font=(FONT, 9)).pack(anchor="w", padx=18, pady=(12, 0))
        mv_row = tk.Frame(pos, bg=C_CARD)
        mv_row.pack(fill="x", padx=16)
        self.pos_mv_var = tk.StringVar(value="--")
        self.pos_mv_label = tk.Label(mv_row, textvariable=self.pos_mv_var, bg=C_CARD,
                                     fg=C_TXT, font=(FONT, 22, "bold"))
        self.pos_mv_label.pack(side="left")
        tk.Label(mv_row, text="元", bg=C_CARD, fg=C_TXT_2,
                 font=(FONT, 10)).pack(side="left", padx=(4, 0), pady=(0, 3),
                                        anchor="s")

        # 持有收益（值单独上色）
        pl_row = tk.Frame(pos, bg=C_CARD)
        pl_row.pack(fill="x", padx=16, pady=(5, 0))
        tk.Label(pl_row, text="持有收益 ", bg=C_CARD, fg=C_TXT_2,
                 font=(FONT, 9)).pack(side="left")
        self.pos_pl_var = tk.StringVar(value="--")
        self.pos_pl_label = tk.Label(pl_row, textvariable=self.pos_pl_var, bg=C_CARD,
                                     fg=C_TXT_2, font=(FONT, 9, "bold"))
        self.pos_pl_label.pack(side="left")
        self.pos_cost_var = tk.StringVar(value="")
        tk.Label(pl_row, textvariable=self.pos_cost_var, bg=C_CARD, fg=C_TXT_3,
                 font=(FONT, 9)).pack(side="left", padx=(8, 0))
        self.pos_pl_pct_var = tk.StringVar(value="")
        tk.Label(pl_row, textvariable=self.pos_pl_pct_var, bg=C_CARD, fg=C_TXT_3,
                 font=(FONT, 9)).pack(side="right")

        # 今日盈亏（值单独上色）
        t_row = tk.Frame(pos, bg=C_CARD)
        t_row.pack(fill="x", padx=16, pady=(3, 0))
        self.today_cap_var = tk.StringVar(value="今日盈亏")
        tk.Label(t_row, textvariable=self.today_cap_var, bg=C_CARD, fg=C_TXT_2,
                 font=(FONT, 9)).pack(side="left")
        self.pos_today_var = tk.StringVar(value="--")
        self.pos_today_label = tk.Label(t_row, textvariable=self.pos_today_var,
                                        bg=C_CARD, fg=C_TXT_2,
                                        font=(FONT, 9, "bold"))
        self.pos_today_label.pack(side="left", padx=(6, 0))

        # 动态吐槽条：左侧彩色边条 + 浅色底，今天是吃面还是加鸡腿
        roast_bar = tk.Frame(pos, bg=C_ROAST_GOLD_BG, highlightthickness=0)
        roast_bar.pack(fill="x", padx=14, pady=(8, 0))
        self.roast_strip = tk.Frame(roast_bar, bg=C_GOLD, width=3)
        self.roast_strip.pack(side="left", fill="y")
        self.roast_var = tk.StringVar(value="")
        self.roast_label = tk.Label(roast_bar, textvariable=self.roast_var,
                                    bg=C_ROAST_GOLD_BG, fg=C_GOLD_HI,
                                    font=(FONT, 9), wraplength=378,
                                    justify="left", anchor="w")
        self.roast_label.pack(side="left", fill="x", expand=True, padx=12, pady=7)

        self.pos_info_var = tk.StringVar(value="")
        tk.Label(pos, textvariable=self.pos_info_var, bg=C_CARD, fg=C_TXT_3,
                 font=(FONT, 8), wraplength=402, justify="left").pack(
            anchor="w", padx=18, pady=(7, 13))

        # ---- 监控设置 ----
        box = tk.Frame(body, bg=C_CARD, highlightbackground=C_LINE,
                       highlightthickness=1)
        box.pack(fill="x", padx=PAD, pady=(0, 10))

        bhead = tk.Frame(box, bg=C_CARD, cursor="hand2")
        bhead.pack(fill="x", padx=18, pady=(13, 7))
        self._settings_arrow_var = tk.StringVar(
            value="▾" if self.cfg.get("settings_open", False) else "▸")
        tk.Label(bhead, textvariable=self._settings_arrow_var, bg=C_CARD,
                 fg=C_GOLD, font=(FONT, 10, "bold")).pack(side="left", padx=(0, 7))
        tk.Label(bhead, text="监控设置", bg=C_CARD, fg=C_TXT,
                 font=(FONT, 10, "bold")).pack(side="left")
        self._settings_hint_var = tk.StringVar(
            value="点击收起" if self.cfg.get("settings_open", False) else "点击展开")
        tk.Label(bhead, textvariable=self._settings_hint_var, bg=C_CARD,
                 fg=C_TXT_3, font=(FONT, 8)).pack(side="right")
        # 标题行任意位置（含子标签）点击均可折叠/展开
        for w in (bhead,) + tuple(bhead.winfo_children()):
            w.bind("<Button-1>", self._toggle_settings)

        # grid/checks 挂在可整体隐藏的 inner 上，折叠时收起、窗口随之变短
        inner = tk.Frame(box, bg=C_CARD)
        self._settings_inner = inner
        if self.cfg.get("settings_open", False):
            inner.pack(fill="x")

        grid = tk.Frame(inner, bg=C_CARD)
        grid.pack(fill="x", padx=16, pady=(4, 4))
        grid.grid_columnconfigure(1, weight=1)

        tk.Label(grid, text="监控品种", bg=C_CARD, fg=C_TXT_2,
                font=(FONT, 9)).grid(row=0, column=0, sticky="w", pady=5)
        self.source_var = tk.StringVar(value=self.cfg["source"])
        names = {k: v["name"] for k, v in SOURCES.items()}
        self.source_combo = ttk.Combobox(grid, textvariable=self.source_var,
                                         values=list(names.values()), state="readonly")
        self.name_to_key = {v: k for k, v in names.items()}
        self.source_combo.set(names[self.cfg["source"]])
        self.source_combo.grid(row=0, column=1, sticky="ew", padx=(12, 0))
        self.source_combo.bind("<<ComboboxSelected>>", self._on_source_change)

        tk.Label(grid, text="档位间隔", bg=C_CARD, fg=C_TXT_2,
                font=(FONT, 9)).grid(row=1, column=0, sticky="w", pady=5)
        self.step_var = tk.StringVar(value=str(self.cfg["step"]))
        ttk.Combobox(grid, textvariable=self.step_var, state="readonly",
                     values=["1", "2", "5", "10", "20", "50"]).grid(
            row=1, column=1, sticky="w", padx=(12, 0))

        tk.Label(grid, text="刷新间隔(秒)", bg=C_CARD, fg=C_TXT_2,
                font=(FONT, 9)).grid(row=2, column=0, sticky="w", pady=5)
        self.interval_var = tk.StringVar(value=str(self.cfg["interval"]))
        ttk.Spinbox(grid, from_=5, to=300, increment=5,
                    textvariable=self.interval_var).grid(
            row=2, column=1, sticky="w", padx=(12, 0))

        checks = tk.Frame(inner, bg=C_CARD)
        checks.pack(fill="x", padx=14, pady=(2, 10))
        self.sound_var = tk.BooleanVar(value=self.cfg["sound"])
        dark_checkbutton(checks, "破位时播放提示音", self.sound_var,
                         bg=C_CARD).pack(anchor="w", pady=2)
        self.alert_up_var = tk.BooleanVar(value=self.cfg.get("alert_up", True))
        dark_checkbutton(checks, "涨破档位也提醒（回本/起飞喊我）", self.alert_up_var,
                         bg=C_CARD,
                         command=lambda: (self.cfg.__setitem__("alert_up", self.alert_up_var.get()),
                                          self._persist())).pack(anchor="w", pady=2)
        self.autostart_var = tk.BooleanVar(value=self.cfg["autostart"])
        dark_checkbutton(checks, "开机自动启动（一起床就看盘）", self.autostart_var,
                         bg=C_CARD, command=self._on_autostart).pack(anchor="w", pady=2)

        # 微信推送：一行显示开关状态 + 配置入口
        wxrow = tk.Frame(inner, bg=C_CARD)
        wxrow.pack(fill="x", padx=14, pady=(6, 4))
        self._wx_state_var = tk.StringVar()
        tk.Label(wxrow, textvariable=self._wx_state_var, bg=C_CARD,
                 fg=C_TXT_2, font=(FONT, 9)).pack(side="left")
        ttk.Button(wxrow, text="微信推送设置",
                   command=self._open_wx_settings).pack(side="right")
        self._refresh_wx_state()

        # 底部操作
        btns = tk.Frame(body, bg=C_BG)
        btns.pack(fill="x", padx=PAD)
        ttk.Button(btns, text="立即刷新", style="Gold.TButton",
                   command=self._poll_once).pack(side="left")
        ttk.Button(btns, text="测试弹窗", command=self._test_alert).pack(side="left", padx=10)
        ttk.Button(btns, text="暗中观察", command=self.hide_window).pack(side="right")

        tk.Frame(body, bg=C_LINE, height=1).pack(fill="x", padx=PAD, pady=(12, 0))
        self.status_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.status_var, bg=C_BG, fg=C_TXT_3,
                font=(FONT, 8), wraplength=430, justify="left").pack(
            anchor="w", padx=PAD, pady=(7, 14))

    # ---- 监控设置折叠 ----
    def _toggle_settings(self, _event=None):
        """监控设置卡片折叠/展开：低频设置收起后窗口明显变短。"""
        opened = not self.cfg.get("settings_open", False)
        self.cfg["settings_open"] = opened
        self._persist()
        self._settings_arrow_var.set("▾" if opened else "▸")
        self._settings_hint_var.set("点击收起" if opened else "点击展开")
        if opened:
            self._settings_inner.pack(fill="x")
        else:
            self._settings_inner.pack_forget()
        self.root.update_idletasks()
        self._fit_window()

    # ---- 财经热搜弹窗（按需打开，平时不占主界面、不后台取数）----
    def _open_news_list(self):
        """财经热搜榜：独立弹窗完整榜单，打开才拉取，弹窗打开期间每 10 分钟自动刷新。"""
        if self._news_list_win is not None:
            try:
                if tk.Toplevel.winfo_exists(self._news_list_win):
                    self._news_list_win.lift()
                    self._news_list_win.focus_force()
                    return
            except Exception:
                pass

        win = tk.Toplevel(self.root)
        self._news_list_win = win
        win.title("财经热搜 · 百度热搜财经榜")
        win.configure(bg=C_BG)
        win.geometry("500x640")
        win.minsize(440, 480)
        win.transient(self.root)

        meta = {"job": None}       # 自动刷新 after 句柄

        def on_close():
            if meta["job"] is not None:
                try:
                    win.after_cancel(meta["job"])
                except Exception:
                    pass
                meta["job"] = None
            self._news_list_win = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", on_close)

        # 顶部金色细线 + 标题行
        tk.Frame(win, bg=C_GOLD, height=2).pack(fill="x")
        head = tk.Frame(win, bg=C_BG)
        head.pack(fill="x", padx=18, pady=(14, 2))
        tk.Label(head, text="财经热搜", bg=C_BG, fg=C_GOLD_HI,
                 font=(FONT, 14, "bold")).pack(side="left")
        refresh_btn = tk.Label(head, text="↻ 刷新", bg=C_BG, fg=C_GOLD,
                               font=(FONT, 9), cursor="hand2")
        refresh_btn.pack(side="right")

        self._news_sub_var = tk.StringVar(value="百度热搜 · 财经榜")
        tk.Label(win, textvariable=self._news_sub_var, bg=C_BG, fg=C_TXT_3,
                 font=(FONT, 8), anchor="w").pack(fill="x", padx=18, pady=(0, 8))

        # 榜单卡：Canvas + 内嵌 Frame 实现滚动
        box = tk.Frame(win, bg=C_CARD, highlightbackground=C_LINE,
                       highlightthickness=1)
        box.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        canvas = tk.Canvas(box, bg=C_CARD, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        rows = tk.Frame(canvas, bg=C_CARD)
        rows_id = canvas.create_window((0, 0), window=rows, anchor="nw")

        def on_rows_configure(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        rows.bind("<Configure>", on_rows_configure)

        def on_canvas_configure(e):
            canvas.itemconfigure(rows_id, width=e.width)
        canvas.bind("<Configure>", on_canvas_configure)

        def on_wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        hint_var = tk.StringVar(value="榜单加载中…")

        def render():
            if not win.winfo_exists():
                return
            for w in rows.winfo_children():
                w.destroy()
            data = self._news_items
            if not data:
                hint_lbl = tk.Label(rows, textvariable=hint_var, bg=C_CARD,
                                    fg=C_TXT_3, font=(FONT, 9), pady=40)
                hint_lbl.pack()
                return
            for n in data:
                self._news_row(rows, n)

        def set_subtitle(note=""):
            if not win.winfo_exists():
                return
            t = "百度热搜 · 财经榜"
            if self._news_last_ok:
                t += " · 更新于 " + self._news_last_ok
            t += " · 每 %d 分钟自动刷新" % (NEWS_REFRESH_SEC // 60)
            if note:
                t += " · " + note
            self._news_sub_var.set(t)

        def loaded(data, err):
            self._news_loading = False
            if not win.winfo_exists():
                return
            refresh_btn.configure(fg=C_GOLD)
            if err is None and data:
                self._news_items = data
                self._news_last_fetch = time.time()
                self._news_last_ok = datetime.now().strftime("%H:%M")
                render()
                set_subtitle()
            elif self._news_items:
                set_subtitle("刷新失败，继续显示旧榜单")
            else:
                hint_var.set("榜单获取失败，点右上角“刷新”重试")
                render()
            try:
                meta["job"] = win.after(NEWS_REFRESH_SEC * 1000,
                                        lambda: self._news_load(True, loaded))
            except Exception:
                pass

        def manual_refresh(_e=None):
            if self._news_loading:
                return
            refresh_btn.configure(fg=C_TXT_3)
            self._news_load(True, loaded)
        refresh_btn.bind("<Button-1>", manual_refresh)
        refresh_btn.bind("<Enter>", lambda _e: refresh_btn.configure(fg=C_GOLD_HI))
        refresh_btn.bind("<Leave>", lambda _e: refresh_btn.configure(fg=C_GOLD))

        # 首次：有 10 分钟内缓存直接用，否则后台拉取
        if self._news_items and time.time() - self._news_last_fetch < NEWS_REFRESH_SEC:
            render()
            set_subtitle()
            try:
                meta["job"] = win.after(NEWS_REFRESH_SEC * 1000,
                                        lambda: self._news_load(True, loaded))
            except Exception:
                pass
        else:
            render()
            self._news_load(True, loaded)

        ttk.Button(win, text="关闭", command=on_close).pack(pady=(2, 14))

    def _news_row(self, parent, n):
        """榜单中的一行：排名徽章 + 标题（热/新/沸标签）+ 热度与升降 + 简介。"""
        row = tk.Frame(parent, bg=C_CARD)
        row.pack(fill="x", padx=14, pady=(8, 0))

        top = tk.Frame(row, bg=C_CARD)
        top.pack(fill="x")
        rank = n.get("rank", 0)
        r_bg = C_GOLD if rank <= 3 else C_CARD_2
        r_fg = C_ON_GOLD if rank <= 3 else C_TXT_2
        tk.Label(top, text=str(rank), bg=r_bg, fg=r_fg, width=2,
                 font=(FONT, 9, "bold"), padx=2, pady=1).pack(side="left",
                                                               anchor="n")
        mid = tk.Frame(top, bg=C_CARD)
        mid.pack(side="left", fill="x", expand=True, padx=(8, 0))
        tline = tk.Frame(mid, bg=C_CARD)
        tline.pack(fill="x")
        has_url = bool(n.get("url"))
        title = tk.Label(tline, text=n["word"], bg=C_CARD,
                         fg=C_GOLD_HI if has_url else C_TXT,
                         font=(FONT, 10, "bold"), anchor="w", justify="left",
                         wraplength=310, cursor="hand2" if has_url else "arrow")
        title.pack(side="left")
        tag_map = {"1": ("新", C_DOWN), "3": ("沸", C_UP)}
        tag_label, tag_fg = tag_map.get(n.get("tag", ""), ("", C_GOLD))
        if tag_label:
            tk.Label(tline, text=tag_label, bg=C_CARD, fg=tag_fg,
                     font=(FONT, 8, "bold"), padx=2).pack(side="left", padx=(4, 0))

        hotline = tk.Frame(top, bg=C_CARD)
        hotline.pack(side="right", anchor="n", padx=(8, 0))
        change = n.get("change", "")
        if change in ("up", "down"):
            arrow, c_fg = ("▲", C_UP) if change == "up" else ("▼", C_DOWN)
            tk.Label(hotline, text=arrow, bg=C_CARD, fg=c_fg,
                     font=(FONT, 8)).pack(side="left")
        hot = _format_hot(n.get("hot"))
        if hot:
            tk.Label(hotline, text=hot, bg=C_CARD, fg=C_TXT_2,
                     font=(FONT, 9, "bold")).pack(side="left", padx=(3, 0))

        desc = n.get("desc", "")
        if desc:
            tk.Label(mid, text=desc, bg=C_CARD, fg=C_TXT_3,
                     font=(FONT, 8), anchor="w", justify="left",
                     wraplength=360).pack(fill="x", pady=(2, 0))

        tk.Frame(parent, bg=C_LINE, height=1).pack(fill="x", padx=14, pady=(8, 0))

        if has_url:
            def open_url(_e=None, u=n["url"]):
                try:
                    webbrowser.open(u)
                except Exception:
                    pass
            for w in (row, top, mid, tline, title):
                w.bind("<Button-1>", open_url)
            title.bind("<Enter>", lambda _e: title.configure(fg=C_GOLD))
            title.bind("<Leave>",
                       lambda _e: title.configure(fg=C_GOLD_HI))

    def _news_load(self, force, callback):
        """后台拉取财经榜；成功/失败均在 UI 线程回调 (data, err)。带 10 分钟节流。"""
        if self._news_loading:
            return
        if not force and self._news_items and \
                time.time() - self._news_last_fetch < NEWS_REFRESH_SEC:
            callback(self._news_items, None)
            return
        self._news_loading = True

        def work():
            data, err = None, None
            try:
                data = fetch_finance_hot()
            except Exception as e:
                err = e

            def ui():
                self._news_loading = False
                if not self.root.winfo_exists():
                    return
                callback(data, err)
            try:
                self.root.after(0, ui)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()


    def _toggle_theme(self):
        new_name = "light" if THEME_NAME == "dark" else "dark"
        self.cfg["theme"] = new_name
        self._persist()
        apply_theme(new_name)
        # 已打开的子窗口仍是旧配色，关掉让其随新主题重建；主界面整体销毁重建
        for win_attr, close_attr in (
                ("_chart_win", "_close_chart"),
                ("_trades_win", "_close_trades"),
                ("_pl_win", "_close_pl_calendar")):
            win = getattr(self, win_attr, None)
            if win is not None:
                closer = getattr(self, close_attr, None)
                if closer is not None:
                    try:
                        closer()
                    except Exception:
                        pass
        # 财经热搜弹窗没有独立 close 方法，直接销毁
        if self._news_list_win is not None:
            try:
                self._news_list_win.destroy()
            except Exception:
                pass
            self._news_list_win = None
        # 微信推送设置窗同理，避免残留旧配色
        if getattr(self, "_wx_win", None) is not None:
            try:
                self._wx_win.destroy()
            except Exception:
                pass
            self._wx_win = None
        apply_dark_style(self.root)
        self.root.configure(bg=C_BG)
        self.body.destroy()
        self._build_ui()
        self._fit_window()
        _dark_title(self.root, THEME_NAME == "dark")
        self._refresh_view_from_cache()

    def _refresh_view_from_cache(self):
        """界面重建后用内存里最近一次行情回填全部主界面数据（不重新取数）。"""
        data = self._latest_data
        if self._online:
            self._dot.configure(fg=C_GOLD)
        else:
            self._dot.configure(fg=C_DOWN)
        if not data:
            return
        key = self.cfg["source"]
        price = data.get(key)
        ts = data.get("time", "")
        self.time_var.set(
            f"更新于 {ts}" + (f"   {data.get('date', '')}" if data.get("date") else ""))
        if data.get("high"):
            self.hl_var.set(f"日内最高 {data['high']:.2f}")
        if data.get("low"):
            self.ll_var.set(f"日内最低 {data['low']:.2f}")
        self._render_position()
        if price is None:
            self.price_var.set("--")
            return
        self.price_var.set(f"{price:.2f}")
        prev = self.last_price
        if prev is None:
            self.chg_label.configure(bg=C_CARD, fg=C_TXT_2, padx=0, pady=0)
        elif price == prev:
            self.chg_var.set("持平")
            self.chg_label.configure(bg=C_CARD, fg=C_TXT_3, padx=0, pady=0)
        else:
            diff = price - prev
            up = diff > 0
            self.chg_var.set(f"{'↑' if up else '↓'} {abs(diff):.2f} 元")
            self.chg_label.configure(bg=C_UP_BG if up else C_DOWN_BG,
                                     fg=C_UP if up else C_DOWN,
                                     padx=8, pady=2)

    def _on_source_change(self, _=None):
        key = self.name_to_key.get(self.source_combo.get())
        if key:
            self.cfg["source"] = key
            self.name_var.set(SOURCES[key]["name"])
            self.baseline_set = False  # 切换品种后重新建立基线，避免误报
            self._persist()
            self._poll_once()

    def _on_autostart(self):
        ok = set_autostart(self.autostart_var.get())
        self.cfg["autostart"] = bool(ok) and self.autostart_var.get()
        if not ok and self.autostart_var.get():
            self.autostart_var.set(False)
            messagebox.showwarning(APP_NAME, "设置开机自启失败（可能需要管理员权限）。")
        self._persist()

    # ---- 我的持仓 ----
    def _toggle_privacy(self):
        self.cfg["privacy"] = not bool(self.cfg.get("privacy"))
        self.privacy_btn.configure(
            text=("👁 打码" if not self.cfg["privacy"] else "🙈 亮牌"))
        self._persist()
        self._render_position()
        if self._trades_win is not None:
            self._refresh_trades()
        if self._chart_win is not None:
            self._draw_chart()

    def _set_trade_date(self, date_str):
        """记录行情接口给出的当前交易日，供本地分时图等使用。"""
        if date_str and date_str != self._trade_date:
            self._trade_date = date_str

    # ---- 每日盈亏：每晚22:20工行结算价 + 交易记录直接重算（不依赖本机常驻） ----
    def _pl_range(self):
        """每日盈亏取数区间：首笔买入前 20 个自然日 ~ 今天（YYYYMMDD）。"""
        trades = self.cfg.get("trades") or []
        dts = [str(t.get("dt", ""))[:10] for t in trades if t.get("dt")]
        start = min(dts) if dts else (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        beg = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=20)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        return beg, end

    def _pl_settle_map(self):
        """本地日结价（工行每晚22:20截止自报价/支付宝截图反推）。
        日结价是银行自主报价、无公开接口，只能本地逐日积累，故不再合并任何在线代理。"""
        return {d: float(v["price"]) for d, v in self._settles.items() if v.get("price")}

    def _pl_klines_sync(self):
        """命中有效缓存返回黄金9999日K（仅作早期估算），否则返回 None（后台拉取）。"""
        cached = self._em_cache.get("em_daily")
        if cached and time.time() - cached[1] < EM_KLINE_TTL:
            return cached[0]
        beg, end = self._pl_range()
        loading = "em_daily" in self._em_loading
        if not loading:
            self._em_loading.add("em_daily")

            def work():
                data, err = None, None
                try:
                    data = fetch_em_daily(beg, end)
                    self._em_cache["em_daily"] = (data, time.time())
                except Exception as e:
                    err = e

                def ui():
                    self._em_loading.discard("em_daily")
                    self._render_position()
                    mode_var = getattr(self, "_chart_mode_var", None)
                    if self._chart_win is not None and mode_var is not None and \
                            self.CHART_MODES.get(mode_var.get()) == "daily":
                        self._draw_chart()
                    if self._pl_win is not None:
                        self._refresh_pl_calendar(err if err is not None and not data else None)
                self.root.after(0, ui)

            threading.Thread(target=work, daemon=True).start()
        return None

    def _daily_rows(self):
        """计算每日盈亏行。精确日结价取本地记录（工行22:20截止自报价/支付宝反推）；
        程序漏记的日期用黄金9999日K近似（标记估算），未就绪时触发后台拉取并返回 None。"""
        settles = self._pl_settle_map()
        if not settles:
            return None
        est = self._pl_klines_sync()
        now = datetime.now()
        now_hm = now.strftime("%H:%M")
        live_today = self._trade_date
        # 实时行仅在工作日交易时段（9:10-22:20）生效，避免周末/盘后陈旧报价冒充实时
        live_sell = None
        if live_today and now.weekday() < 5 and in_trade_session(now_hm):
            live_sell = self._latest_sell
        return daily_pl_rows(
            settles, est, self.cfg.get("trades") or [],
            float(self.cfg.get("hold_grams") or 0),
            live_sell=live_sell, today=live_today, now_hm=now_hm)


    def _edit_position(self):
        win = tk.Toplevel(self.root)
        win.title("编辑我的持仓")
        win.after(60, lambda w=win: _dark_title(w, THEME_NAME == "dark"))
        win.geometry("460x268")
        win.minsize(420, 240)
        win.resizable(True, True)
        win.configure(bg=C_BG)
        win.transient(self.root)
        win.grab_set()
        win.grid_columnconfigure(1, weight=1)

        grams_var = tk.StringVar(value=f"{self.cfg.get('hold_grams') or 0:g}")
        cost_var = tk.StringVar(value=f"{self.cfg.get('hold_cost') or 0:g}")

        def add_row(r, label, var, hint=""):
            tk.Label(win, text=label, bg=C_BG, fg=C_TXT,
                     font=(FONT, 9)).grid(row=r, column=0, sticky="w",
                                          padx=14, pady=(14, 0))
            ttk.Entry(win, textvariable=var, width=16).grid(
                row=r, column=1, sticky="ew", padx=(8, 14), pady=(14, 0))
            if hint:
                hlab = tk.Label(win, text=hint, bg=C_BG, fg=C_TXT_2,
                                font=(FONT, 8), wraplength=410, justify="left",
                                anchor="w")
                hlab.grid(row=r + 1, column=0, columnspan=2, sticky="ew",
                          padx=14, pady=(5, 0))
                win.bind("<Configure>", lambda e, w=hlab:
                         w.configure(wraplength=max(120, e.width - 36)),
                         add="+")

        add_row(0, "持有克数(克)", grams_var)
        add_row(2, "累计投入成本(元)", cost_var,
                "今日/每日盈亏按日K收盘+交易记录自动重算，无需填基准价")

        def on_save():
            try:
                self.cfg["hold_grams"] = max(0.0, float(grams_var.get()))
                self.cfg["hold_cost"] = max(0.0, float(cost_var.get()))
                self._persist()
                win.destroy()
                self._render_position()
                self._refresh_pl_calendar()
            except ValueError:
                messagebox.showwarning("编辑持仓", "请填写有效的数字。", parent=win)

        btns = tk.Frame(win, bg=C_BG)
        btns.grid(row=4, column=0, columnspan=2, pady=18)
        ttk.Button(btns, text="保存", style="Gold.TButton",
                   command=on_save).pack(side="left", padx=8)
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="left")

    # ---- 补仓测算 ----
    def _avg_down_calculator(self):
        win = tk.Toplevel(self.root)
        win.title("加仓小天才 · 回本测算")
        win.after(60, lambda w=win: _dark_title(w, THEME_NAME == "dark"))
        win.geometry("430x486")
        win.resizable(False, False)
        win.configure(bg=C_BG)
        win.transient(self.root)
        win.grab_set()

        grams0 = float(self.cfg.get("hold_grams") or 0)
        cost0 = float(self.cfg.get("hold_cost") or 0)
        buy_px = self._latest_data.get("jicun_active")
        sell_px = self._latest_data.get("jicun_sell")

        tk.Label(win, text="加仓小天才：假设现在立刻补一笔", bg=C_BG, fg=C_GOLD_HI,
                 font=(FONT, 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 4))

        in_box = tk.Frame(win, bg=C_BG)
        in_box.grid(row=1, column=0, columnspan=2, sticky="we", padx=18)
        tk.Label(in_box, text="补仓金额(元)", bg=C_BG, fg=C_TXT_2,
                 font=(FONT, 9)).pack(side="left")
        amt_var = tk.StringVar(value="5000")
        ttk.Entry(in_box, textvariable=amt_var, width=12).pack(side="left", padx=8)
        tk.Label(in_box, text="买入价(元/克)", bg=C_BG, fg=C_TXT_2,
                 font=(FONT, 9)).pack(side="left", padx=(10, 0))
        px_var = tk.StringVar(value=f"{buy_px:g}" if buy_px else "")
        ttk.Entry(in_box, textvariable=px_var, width=9).pack(side="left", padx=8)

        result = tk.Frame(win, bg=C_CARD, highlightbackground=C_LINE,
                          highlightthickness=1)
        result.grid(row=2, column=0, columnspan=2, sticky="we", padx=18, pady=12)
        result.columnconfigure(0, weight=1)
        rvar = tk.StringVar(value="")
        tk.Label(result, textvariable=rvar, bg=C_CARD, fg=C_TXT, justify="left",
                 font=(FONT, 10), wraplength=380, anchor="w").grid(
            row=0, column=0, sticky="w", padx=14, pady=12)

        tk.Label(win, text="※ 回本=赎回价回到摊薄均价；金价未来走势无法预测，\n"
                           "   下方月数为“假设匀速月涨幅”下的理论参考，不构成投资建议。",
                 bg=C_BG, fg=C_TXT_3, justify="left",
                 font=(FONT, 8)).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 6))

        ttk.Button(win, text="开始测算", style="Gold.TButton",
                   command=lambda: calc()).grid(row=4, column=0, pady=8)
        ttk.Button(win, text="关闭", command=win.destroy).grid(row=4, column=1)

        def calc():
            try:
                add_money = float(amt_var.get())
                buy = float(px_var.get())
            except ValueError:
                rvar.set("请填写有效的补仓金额和买入价。")
                return
            if add_money <= 0 or buy <= 0 or grams0 <= 0:
                rvar.set("请先在“编辑”中填写已有克数，并填入有效的补仓金额/买入价。")
                return

            add_grams = add_money / buy
            new_grams = grams0 + add_grams
            new_cost = cost0 + add_money
            avg = new_cost / new_grams
            avg_old = cost0 / grams0
            spread = (buy - sell_px) if sell_px else 0.0  # 买卖价差（点差）

            # 回本目标：赎回价需达到摊薄均价。当前价按买入价。
            cur_sell = sell_px if sell_px else buy
            need_gap = avg - cur_sell
            need_pct = need_gap / cur_sell * 100
            # 浮亏（按当前赎回价估值）
            pl_now = grams0 * cur_sell - cost0

            def months(month_rate):
                if need_gap <= 0:
                    return 0.0
                return math.log(avg / cur_sell) / math.log(1 + month_rate)

            if need_gap <= 0:
                horizon = "按当前赎回价，补仓后已处于回本价位。"
            else:
                parts = []
                for mr in (0.01, 0.02, 0.03, 0.05):
                    m = months(mr)
                    parts.append(f"月涨{mr * 100:g}%：约 {m:.1f} 个月（{m / 12:.1f} 年）")
                horizon = "回本所需时间（理论参考）\n" + "\n".join(parts)

            rvar.set(
                f"本次补仓 {add_money:,.0f} 元 ≈ {add_grams:.4f} 克（买入价 {buy:.2f}）\n"
                f"补仓后总持仓 {new_grams:.4f} 克，总成本 {new_cost:,.2f} 元\n"
                f"摊薄均价 {avg_old:.2f} → {avg:.2f} 元/克（降低 {avg_old - avg:.2f}）\n"
                f"当前赎回价 {cur_sell:.2f}（买卖价差 {spread:.2f}）\n"
                f"需赎回价再涨 {need_gap:.2f} 元（+{need_pct:.2f}%）才回本\n"
                f"补仓前浮盈 {pl_now:,.2f} 元\n"
                f"———————————\n{horizon}")

        if buy_px:
            calc()  # 打开时先用实时价算一次

    # ---- 卖出（赎回）手续费测算 ----
    def _sell_calculator(self):
        win = tk.Toplevel(self.root)
        win.title("跑路计算器 · 赎回手续费")
        win.after(60, lambda w=win: _dark_title(w, THEME_NAME == "dark"))
        win.geometry("430x470")
        win.resizable(False, False)
        win.configure(bg=C_BG)
        win.transient(self.root)
        win.grab_set()

        grams0 = float(self.cfg.get("hold_grams") or 0)
        cost0 = float(self.cfg.get("hold_cost") or 0)
        sell_px = self._latest_data.get("jicun_sell")
        hidden = bool(self.cfg.get("privacy"))

        tk.Label(win, text="跑路计算器：假设现在立刻落袋为安", bg=C_BG, fg=C_GOLD_HI,
                 font=(FONT, 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 4))

        in_box = tk.Frame(win, bg=C_BG)
        in_box.grid(row=1, column=0, columnspan=2, sticky="we", padx=18)
        tk.Label(in_box, text="卖出克数(克)", bg=C_BG, fg=C_TXT_2,
                 font=(FONT, 9)).pack(side="left")
        g_var = tk.StringVar(value=(f"{grams0:g}" if grams0 else ""))
        ttk.Entry(in_box, textvariable=g_var, width=11).pack(side="left", padx=8)
        tk.Label(in_box, text="赎回价(元/克)", bg=C_BG, fg=C_TXT_2,
                 font=(FONT, 9)).pack(side="left", padx=(10, 0))
        px_var = tk.StringVar(value=f"{sell_px:g}" if sell_px else "")
        ttk.Entry(in_box, textvariable=px_var, width=9).pack(side="left", padx=8)

        result = tk.Frame(win, bg=C_CARD, highlightbackground=C_LINE,
                          highlightthickness=1)
        result.grid(row=2, column=0, columnspan=2, sticky="we", padx=18, pady=12)
        result.columnconfigure(0, weight=1)
        rvar = tk.StringVar(value="")
        tk.Label(result, textvariable=rvar, bg=C_CARD, fg=C_TXT, justify="left",
                 font=(FONT, 10), wraplength=380, anchor="w").grid(
            row=0, column=0, sticky="w", padx=14, pady=12)

        tk.Label(win, text="※ 赎回手续费=赎回金额×0.5%（工行个人积存金标准），\n"
                           "   按赎回当日实时赎回价成交，资金一般 T+5 个工作日到账；\n"
                           "   兑换实物金条不按此收费。实际以工行办理时为准。",
                 bg=C_BG, fg=C_TXT_3, justify="left",
                 font=(FONT, 8)).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 6))

        ttk.Button(win, text="开始测算", style="Gold.TButton",
                   command=lambda: calc()).grid(row=4, column=0, pady=8)
        ttk.Button(win, text="关闭", command=win.destroy).grid(row=4, column=1)

        def calc():
            try:
                g = float(g_var.get())
                p = float(px_var.get())
            except ValueError:
                rvar.set("请填写有效的卖出克数和赎回价。")
                return
            if g <= 0 or p <= 0:
                rvar.set("请填写有效的卖出克数和赎回价。")
                return
            if grams0 <= 0:
                rvar.set("请先在“编辑”中填写持仓克数。")
                return
            if g > grams0 + 1e-9:
                rvar.set(f"卖出克数不能超过当前持仓（{grams0:.4f} 克）。")
                return

            redeem = g * p                       # 赎回金额（扣费前）
            fee = redeem * REDEEM_FEE_RATE       # 赎回手续费 0.5%
            arrived = redeem - fee               # 实际到账
            cost_part = cost0 * g / grams0       # 按比例分摊的对应成本
            pl = arrived - cost_part             # 净盈亏（扣费后）
            fee_eff = fee / g                    # 折算每克手续费

            def mg(s):
                return "****" if hidden else s

            lines = [
                f"卖出 {mg(f'{g:.4f}')} 克 × 赎回价 {p:.2f}",
                f"赎回金额（扣费前）：{mg(f'{redeem:,.2f}')} 元",
                f"手续费（0.5%）：{mg(f'{fee:,.2f}')} 元（约 {mg(f'{fee_eff:.2f}')} 元/克）",
                f"实际到账：{mg(f'{arrived:,.2f}')} 元",
                f"对应成本：{mg(f'{cost_part:,.2f}')} 元",
                f"扣费后净盈亏：{mg(f'{pl:,.2f}')} 元",
            ]
            if g < grams0 - 1e-9:
                rest_g = grams0 - g
                rest_cost = cost0 - cost_part
                lines.append("———————————")
                lines.append(f"本次为部分卖出，剩余 {mg(f'{rest_g:.4f}')} 克，"
                             f"剩余成本 {mg(f'{rest_cost:,.2f}')} 元")
            rvar.set("\n".join(lines))

        if sell_px:
            calc()  # 打开时先用实时赎回价算一次

    # ---- 交易记录（逐笔盈亏）----
    def _open_trades(self):
        if self._trades_win is not None and tk.Toplevel.winfo_exists(self._trades_win):
            self._trades_win.lift()
            self._trades_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        win.title("我的战绩 · 逐笔补仓盈亏")
        win.after(60, lambda w=win: _dark_title(w, THEME_NAME == "dark"))
        win.geometry("600x540")
        win.minsize(560, 460)
        win.configure(bg=C_BG)
        win.transient(self.root)

        tk.Label(win, text="我的战绩 · 按实时赎回价计算浮动盈亏", bg=C_BG, fg=C_GOLD_HI,
                font=(FONT, 11, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        self._trades_tip_var = tk.StringVar(value="")
        tk.Label(win, textvariable=self._trades_tip_var, bg=C_BG, fg=C_TXT_2,
                font=(FONT, 8), justify="left").pack(anchor="w", padx=16)

        cols = ("dt", "amount", "grams", "price", "pl", "pct")
        headers = ("买入时间", "金额(元)", "克数(克)", "买入价", "浮动盈亏(元)", "涨跌幅")
        widths = (130, 80, 85, 75, 105, 75)
        frame = tk.Frame(win, bg=C_CARD, highlightbackground=C_LINE, highlightthickness=1)
        frame.pack(fill="both", expand=True, padx=16, pady=10)
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=11, selectmode="browse")
        for c, h, w in zip(cols, headers, widths):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w" if c == "dt" else "e")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        tree.tag_configure("plus", foreground=C_UP)
        tree.tag_configure("minus", foreground=C_DOWN)
        tree.tag_configure("base", background=C_BASE_BG)
        tree.tag_configure("total", background=C_TOTAL_BG)
        self._trades_tree = tree

        btns = tk.Frame(win, bg=C_BG)
        btns.pack(fill="x", padx=16, pady=(0, 12))
        ttk.Button(btns, text="记一笔补仓", style="Gold.TButton",
                   command=self._add_trade).pack(side="left")
        ttk.Button(btns, text="补仓计划价",
                   command=self._open_plans).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="删除选中", command=self._delete_trade).pack(side="left", padx=8)
        ttk.Button(btns, text="关闭", command=self._close_trades).pack(side="right")

        self._trades_win = win
        win.protocol("WM_DELETE_WINDOW", self._close_trades)
        self._refresh_trades()

    def _refresh_trades(self):
        win = self._trades_win
        if win is None or not tk.Toplevel.winfo_exists(win):
            return
        tree = self._trades_tree
        prev_sel = tree.selection()  # 定时刷新会重建整表，先记下选中行，重建后恢复
        tree.delete(*tree.get_children())
        sell = self._latest_sell
        trades = self.cfg.get("trades") or []

        hidden = bool(self.cfg.get("privacy"))
        detail_g = detail_a = detail_pl = 0.0
        for i, t in enumerate(trades):
            amt = float(t.get("amount") or 0)
            g = float(t.get("grams") or 0)
            px = amt / g if g else 0.0
            detail_g += g
            detail_a += amt
            if hidden:
                vals = (t.get("dt", ""), "****", "****", "****", "****", "****")
                tag = ()
            elif sell:
                pl = (sell - px) * g
                detail_pl += pl
                vals = (t.get("dt", ""), f"{amt:,.0f}", f"{g:.4f}", f"{px:.2f}",
                        f"{pl:+,.2f}", f"{(sell / px - 1) * 100:+.2f}%")
                tag = ("plus",) if pl >= 0 else ("minus",)
            else:
                vals = (t.get("dt", ""), f"{amt:,.0f}", f"{g:.4f}", f"{px:.2f}", "--", "--")
                tag = ()
            tree.insert("", "end", iid=f"t{i}", values=vals, tags=tag)

        # 底仓：总持仓减去补仓明细，倒挤出更早的买入
        grams0 = float(self.cfg.get("hold_grams") or 0)
        cost0 = float(self.cfg.get("hold_cost") or 0)
        base_g, base_a = grams0 - detail_g, cost0 - detail_a
        total_g, total_a, total_pl = detail_g, detail_a, detail_pl
        if abs(base_g) > 0.0001 or abs(base_a) > 0.01:
            if base_g < -0.0001 or base_a < -0.01:
                tree.insert("", "end", iid="base",
                            values=("底仓（异常：明细超出总持仓）",
                                    "****" if hidden else f"{base_a:,.0f}",
                                    "****" if hidden else f"{base_g:.4f}",
                                    "--", "请核对持仓" if not hidden else "****", ""),
                            tags=("minus", "base"))
            else:
                bpx = base_a / base_g if base_g > 0.0001 else 0.0
                if hidden:
                    vals = ("底仓（更早买入 · 倒挤）", "****", "****", "****", "****", "****")
                    tags = ("base",)
                elif sell:
                    bpl = (sell - bpx) * base_g
                    total_pl += bpl
                    vals = ("底仓（更早买入 · 倒挤）", f"{base_a:,.0f}", f"{base_g:.4f}",
                            f"{bpx:.2f}", f"{bpl:+,.2f}", f"{(sell / bpx - 1) * 100:+.2f}%")
                    tags = ("plus", "base") if bpl >= 0 else ("minus", "base")
                else:
                    vals = ("底仓（更早买入 · 倒挤）", f"{base_a:,.0f}", f"{base_g:.4f}",
                            f"{bpx:.2f}", "--", "--")
                    tags = ("base",)
                tree.insert("", "end", iid="base", values=vals, tags=tags)
                total_g += base_g
                total_a += base_a

        # 合计
        if hidden:
            vals = ("合计", "****", "****", "****", "****", "****")
            tags = ("total",)
        elif sell:
            vals = ("合计", f"{total_a:,.0f}", f"{total_g:.4f}",
                    f"{total_a / total_g:.2f}" if total_g else "--",
                    f"{total_pl:+,.2f}", f"{total_pl / total_a * 100:+.2f}%")
            tags = ("plus", "total") if total_pl >= 0 else ("minus", "total")
        else:
            vals = ("合计", f"{total_a:,.0f}", f"{total_g:.4f}",
                    f"{total_a / total_g:.2f}" if total_g else "--", "--", "")
            tags = ("total",)
        tree.insert("", "end", iid="total", values=vals, tags=tags)

        if sell:
            self._trades_tip_var.set(
                f"实时赎回价 {sell:.2f} 元/克｜红盈绿亏｜买入价按“金额÷克数”倒算，与App成交单价或有小幅差异")
        else:
            self._trades_tip_var.set(
                "暂无实时赎回价（非交易时段），浮盈显示 --｜买入价按“金额÷克数”倒算")

        # 重建后恢复此前选中（仅当该行仍存在），避免轮询刷新导致“删除选中”失效
        for sid in prev_sel:
            if tree.exists(sid):
                tree.selection_add(sid)

    def _close_trades(self):
        win = self._trades_win
        self._trades_win = None
        self._trades_tree = None
        self._trades_tip_var = None
        if win is not None:
            win.destroy()

    # ---- 每日盈亏日历（仿支付宝：月历每格显示当日盈亏，红涨绿跌，点格看明细） ----
    def _open_pl_calendar(self):
        if self._pl_win is not None and tk.Toplevel.winfo_exists(self._pl_win):
            self._pl_win.lift()
            self._pl_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        win.title("韭历 · 每日盈亏")
        win.after(60, lambda w=win: _dark_title(w, THEME_NAME == "dark"))
        win.geometry("430x560")
        win.minsize(400, 520)
        win.configure(bg=C_BG)
        win.transient(self.root)

        # 顶部：月份切换
        head = tk.Frame(win, bg=C_BG)
        head.pack(fill="x", padx=16, pady=(14, 2))
        self._pl_prev_btn = tk.Button(head, text="‹", font=(FONT, 14, "bold"),
                                      fg=C_GOLD, bg=C_BG, bd=0, cursor="hand2",
                                      activebackground=C_CARD_2, activeforeground=C_GOLD_HI,
                                      command=self._pl_prev_month)
        self._pl_prev_btn.pack(side="left")
        self._pl_title_var = tk.StringVar()
        tk.Label(head, textvariable=self._pl_title_var, bg=C_BG, fg=C_TXT,
                font=(FONT, 13, "bold")).pack(side="left", padx=14)
        self._pl_next_btn = tk.Button(head, text="›", font=(FONT, 14, "bold"),
                                      fg=C_GOLD, bg=C_BG, bd=0, cursor="hand2",
                                      activebackground=C_CARD_2, activeforeground=C_GOLD_HI,
                                      command=self._pl_next_month)
        self._pl_next_btn.pack(side="left")

        # 本月合计
        self._pl_month_var = tk.StringVar()
        self._pl_month_label = tk.Label(win, textvariable=self._pl_month_var,
                                        bg=C_BG, fg=C_TXT_2,
                                        font=(FONT, 10, "bold"))
        self._pl_month_label.pack(anchor="w", padx=18, pady=(2, 6))

        # 星期表头
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
        wd = tk.Frame(win, bg=C_BG)
        wd.pack(fill="x", padx=14)
        for i, name in enumerate(weekday_names):
            tk.Label(wd, text=name, bg=C_BG, fg=C_TXT_3,
                    font=(FONT, 8)).grid(row=0, column=i, sticky="nsew",
                                                        padx=2, pady=2)
            wd.grid_columnconfigure(i, weight=1, uniform="pl")

        # 日期网格容器（每月重建）
        self._pl_grid = tk.Frame(win, bg=C_BG)
        self._pl_grid.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        for i in range(7):
            self._pl_grid.grid_columnconfigure(i, weight=1, uniform="pl")

        # 选中日明细
        detail_box = tk.Frame(win, bg=C_CARD, highlightbackground=C_LINE,
                              highlightthickness=1)
        detail_box.pack(fill="x", padx=14, pady=(2, 4))
        self._pl_detail_var = tk.StringVar()
        tk.Label(detail_box, textvariable=self._pl_detail_var, bg=C_CARD, fg=C_TXT,
                font=(FONT, 9), justify="left", anchor="w",
                wraplength=380).pack(anchor="w", padx=10, pady=8)

        tk.Label(win, text="每日盈亏与支付宝同口径：按每晚 22:20 交易截止时工行积存金"
                          "报价结算（程序 22:20 在线即自动锁定；该价为银行以 Au(T+D)/"
                          "境外价为参考的自报价，无公开接口；程序未运行而漏记的日期"
                          "用黄金9999日K近似并标“估”）；当日盘中按实时赎回价。"
                          "每日盈亏=(昨日存量+当日买入)×(今收−昨收)，当日买入即计入、"
                          "统一按上一交易日日结价起算；买入价只影响成本，不参与当日盈亏。",
                bg=C_BG, fg=C_TXT_3, font=(FONT, 7),
                justify="left", wraplength=390).pack(anchor="w", padx=16, pady=(0, 10))

        # 默认展示当月（当月无交易日则定位到最近一个有数据的月份）
        now = datetime.now()
        self._pl_cal_date = now.replace(day=1)
        self._pl_selected = now.strftime("%Y-%m-%d")
        self._pl_win = win
        win.protocol("WM_DELETE_WINDOW", self._close_pl_calendar)
        self._refresh_pl_calendar()

    def _pl_prev_month(self):
        d = self._pl_cal_date
        self._pl_cal_date = (d.replace(day=1) - timedelta(days=1)).replace(day=1)
        self._refresh_pl_calendar()

    def _pl_next_month(self):
        d = self._pl_cal_date
        self._pl_cal_date = (d.replace(day=28) + timedelta(days=10)).replace(day=1)
        self._refresh_pl_calendar()

    def _pl_select_day(self, ds):
        self._pl_selected = ds
        self._refresh_pl_calendar()

    def _refresh_pl_calendar(self, fetch_error=None):
        win = self._pl_win
        if win is None or not tk.Toplevel.winfo_exists(win):
            return
        hidden = bool(self.cfg.get("privacy"))
        cur = self._pl_cal_date or datetime.now().replace(day=1)
        rows = self._daily_rows()  # 未就绪会触发后台拉取，完成后再次刷新

        self._pl_title_var.set(f"{cur.year} 年 {cur.month} 月")
        # 月份导航边界：不晚于当前月；不早于首笔买入所在月的上一个月
        trades = self.cfg.get("trades") or []
        dts = sorted(str(t.get("dt", ""))[:10] for t in trades if t.get("dt"))
        min_d = datetime.strptime(dts[0], "%Y-%m-%d").replace(day=1) if dts \
            else (datetime.now() - timedelta(days=180)).replace(day=1)
        now_first = datetime.now().replace(day=1)
        self._pl_prev_btn.configure(state="normal" if cur > min_d else "disabled")
        # 下个月按钮在 _build_grid 后根据“下月是否已有任何交易日”再定，这里先按当前月封顶
        for child in self._pl_grid.winfo_children():
            child.destroy()

        if rows is None:
            msg = "日结价加载中…" if not fetch_error else f"行情获取失败：{fetch_error}\n将随下次刷新自动重试"
            tk.Label(self._pl_grid, text=msg, bg=C_BG, fg=C_TXT_2,
                    font=(FONT, 10), justify="center").grid(
                row=0, column=0, columnspan=7, pady=40)
            self._pl_month_var.set("本月盈亏 …")
            self._pl_detail_var.set("正在获取黄金9999日K估算价…")
            return

        # 本月盈亏合计（仅日历区间内的交易日）
        month_rows = {d: r for d, r in rows.items()
                      if d.startswith(cur.strftime("%Y-%m"))}
        month_sum = sum(r["pl"] for r in month_rows.values())
        n_trade = len(month_rows)
        if hidden:
            self._pl_month_var.set(f"本月（{n_trade} 个交易日）盈亏 ****")
        elif n_trade:
            sgn = "+" if month_sum >= 0 else ""
            color = C_UP if month_sum >= 0 else C_DOWN
            self._pl_month_var.set(
                f"本月（{n_trade} 个交易日）盈亏  {sgn}{month_sum:,.2f} 元")
            self._pl_month_label.configure(fg=color)
        else:
            self._pl_month_var.set("本月暂无交易日")
            self._pl_month_label.configure(fg=C_TXT_2)

        # 下个月是否有交易日数据：有则允许前进；封顶当前月
        nxt = (cur.replace(day=28) + timedelta(days=10)).replace(day=1)
        has_next = nxt <= now_first or any(d.startswith(nxt.strftime("%Y-%m")) for d in rows)
        self._pl_next_btn.configure(state="normal" if has_next else "disabled")

        today_str = datetime.now().strftime("%Y-%m-%d")
        cal = calendar.Calendar(firstweekday=0)  # 周一起
        weeks = cal.monthdatescalendar(cur.year, cur.month)
        for wi, week in enumerate(weeks):
            for di, daydate in enumerate(week):
                ds = daydate.strftime("%Y-%m-%d")
                in_month = daydate.month == cur.month
                info = rows.get(ds)
                cell = self._pl_day_cell(self._pl_grid, ds, daydate.day, info,
                                         in_month, hidden, ds == today_str,
                                         ds == self._pl_selected)
                cell.grid(row=wi, column=di, sticky="nsew", padx=2, pady=2)
            self._pl_grid.grid_rowconfigure(wi, weight=1)

        self._render_pl_detail(rows, hidden, today_str)

    def _pl_day_cell(self, parent, ds, daynum, info, in_month, hidden, is_today, selected):
        bg = C_CARD if in_month else C_CHART
        fg_day = C_TXT_3 if not in_month else C_TXT_2
        if info is not None and in_month:
            if info["pl"] > 0.005:
                pl_color = C_UP
            elif info["pl"] < -0.005:
                pl_color = C_DOWN
            else:
                pl_color = C_TXT_2
        else:
            pl_color = C_TXT_3
        if selected and in_month:
            bg = C_SEL
        cell = tk.Frame(parent, bg=bg, highlightbackground=(
                        C_GOLD if selected else C_LINE),
                        highlightthickness=(1.5 if selected else 1), cursor="hand2")
        head_txt = str(daynum) + (" 今" if is_today and in_month else "")
        head = tk.Frame(cell, bg=bg)
        head.pack(fill="x", padx=5, pady=(3, 0))
        tk.Label(head, text=head_txt, bg=bg, fg=(C_GOLD_HI if is_today and in_month else fg_day),
                font=(FONT, 8, "bold" if is_today else "normal")).pack(side="left")
        if info is not None and info.get("est"):
            tk.Label(head, text="估", bg=bg, fg=C_GOLD_DIM,
                    font=(FONT, 7, "bold")).pack(side="right")
        if in_month:
            if info is None:
                tk.Label(cell, text="—", bg=bg, fg=C_TXT_3,
                        font=(FONT, 8)).pack(anchor="e", padx=5, pady=(0, 3))
            elif hidden:
                tk.Label(cell, text="****", bg=bg, fg=pl_color,
                        font=(FONT, 8, "bold")).pack(
                    anchor="e", padx=5, pady=(0, 3))
            else:
                tk.Label(cell, text=f"{info['pl']:+,.0f}", bg=bg, fg=pl_color,
                        font=(FONT, 9, "bold")).pack(
                    anchor="e", padx=5, pady=(0, 3))
            for w in cell.winfo_children():
                w.bind("<Button-1>", lambda e, d=ds: self._pl_select_day(d))
            cell.bind("<Button-1>", lambda e, d=ds: self._pl_select_day(d))
        return cell

    def _render_pl_detail(self, rows, hidden, today_str):
        ds = self._pl_selected
        info = rows.get(ds) if ds else None
        if info is None:
            self._pl_detail_var.set(
                f"{ds or '未选择日期'}：非交易日或无盈亏数据。点上方有数字的日期查看明细。")
            return
        weekday = "一二三四五六日"[datetime.strptime(ds, "%Y-%m-%d").weekday()]
        if info["is_today"] or ds == today_str:
            tag = "（今日实时）"
        elif info.get("est"):
            tag = "（估算·黄金9999日K近似）"
        else:
            tag = "（收盘·22:20 工行结算价）"
        chg = info["mark"] - info["prev"]
        hold_pl = info["grams_open"] * chg
        # 支付宝口径：当日买入即计入结算持仓，与存量统一按上一交易日日结价起算
        buy_pl = info["buy_g"] * chg
        if hidden:
            self._pl_detail_var.set(
                f"{ds} 周{weekday}{tag}\n"
                f"昨收 {info['prev']:.2f} → 今值 {info['mark']:.2f}（{chg:+.2f}）｜"
                f"存量 {info['grams_open']:.4f} 克\n"
                f"存量盈亏 ****｜当日买入盈亏 ****｜合计盈亏 ****")
            return
        lines = [
            f"{ds} 周{weekday}{tag}",
            f"昨收 {info['prev']:.2f} → 今值 {info['mark']:.2f}（{chg:+.2f} 元/克）"
            f"｜昨日存量 {info['grams_open']:.4f} 克",
            f"存量盈亏 {hold_pl:+,.2f} 元",
        ]
        if info["buy_g"] > 0:
            px = info["buy_cost"] / info["buy_g"]
            lines.append(
                f"当日买入 {info['buy_g']:.4f} 克 @ {px:.2f}（{info['buy_cost']:,.0f} 元）"
                f"｜买入计入盈亏 {buy_pl:+,.2f} 元（按上一交易日日结价起算）")
        sgn = "+" if info["pl"] >= 0 else ""
        lines.append(f"当日合计盈亏 {sgn}{info['pl']:,.2f} 元")
        if info.get("est"):
            lines.append("注：该日无 22:20 结算价记录（程序当时未运行），按黄金9999日收盘价近似。")
        self._pl_detail_var.set("\n".join(lines))

    def _close_pl_calendar(self):
        win = self._pl_win
        self._pl_win = None
        if win is not None:
            win.destroy()

    # ---- 分时走势图（仿支付宝：金色分时线 + 高低点 + 买入点位） ----
    def _open_chart(self):
        if self._chart_win is not None and tk.Toplevel.winfo_exists(self._chart_win):
            self._chart_win.lift()
            self._chart_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        win.title("金价走势 · 买点地图")
        win.after(60, lambda w=win: _dark_title(w, THEME_NAME == "dark"))
        win.geometry("900x540")
        win.minsize(780, 480)
        win.configure(bg=C_BG)
        win.transient(self.root)

        top = tk.Frame(win, bg=C_BG)
        top.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(top, text="模式", bg=C_BG, fg=C_TXT_2,
                 font=(FONT, 9)).pack(side="left")
        self._chart_mode_var = tk.StringVar(value="今日分时（在线）")
        self._chart_mode_combo = ttk.Combobox(
            top, textvariable=self._chart_mode_var, width=16, state="readonly",
            values=["今日分时（在线）", "历史日K（在线）", "本地采集分时"])
        self._chart_mode_combo.pack(side="left", padx=(6, 14))
        self._chart_mode_combo.bind("<<ComboboxSelected>>",
                                    lambda _=None: self._on_chart_mode())
        tk.Label(top, text="交易日", bg=C_BG, fg=C_TXT_2,
                 font=(FONT, 9)).pack(side="left")
        days = sorted(self._hist.keys(), reverse=True)
        today = self._trade_date or (days[0] if days else "")
        if today and today not in days:
            days.insert(0, today)
        self._chart_day_var = tk.StringVar(value=days[0] if days else "")
        self._chart_combo = ttk.Combobox(
            top, textvariable=self._chart_day_var, values=days,
            state="disabled", width=12)
        self._chart_combo.pack(side="left", padx=(6, 14))
        self._chart_combo.bind("<<ComboboxSelected>>", lambda _=None: self._draw_chart())
        self._chart_cost_var = tk.BooleanVar(value=True)
        self._chart_cost_cb = dark_checkbutton(
            top, "显示持仓成本线", self._chart_cost_var,
            command=self._draw_chart)
        self._chart_cost_cb.pack(side="left")
        self._chart_refresh_btn = tk.Button(
            top, text="重新取数", font=(FONT, 8),
            relief="flat", bg=C_CARD_2, fg=C_TXT, bd=0, padx=8, pady=3,
            activebackground=C_ACTIVE, activeforeground=C_GOLD_HI,
            command=self._reload_chart)
        self._chart_refresh_btn.pack(side="right")

        chart_frame = tk.Frame(win, bg=C_CHART,
                               highlightbackground=C_LINE, highlightthickness=1)
        chart_frame.pack(fill="both", expand=True, padx=14, pady=(4, 6))
        self._chart_canvas = tk.Canvas(chart_frame, bg=C_CHART,
                                       highlightthickness=0, height=400)
        self._chart_canvas.pack(fill="both", expand=True)
        self._chart_canvas.bind("<Configure>", lambda _=None: self._draw_chart())
        self._chart_canvas.bind("<Motion>", self._on_chart_motion)
        self._chart_canvas.bind("<Leave>", self._on_chart_leave)
        # 十字光标命中点数据（由各渲染函数写入：(x, y, 时间标签, 价格) 列表）
        self._chart_pts = None
        self._chart_cross = None  # 十字光标的画布 item id 集合

        self._chart_tip_var = tk.StringVar(value="")
        tk.Label(win, textvariable=self._chart_tip_var, bg=C_BG, fg=C_TXT_3,
                 font=(FONT, 8), justify="left",
                 wraplength=760).pack(anchor="w", padx=16, pady=(0, 10))

        self._chart_win = win
        win.protocol("WM_DELETE_WINDOW", self._close_chart)
        self._draw_chart()

    def _close_chart(self):
        win = self._chart_win
        self._chart_win = None
        self._chart_canvas = None
        self._chart_combo = None
        self._chart_day_var = None
        self._chart_cost_var = None
        self._chart_cost_cb = None
        self._chart_mode_var = None
        self._chart_mode_combo = None
        self._chart_refresh_btn = None
        self._chart_tip_var = None
        self._chart_pts = None
        self._chart_geom = None
        self._chart_tip_base = ""
        if win is not None:
            win.destroy()

    @staticmethod
    def _hm_to_min(hm):
        h, m = hm.split(":")[:2]
        return int(h) * 60 + int(m)

    @staticmethod
    def _cv_dot(cv, x, y, r=4.5, color="#ff9e4d"):
        """买点 / 端点圆点：实心 + 画布色描边。（勿与状态灯 self._dot 同名）"""
        cv.create_oval(x - r, y - r, x + r, y + r,
                       fill=color, outline=C_CHART, width=1.6)

    def _on_chart_leave(self, _=None):
        """鼠标离开画布：清除十字光标与底部读数。"""
        self._clear_crosshair()
        if self._chart_tip_var is not None:
            self._chart_tip_var.set(self._chart_tip_base or "")

    def _clear_crosshair(self):
        cv = self._chart_canvas
        if cv is not None:
            cv.delete("crosshair")
        self._chart_cross = None

    def _on_chart_motion(self, ev):
        """十字光标：横向吸附到最近的数据点，纵向跟随鼠标。"""
        cv = self._chart_canvas
        pts = self._chart_pts
        geom = self._chart_geom
        if cv is None or not pts or not geom:
            return
        L, R, T, B = geom
        if not (L - 4 <= ev.x <= R + 4 and T - 4 <= ev.y <= B + 4):
            self._clear_crosshair()
            return
        i = min(range(len(pts)), key=lambda k: abs(pts[k][0] - ev.x))
        px, py, lab, price = pts[i]
        self._clear_crosshair()
        items = []
        # 两条虚线
        items.append(cv.create_line(px, T, px, B, fill=C_REF_DASH,
                                    dash=(3, 3), tags="crosshair"))
        items.append(cv.create_line(L, ev.y, R, ev.y, fill=C_REF_DASH,
                                    dash=(3, 3), tags="crosshair"))
        # 吸附点高亮
        items.append(cv.create_oval(px - 4, py - 4, px + 4, py + 4,
                                    fill=C_GOLD_HI, outline=C_CHART, width=1.4,
                                    tags="crosshair"))
        # 右轴浮动价签（纵向跟随鼠标，显示吸附点价格）
        tag_w = 52
        ty = min(max(ev.y, T + 9), B - 9)
        cv.create_rectangle(R + 2, ty - 9, R + tag_w, ty + 9,
                            fill=C_GOLD, outline="", tags="crosshair")
        items.append(cv.create_text(R + tag_w / 2 - 2, ty,
                                    text=f"{price:.2f}", fill=C_ON_GOLD,
                                    font=(FONT, 8, "bold"), tags="crosshair"))
        # 底部时间气泡
        tw = max(58, 7 * len(lab) + 12)
        tx = min(max(px, L + tw / 2), R - tw / 2)
        cv.create_rectangle(tx - tw / 2, B + 16, tx + tw / 2, B + 32,
                            fill=C_CARD_2, outline=C_LINE, tags="crosshair")
        items.append(cv.create_text(tx, B + 24, text=lab, fill=C_TXT,
                                    font=(FONT, 8), tags="crosshair"))
        self._chart_cross = items
        # 底部提示行同步读数
        if self._chart_tip_var is not None:
            self._chart_tip_var.set(
                f"{lab}    价格 {price:.2f} 元/克｜{self._chart_tip_base or ''}")

    # ---- 走势图（三模式：在线分时 / 在线历史日K / 本地采集分时） ----
    CHART_MODES = {"今日分时（在线）": "today",
                   "历史日K（在线）": "daily",
                   "本地采集分时": "local"}

    def _on_chart_mode(self):
        mode = self.CHART_MODES.get(self._chart_mode_var.get(), "today")
        # 交易日下拉仅在本地采集模式下使用
        self._chart_combo.configure(state="readonly" if mode == "local" else "disabled")
        self._draw_chart()

    def _reload_chart(self):
        self._em_cache.clear()  # 强制重新拉取在线行情
        self._draw_chart()

    def _draw_chart(self):
        win = self._chart_win
        if win is None or not tk.Toplevel.winfo_exists(win):
            return
        cv = self._chart_canvas
        cv.delete("all")
        cv.update_idletasks()
        self._chart_seq += 1  # 代际令牌：使切模式前在途的在线回调失效
        self._chart_pts = None  # 十字光标命中点随重绘失效
        self._chart_cross = None
        self._chart_tip_base = ""
        w = max(cv.winfo_width(), 700)
        h = max(cv.winfo_height(), 360)
        L, R, T, B = 18, w - 78, 46, h - 38
        self._chart_geom = (L, R, T, B)
        mode = self.CHART_MODES.get(self._chart_mode_var.get(), "today")
        if mode == "today":
            self._draw_em_trend(cv, L, R, T, B)
        elif mode == "daily":
            self._draw_em_daily(cv, L, R, T, B)
        else:
            self._draw_local(cv, L, R, T, B)

    def _ensure_em(self, key, ttl, loader, on_done, on_wait):
        """在线行情缓存 + 后台线程拉取。命中缓存直接 on_done；拉取中/失败调 on_wait。"""
        seq = self._chart_seq  # 本次绘制的代际，回调时若已切模式则丢弃
        cached = self._em_cache.get(key)
        if cached and time.time() - cached[1] < ttl:
            on_done(cached[0])
            return
        if key in self._em_loading:
            on_wait(cached[0] if cached else None, None)
            return
        self._em_loading.add(key)

        def work():
            data, err = None, None
            try:
                data = loader()
                self._em_cache[key] = (data, time.time())
            except Exception as e:  # 网络/解析失败，界面提示可重试
                err = str(e)

            def ui():
                self._em_loading.discard(key)
                if self._chart_win is None or not tk.Toplevel.winfo_exists(self._chart_win):
                    return
                if self._chart_seq != seq:  # 期间已切换模式/重绘，本次结果作废
                    return
                # 清掉同步阶段画的“正在加载…”，避免失败/成功文案与之叠印
                # （各 on_done/on_wait 均自绘网格整框，可安全全清）
                if self._chart_canvas is not None:
                    self._chart_canvas.delete("all")
                if err is not None:
                    on_wait(cached[0] if cached else None, err)
                else:
                    on_done(data)
            self.root.after(0, ui)

        threading.Thread(target=work, daemon=True).start()
        on_wait(cached[0] if cached else None, None)

    def _chart_message(self, cv, L, R, T, B, text, tip=None):
        """网格底 + 居中提示（加载中/失败/无数据）。"""
        for i in range(5):
            y = T + (B - T) * i / 4
            cv.create_line(L, y, R, y, fill=C_GRID)
        cv.create_text((L + R) / 2, (T + B) / 2, text=text, fill=C_TXT_2,
                       font=(FONT, 10), justify="center")
        if tip is not None:
            self._chart_tip_var.set(tip)

    def _draw_chart_frame(self, cv, L, R, T, B, y_min, y_max, vlines=None,
                          skip_y=None):
        """纵网格 + 横网格 + 右轴价格刻度。skip_y 处的刻度留给最新价价签。"""
        for x, lab in (vlines or []):
            cv.create_line(x, T, x, B, fill=C_GRID_AX)
            cv.create_text(x, B + 10, text=lab, fill=C_TXT_3,
                           font=(FONT, 8))
        for i in range(5):
            y = T + (B - T) * i / 4
            cv.create_line(L, y, R, y, fill=C_GRID)
            p = y_max - (y_max - y_min) * i / 4
            if skip_y is not None and abs(y - skip_y) < 12:
                continue
            cv.create_text(R + 10, y, text=f"{p:.2f}", fill=C_TXT_3,
                           font=(FONT, 8), anchor="w")
        cv.create_line(R, T, R, B, fill=C_LINE)

    def _avg_cost(self):
        """当前持仓加权平均成本（元/克）；无持仓返回 None。"""
        grams = float(self.cfg.get("hold_grams") or 0)
        cost = float(self.cfg.get("hold_cost") or 0)
        if grams > 0 and cost > 0:
            return cost / grams
        ta = tg = 0.0
        for t in self.cfg.get("trades") or []:
            ta += float(t.get("amount") or 0)
            tg += float(t.get("grams") or 0)
        return ta / tg if tg > 0 else None

    def _avg_cost_line(self, cv, L, R, y_of, avg):
        """单条持仓平均成本虚线（克制呈现，替代原先每笔一条）。"""
        yc = y_of(avg)
        cv.create_line(L, yc, R, yc, fill=C_COST_LINE, dash=(5, 4), width=1)
        txt = f"持仓成本 {avg:.2f}"
        tid = cv.create_text(L + 6, yc - 6, anchor="sw", text=txt,
                             fill=C_COST, font=(FONT, 8))
        x1, y1, x2, y2 = cv.bbox(tid)
        rid = cv.create_rectangle(x1 - 5, y1 - 2, x2 + 5, y2 + 2,
                                  fill=C_CHART, outline="#a85f22")
        cv.tag_lower(rid, tid)
        return (x1 - 5, y1 - 2, x2 + 5, y2 + 2)

    def _plan_lines(self, cv, L, R, y_of, y_min, y_max, hidden):
        """补仓计划价蓝色虚线（含标签避让框），返回标签占位矩形列表。"""
        if hidden:
            return []
        boxes = []
        plans = sorted((float(p.get("price") or 0) for p in (self.cfg.get("plans") or [])),
                       reverse=True)
        prev_y = None
        for target in plans:
            if target <= 0 or not (y_min <= target <= y_max):
                continue
            yp = y_of(target)
            cv.create_line(L, yp, R, yp, fill=C_PLAN_LINE, dash=(2, 3), width=1)
            txt = f"补仓计划 {target:g}"
            # 与上一条计划标签错开，避免重叠
            ty = yp - 6 if (prev_y is None or abs(prev_y - yp) > 26) else yp + 14
            prev_y = ty
            tid = cv.create_text(L + 6, ty, anchor="sw", text=txt,
                                 fill=C_PLAN, font=(FONT, 8))
            x1, y1, x2, y2 = cv.bbox(tid)
            rid = cv.create_rectangle(x1 - 5, y1 - 2, x2 + 5, y2 + 2,
                                      fill=C_CHART, outline=C_PLAN_LINE)
            cv.tag_lower(rid, tid)
            boxes.append((x1 - 5, y1 - 2, x2 + 5, y2 + 2))
        return boxes

    def _price_tag(self, cv, R, x, y, price, color):
        """最新价：曲线端点圆点 + 细引线 + 右轴区圆角实心价签（同时覆盖该处刻度）。"""
        self._cv_dot(cv, x, y, r=4.5, color=color)
        if x < R - 6:
            cv.create_line(x + 5, y, R - 2, y, fill=color, width=1)
        tag_w = 52
        _round_rect(cv, R + 2, y - 9, R + tag_w, y + 9, 5, fill=color, outline="")
        tag_fg = C_ON_GOLD if color in (C_GOLD, "#c9a04e") else "#ffffff"  # 红绿价签两主题下均为深底白字
        cv.create_text(R + tag_w / 2 - 2, y, text=f"{price:.2f}",
                       fill=tag_fg, font=(FONT, 8, "bold"))

    # ---------- 在线：黄金9999 当日分时 ----------
    def _draw_em_trend(self, cv, L, R, T, B):
        hidden = bool(self.cfg.get("privacy"))

        def on_done(d):
            self._render_em_trend(cv, L, R, T, B, d, hidden)

        def on_wait(data, error):
            if data is not None:
                self._render_em_trend(cv, L, R, T, B, data, hidden)
                return
            if error:
                self._chart_message(
                    cv, L, R, T, B,
                    f"在线行情获取失败\n{error}\n可点右上角“刷新”重试，或切换“本地采集分时”")
            else:
                self._chart_message(cv, L, R, T, B,
                                    "正在从东方财富获取黄金9999分时…")

        self._ensure_em("em_trend", EM_TRENDS_TTL, fetch_em_trends, on_done, on_wait)

    def _render_em_trend(self, cv, L, R, T, B, d, hidden):
        all_pts = d.get("points") or []
        # 东财返回的“交易日”跨自然日（含前一夜盘20:00-次日2:30，周一还含周五夜盘）。
        # 这里只画本自然日：优先取最后一个含白盘(>=09:00)的自然日；
        # 纯夜盘时段（无白盘点）则取数据最后一天。
        day_days = sorted({ts[:10] for ts, _ in all_pts if ts[11:16] >= "09:00"})
        any_days = sorted({ts[:10] for ts, _ in all_pts})
        date = day_days[-1] if day_days else (any_days[-1] if any_days else "")
        # 积存金交易时段对应的上金所盘口：白盘 9:10-15:30、夜盘 20:00-22:20；
        # 午间休市（11:30-13:30）与盘间休市（15:30-20:00）按点序等距压缩
        pts = [[ts[11:16], p] for ts, p in all_pts
               if ts[:10] == date
               and ("08:59" <= ts[11:16] <= "15:31"
                    or "19:59" <= ts[11:16] <= "22:20")]
        if len(pts) < 2:
            self._chart_message(
                cv, L, R, T, B,
                "当前自然日暂无盘口分时（休市）\n黄金9999：白盘 9:00-15:30 / 夜盘 20:00-次日2:30",
                tip="在线分时由东方财富黄金9999（Au99.99）提供，周末/非交易时段无分时；"
                    "历史走势请切“历史日K（在线）”，可回看 2020 年至今。")
            return
        n = len(pts)

        try:
            base = float(d.get("pre_close")) if d.get("pre_close") else None
        except (TypeError, ValueError):
            base = None
        prices = [p for _, p in pts]
        y_lo, y_hi = min(prices), max(prices)
        if base:
            y_lo, y_hi = min(y_lo, base), max(y_hi, base)
        if not hidden:
            for _p in (float(x.get("price") or 0) for x in (self.cfg.get("plans") or [])):
                if _p > 0:
                    y_lo, y_hi = min(y_lo, _p), max(y_hi, _p)
        pad = max((y_hi - y_lo) * 0.15, 0.3)
        y_min, y_max = y_lo - pad, y_hi + pad

        def x_of(i):
            return L + (R - L) * (i / (n - 1) if n > 1 else 0)

        def y_of(p):
            return T + (y_max - p) / (y_max - y_min) * (B - T)

        # 时间刻度：首点/末点 + 就近的 11:30/13:30/15:30/20:00（覆盖白盘+夜盘缺口）
        def nearest_idx(tgt):
            tm = self._hm_to_min(tgt)
            return min(range(n), key=lambda i: abs(self._hm_to_min(pts[i][0]) - tm))

        ticks = [(0, pts[0][0]), (n - 1, pts[-1][0])]
        for tgt in ("11:30", "13:30", "15:30", "20:00"):
            i = nearest_idx(tgt)
            if abs(self._hm_to_min(pts[i][0]) - self._hm_to_min(tgt)) <= 20:
                ticks.append((i, pts[i][0]))
        ticks = sorted(set(ticks))
        last_p = pts[-1][1]
        self._draw_chart_frame(cv, L, R, T, B, y_min, y_max,
                               [(x_of(i), lab) for i, lab in ticks],
                               skip_y=y_of(last_p))

        # 顶部信息行
        pmax, pmin = max(prices), min(prices)
        chg_txt = ""
        if base:
            chg = last_p - base
            chg_txt = f"   较昨收 {'+' if chg >= 0 else ''}{chg:.2f}（{(last_p / base - 1) * 100:+.2f}%）"
        cv.create_text(L, 12, anchor="nw", fill=C_GOLD,
                       font=(FONT, 10, "bold"),
                       text=f"黄金9999（Au99.99）在线分时  {date}"
                            f"    最新 {last_p:.2f}   高 {pmax:.2f}   低 {pmin:.2f}{chg_txt}")

        # 金色分时线 + 浅色填充
        coords = []
        for i, (_, p) in enumerate(pts):
            coords.extend([x_of(i), y_of(p)])
        cv.create_polygon(coords + [coords[-2], B, coords[0], B],
                          fill=C_AREA, outline="")
        cv.create_line(coords, fill=C_GOLD, width=1.8)

        # 基准线画在填充之上，保证可读
        if base and y_min <= base <= y_max:
            yb = y_of(base)
            cv.create_line(L, yb, R, yb, fill=C_REF_DASH, dash=(4, 3))
            cv.create_text(L + 4, yb - 5, text=f"昨收 {base:.2f}", fill=C_TXT_3,
                           font=(FONT, 8), anchor="sw")
        cost_box = None
        avg = self._avg_cost()
        if not hidden and self._chart_cost_var.get() and avg and y_min <= avg <= y_max:
            cost_box = self._avg_cost_line(cv, L, R, y_of, avg)
        plan_boxes = self._plan_lines(cv, L, R, y_of, y_min, y_max, hidden)

        # 标签统一防碰撞布局（高低点 + 买点）；右轴价签区/成本标签预留避让
        tag_y = y_of(last_p)
        reserve = [(R - 6, tag_y - 12, R + 56, tag_y + 12)]
        if cost_box:
            reserve.append(cost_box)
        reserve.extend(plan_boxes)
        labels = _ChartLabels(cv, L, R, T, B, reserve=reserve)
        hi_idx = max(range(n), key=lambda i: pts[i][1])
        lo_idx = min(range(n), key=lambda i: pts[i][1])
        hx, hy = x_of(hi_idx), y_of(pts[hi_idx][1])
        lx, ly = x_of(lo_idx), y_of(pts[lo_idx][1])
        if hi_idx != n - 1:
            labels.add(hx, hy, f"{pts[hi_idx][1]:.2f}", kind="gold")
        if lo_idx != hi_idx and lo_idx != n - 1:
            labels.add(lx, ly, f"{pts[lo_idx][1]:.2f}", kind="gold")

        if not hidden:
            day_trades = sorted(
                [t for t in (self.cfg.get("trades") or [])
                 if str(t.get("dt", ""))[:10] == date],
                key=lambda x: x.get("dt", ""))
            for t in day_trades:
                amt, g = float(t.get("amount") or 0), float(t.get("grams") or 0)
                if g <= 0:
                    continue
                px = amt / g
                thm = str(t.get("dt", ""))[11:16]
                before = [i for i in range(n) if pts[i][0] <= thm]
                mi = before[-1] if before else 0
                labels.add(x_of(mi), y_of(pts[mi][1]), f"买 {px:.2f} {thm}")
        labels.flush()

        # 最新价价签
        color = C_UP if (base and last_p >= base) else (C_DOWN if base else C_GOLD)
        self._price_tag(cv, R, x_of(n - 1), y_of(last_p), last_p, color)

        if hidden:
            self._chart_tip_base = (
                "在线分时：东方财富黄金9999（与积存金同源，点差通常 0~1 元/克）；"
                "隐私模式下买入点位与成本线已隐藏。")
        else:
            self._chart_tip_base = (
                "金色曲线=东方财富黄金9999在线分时（无需程序常开）｜橙点=当日买入成交价"
                "｜橙色虚线=持仓成本｜蓝色虚线=补仓计划价（鼠标移动可看任意时点价格）")
        self._chart_pts = [(x_of(i), y_of(p), f"{date} {hm}", p)
                           for i, (hm, p) in enumerate(pts)]
        self._chart_tip_var.set(self._chart_tip_base)

    # ---------- 在线：黄金9999 历史日K（收盘折线 + 全部买点） ----------
    def _draw_em_daily(self, cv, L, R, T, B):
        hidden = bool(self.cfg.get("privacy"))
        beg, end = self._pl_range()

        def loader():
            return fetch_em_daily(beg, end)

        def on_done(kl):
            self._render_em_daily(cv, L, R, T, B, kl, hidden)

        def on_wait(data, error):
            if data is not None:
                self._render_em_daily(cv, L, R, T, B, data, hidden)
                return
            if error:
                self._chart_message(
                    cv, L, R, T, B,
                    f"历史日K获取失败\n{error}\n可点右上角“刷新”重试，或切换“本地采集分时”")
            else:
                self._chart_message(cv, L, R, T, B, "正在从东方财富获取黄金9999历史日K…")

        self._ensure_em("em_daily", EM_KLINE_TTL, loader, on_done, on_wait)

    def _render_em_daily(self, cv, L, R, T, B, kl, hidden):
        if not kl:
            self._chart_message(cv, L, R, T, B, "暂无历史日K数据")
            return
        n = len(kl)
        closes = [r[2] for r in kl]
        y_lo, y_hi = min(closes), max(closes)
        if not hidden:
            for _p in (float(x.get("price") or 0) for x in (self.cfg.get("plans") or [])):
                if _p > 0:
                    y_lo, y_hi = min(y_lo, _p), max(y_hi, _p)
        pad = max((y_hi - y_lo) * 0.08, 0.5)
        y_min, y_max = y_lo - pad, y_hi + pad

        def x_of(i):
            return L + (R - L) * (i / (n - 1) if n > 1 else 0)

        def y_of(p):
            return T + (y_max - p) / (y_max - y_min) * (B - T)

        # 约 6 个日期刻度
        tick_idxs = sorted({int(round(i * (n - 1) / 5)) for i in range(6)})
        vlines = [(x_of(i), kl[i][0][5:10]) for i in tick_idxs]
        last_c = closes[-1]
        self._draw_chart_frame(cv, L, R, T, B, y_min, y_max, vlines,
                               skip_y=y_of(last_c))

        # 顶部信息行
        cv.create_text(L, 12, anchor="nw", fill=C_GOLD,
                       font=(FONT, 10, "bold"),
                       text=f"黄金9999（Au99.99）历史日K  {kl[0][0]} ~ {kl[-1][0]}"
                            f"    {n} 个交易日    最新 {last_c:.2f}")

        # 金色收盘折线 + 浅色填充
        coords = []
        for i, c in enumerate(closes):
            coords.extend([x_of(i), y_of(c)])
        cv.create_polygon(coords + [coords[-2], B, coords[0], B],
                          fill=C_AREA, outline="")
        cv.create_line(coords, fill=C_GOLD, width=1.8)

        # 持仓平均成本线画在填充之上
        cost_box = None
        avg = self._avg_cost()
        if not hidden and self._chart_cost_var.get() and avg and y_min <= avg <= y_max:
            cost_box = self._avg_cost_line(cv, L, R, y_of, avg)
        plan_boxes = self._plan_lines(cv, L, R, y_of, y_min, y_max, hidden)

        # 标签统一防碰撞布局（区间高低点 + 全部买点）；右轴价签区/成本标签预留避让
        tag_y = y_of(last_c)
        reserve = [(R - 6, tag_y - 12, R + 56, tag_y + 12)]
        if cost_box:
            reserve.append(cost_box)
        reserve.extend(plan_boxes)
        labels = _ChartLabels(cv, L, R, T, B, reserve=reserve)
        hi_idx = max(range(n), key=lambda i: closes[i])
        lo_idx = min(range(n), key=lambda i: closes[i])
        hx, hy = x_of(hi_idx), y_of(closes[hi_idx])
        lx, ly = x_of(lo_idx), y_of(closes[lo_idx])
        if hi_idx != n - 1:
            labels.add(hx, hy, f"{closes[hi_idx]:.2f}", kind="gold")
        if lo_idx != hi_idx and lo_idx != n - 1:
            labels.add(lx, ly, f"{closes[lo_idx]:.2f}", kind="gold")

        if not hidden:
            all_dates = [r[0] for r in kl]
            date_to_i = {d: i for i, d in enumerate(all_dates)}

            def day_index(ds):
                if ds in date_to_i:
                    return date_to_i[ds]
                before = [i for i, d in enumerate(all_dates) if d <= ds]
                return before[-1] if before else 0

            day_rank = {}
            day_count = {}
            for t in (self.cfg.get("trades") or []):
                ds = str(t.get("dt", ""))[:10]
                day_count[ds] = day_count.get(ds, 0) + 1
            for t in sorted(self.cfg.get("trades") or [], key=lambda x: x.get("dt", "")):
                amt, g = float(t.get("amount") or 0), float(t.get("grams") or 0)
                if g <= 0:
                    continue
                px = amt / g
                dt = str(t.get("dt", ""))
                ds, dshort = dt[:10], dt[5:10]
                di = day_index(ds)
                rank = len(day_rank.setdefault(ds, []))
                day_rank[ds].append(t)
                jitter = (rank - (day_count.get(ds, 1) - 1) / 2) * 8
                labels.add(x_of(di) + jitter, y_of(closes[di]),
                           f"买 {px:.2f}  {dshort}")
        labels.flush()

        # 最新价价签（较前一交易日涨跌着色）
        if n >= 2:
            color = C_UP if last_c >= closes[-2] else C_DOWN
        else:
            color = C_GOLD
        self._price_tag(cv, R, x_of(n - 1), y_of(last_c), last_c, color)

        if hidden:
            self._chart_tip_base = (
                "历史日K：东方财富黄金9999收盘价（2020年至今可查）；隐私模式下买入点位已隐藏。")
        else:
            self._chart_tip_base = (
                "金色曲线=东方财富黄金9999日收盘价｜橙点=每笔买入成交价（定位到成交当日，节假日顺延），"
                "标签自动避让、引线连到买点｜橙色虚线=持仓成本｜蓝色虚线=补仓计划价"
                "（鼠标移动可看任意时点收盘价）")
        self._chart_pts = [(x_of(i), y_of(r[2]), r[0], r[2])
                           for i, r in enumerate(kl)]
        self._chart_tip_var.set(self._chart_tip_base)

    # ---------- 本地采集分时（程序运行期间累积，兜底） ----------
    def _draw_local(self, cv, L, R, T, B):
        selected = self._chart_day_var.get() if self._chart_day_var else ""
        pts = list(self._hist.get(selected, []))
        # 当日：把内存中最新价也补进曲线（尚未到下一分钟落盘时）
        latest_day = self._trade_date
        if selected == latest_day and self._latest_sell:
            now_hm = datetime.now().strftime("%H:%M")
            if in_trade_session(now_hm):
                if pts and pts[-1][0] == now_hm:
                    pts[-1][1] = float(self._latest_sell)
                elif not pts or pts[-1][0] < now_hm:
                    pts.append([now_hm, float(self._latest_sell)])
        is_today = bool(selected and selected == latest_day)
        hidden = bool(self.cfg.get("privacy"))

        # 顶部信息行
        if pts:
            pmax = max(p for _, p in pts)
            pmin = min(p for _, p in pts)
            last_p = pts[-1][1]
            base = None  # 昨收基准：上一交易日22:20工行结算价（缺口日为9999日K近似）
            _rows = self._daily_rows()
            if _rows and selected in _rows:
                base = _rows[selected]["prev"]
            chg_txt = ""
            if base:
                chg = last_p - base
                chg_txt = f"   较昨收 {'+' if chg >= 0 else ''}{chg:.2f}（{(last_p / base - 1) * 100:+.2f}%）"
            cv.create_text(L, 12, anchor="nw", fill=C_GOLD,
                           font=(FONT, 10, "bold"),
                           text=f"工银积存金 · 赎回价  {selected}"
                                f"    最新 {last_p:.2f}   高 {pmax:.2f}   低 {pmin:.2f}{chg_txt}")

        # 时间轴：09:10 / 13:00 / 18:00 / 22:20
        total = trade_minutes()

        def x_of(hm):
            tm = self._hm_to_min(hm) - (9 * 60 + 10)
            return L + min(max(tm / total, 0), 1) * (R - L)

        vlines = [(x_of(t), t) for t in ("09:10", "13:00", "18:00", "22:20")]

        if len(pts) < 1:
            self._draw_chart_frame(cv, L, R, T, B, 0, 1, vlines)
            cv.create_text((L + R) / 2, (T + B) / 2,
                           text="该交易日暂无本地分时数据\n可切换“今日分时（在线）”直接查看，无需程序常开",
                           fill=C_TXT_2, font=(FONT, 10), justify="center")
            self._chart_tip_var.set(
                "本地分时需程序运行期间按分钟采集；在线模式（今日分时/历史日K）由东方财富提供，"
                "随时可看、无需常驻。")
            days = sorted(set(list(self._hist.keys()) + ([latest_day] if latest_day else [])),
                          reverse=True)
            self._chart_combo.configure(values=days)
            return

        # y 轴范围（纳入昨收基准与当日计划价；历史成本线不纳入，保持走势本身的缩放）
        prices = [p for _, p in pts]
        y_lo, y_hi = min(prices), max(prices)
        if base:
            y_lo, y_hi = min(y_lo, base), max(y_hi, base)
        if is_today and not hidden:
            for _p in (float(x.get("price") or 0) for x in (self.cfg.get("plans") or [])):
                if _p > 0:
                    y_lo, y_hi = min(y_lo, _p), max(y_hi, _p)
        pad = max((y_hi - y_lo) * 0.15, 0.3)
        y_min, y_max = y_lo - pad, y_hi + pad

        def y_of(p):
            return T + (y_max - p) / (y_max - y_min) * (B - T)

        lhm, lpv = pts[-1]
        self._draw_chart_frame(cv, L, R, T, B, y_min, y_max, vlines,
                               skip_y=y_of(lpv))
        # 金色分时线 + 浅色填充
        coords = []
        for hm, p in pts:
            coords.extend([x_of(hm), y_of(p)])
        fill_pts = coords + [coords[-2], B, coords[0], B]
        cv.create_polygon(fill_pts, fill=C_AREA, outline="")
        cv.create_line(coords, fill=C_GOLD, width=1.8)

        # 基准线画在填充之上
        if base and y_min <= base <= y_max:
            yb = y_of(base)
            cv.create_line(L, yb, R, yb, fill=C_REF_DASH, dash=(4, 3))
            cv.create_text(L + 4, yb - 5, text=f"昨收 {base:.2f}", fill=C_TXT_3,
                           font=(FONT, 8), anchor="sw")
        cost_box = None
        avg = self._avg_cost()
        if is_today and not hidden and self._chart_cost_var.get() \
                and avg and y_min <= avg <= y_max:
            cost_box = self._avg_cost_line(cv, L, R, y_of, avg)
        plan_boxes = self._plan_lines(cv, L, R, y_of, y_min, y_max,
                                      hidden or not is_today)

        # 标签统一防碰撞布局（高低点 + 买点）；右轴价签区/成本标签预留避让
        tag_y = y_of(lpv)
        reserve = [(R - 6, tag_y - 12, R + 56, tag_y + 12)]
        if cost_box:
            reserve.append(cost_box)
        reserve.extend(plan_boxes)
        labels = _ChartLabels(cv, L, R, T, B, reserve=reserve)
        hi_idx = max(range(len(pts)), key=lambda i: pts[i][1])
        lo_idx = min(range(len(pts)), key=lambda i: pts[i][1])
        hx, hy = x_of(pts[hi_idx][0]), y_of(pts[hi_idx][1])
        lx, ly = x_of(pts[lo_idx][0]), y_of(pts[lo_idx][1])
        last_i = len(pts) - 1
        if hi_idx != last_i:
            labels.add(hx, hy, f"{pts[hi_idx][1]:.2f}", kind="gold")
        if lo_idx != hi_idx and lo_idx != last_i:
            labels.add(lx, ly, f"{pts[lo_idx][1]:.2f}", kind="gold")

        today_trades = [t for t in (self.cfg.get("trades") or [])
                        if str(t.get("dt", "")).startswith(selected)]
        if not hidden:
            for t in sorted(today_trades, key=lambda x: x.get("dt", "")):
                amt, g = float(t.get("amount") or 0), float(t.get("grams") or 0)
                if g <= 0:
                    continue
                px = amt / g
                thm = str(t.get("dt", ""))[11:16]
                # 点位取成交时刻最近的本地采集价；没有本地数据时回退买入价
                near = [p for hm, p in pts if hm <= thm]
                mp = near[-1] if near else px
                mp = min(max(mp, y_min), y_max)
                labels.add(x_of(thm), y_of(mp), f"买 {px:.2f} {thm}")
        labels.flush()

        # 最新价圆点与右侧价签
        up_color = C_UP if (base and lpv >= base) else (C_DOWN if base else C_GOLD)
        self._price_tag(cv, R, x_of(lhm), y_of(lpv), lpv, up_color)

        # 日期下拉补充新出现的交易日，保留当前选择
        days = sorted(set(list(self._hist.keys()) + ([latest_day] if latest_day else [])),
                      reverse=True)
        cur_vals = list(self._chart_combo.cget("values"))
        if cur_vals != tuple(days) and cur_vals != days:
            self._chart_combo.configure(values=days)

        if hidden:
            self._chart_tip_base = (
                "金色曲线为本地采集的实时赎回价（公开行情）；隐私模式下买入点位与成本线已隐藏。")
        else:
            self._chart_tip_base = (
                "金色曲线=本地按分钟采集的赎回价（仅程序运行期间有数据，建议用在线模式）"
                "｜橙点=当日买入成交价，标签自动避让｜橙色虚线=持仓成本"
                "｜蓝色虚线=补仓计划价（鼠标移动可看任意时点价格）")
        self._chart_pts = [(x_of(hm), y_of(p), f"{selected} {hm}", p)
                           for hm, p in pts]
        self._chart_tip_var.set(self._chart_tip_base)

    def _add_trade(self):
        win = tk.Toplevel(self.root)
        win.title("记一笔补仓")
        win.after(60, lambda w=win: _dark_title(w, THEME_NAME == "dark"))
        win.geometry("310x300")
        win.resizable(False, False)
        win.configure(bg=C_BG)
        win.transient(self._trades_win or self.root)
        win.grab_set()

        dt_var = tk.StringVar(value=f"{datetime.now():%Y-%m-%d %H:%M}")
        amt_var = tk.StringVar(value="5000")
        grams_var = tk.StringVar(value="")
        sync_var = tk.BooleanVar(value=True)
        buy_px = self._latest_data.get("jicun_active")

        def form_row(r, label, var, hint=None):
            tk.Label(win, text=label, bg=C_BG, fg=C_TXT_2,
                     font=(FONT, 9)).grid(
                row=r, column=0, sticky="w", padx=16, pady=(12, 0))
            ttk.Entry(win, textvariable=var, width=17).grid(
                row=r, column=1, padx=8, pady=(12, 0))
            if hint:
                tk.Label(win, text=hint, bg=C_BG, fg=C_TXT_3,
                         font=(FONT, 8)).grid(
                    row=r + 1, column=1, sticky="w", padx=8)

        form_row(0, "买入时间", dt_var)
        form_row(2, "金额(元)", amt_var)
        form_row(4, "克数(克)", grams_var,
                 "留空则按实时买入价倒算" + (f"（当前 {buy_px:g}）" if buy_px else "，暂无报价需手填"))
        dark_checkbutton(win, "同时计入总持仓克数与成本", sync_var).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 0))

        def on_save():
            try:
                amt = float(amt_var.get())
                g = float(grams_var.get()) if grams_var.get().strip() else 0.0
                if amt <= 0 or g < 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("添加补仓", "请填写有效的金额/克数。", parent=win)
                return
            if g <= 0:
                if not buy_px:
                    messagebox.showwarning("添加补仓", "暂无实时买入价，请手动填写克数。", parent=win)
                    return
                g = amt / buy_px
            rec = {"dt": dt_var.get().strip() or f"{datetime.now():%Y-%m-%d %H:%M}",
                   "amount": round(amt, 2), "grams": round(g, 4)}
            trades = self.cfg.setdefault("trades", [])
            trades.append(rec)
            trades.sort(key=lambda x: x.get("dt", ""), reverse=True)
            if sync_var.get():
                self.cfg["hold_grams"] = float(self.cfg.get("hold_grams") or 0) + g
                self.cfg["hold_cost"] = float(self.cfg.get("hold_cost") or 0) + amt
            self._persist()
            self._render_position()
            self._refresh_trades()
            self._refresh_pl_calendar()
            win.destroy()

        ttk.Button(win, text="含泪记上", style="Gold.TButton",
                   command=on_save).grid(row=7, column=0, pady=16)
        ttk.Button(win, text="取消", command=win.destroy).grid(row=7, column=1)

    def _delete_trade(self):
        tree = self._trades_tree
        sel = tree.selection()
        # 严格匹配补仓行 iid（"t0"/"t1"…），排除 "total" 等以 t 开头的合计/底仓行
        idx = None
        if sel:
            sid = sel[0]
            if sid.startswith("t") and sid[1:].isdigit():
                idx = int(sid[1:])
        trades = self.cfg.get("trades") or []
        if idx is None or not (0 <= idx < len(trades)):
            messagebox.showinfo("删除补仓", "请先选中一笔补仓记录（合计、底仓行不可删除）。",
                                parent=self._trades_win)
            return
        t = trades[idx]
        if not messagebox.askyesno(
                "删除补仓",
                f"删除 {t.get('dt', '')} 买入 {float(t.get('amount') or 0):,.0f} 元的记录？\n"
                "总持仓克数与成本将同步扣减（底仓相应增加）。",
                parent=self._trades_win):
            return
        self.cfg["hold_grams"] = max(
            0.0, float(self.cfg.get("hold_grams") or 0) - float(t.get("grams") or 0))
        self.cfg["hold_cost"] = max(
            0.0, float(self.cfg.get("hold_cost") or 0) - float(t.get("amount") or 0))
        del trades[idx]
        self.cfg["trades"] = trades
        self._persist()
        self._render_position()
        self._refresh_trades()
        self._refresh_pl_calendar()

    def _render_position(self):
        """根据最新赎回价计算并刷新持仓卡片。"""
        sell = self._latest_sell
        grams = float(self.cfg.get("hold_grams") or 0)
        cost = float(self.cfg.get("hold_cost") or 0)
        if not sell or grams <= 0:
            self.pos_mv_var.set("--")
            self.pos_pl_var.set("--")
            self.pos_pl_label.configure(fg=C_TXT_2)
            self.pos_cost_var.set("")
            self.pos_pl_pct_var.set("")
            self.today_cap_var.set("今日盈亏")
            self.pos_today_var.set("--")
            self.pos_today_label.configure(fg=C_TXT_2)
            self.pos_info_var.set("")
            self._set_roast("还没录入持仓，先建个仓，我们才有故事（和眼泪）。", "gold")
            return

        if self.cfg.get("privacy"):
            # 隐私模式：市值/收益/成本/克数全部打码，仅保留公开行情（赎回价、日内高低）
            self.pos_mv_var.set("****")
            self.pos_pl_var.set("****")
            self.pos_pl_label.configure(fg=C_TXT_2)
            self.pos_cost_var.set("（成本 **** 元/克）")
            self.pos_pl_pct_var.set("收益率 **")
            self.today_cap_var.set("今日盈亏")
            self.pos_today_var.set("****")
            self.pos_today_label.configure(fg=C_TXT_2)
            self.pos_info_var.set(
                f"持仓 ** 克｜赎回价 {sell:.2f} 元/克｜日内 "
                f"{self._latest_high:.2f} ~ {self._latest_low:.2f}")
            self._set_roast("打码模式已开启：赚亏都只有自己知道，群友只能看到你的淡定。",
                            "gold")
            return

        mv = grams * sell
        pl = mv - cost
        cost_px = cost / grams
        self.pos_mv_var.set(f"{mv:,.2f}")
        sign = "+" if pl >= 0 else ""
        self.pos_pl_var.set(f"{sign}{pl:,.2f} 元")
        self.pos_pl_label.configure(fg=C_UP if pl >= 0 else C_DOWN)
        self.pos_cost_var.set(f"成本 {cost_px:.2f} 元/克")
        self.pos_pl_pct_var.set(
            f"收益率 {sign}{pl / cost * 100:.2f}%" if cost else "")

        # 今日盈亏：按每晚22:20工行结算价（本地记录）+ 交易记录重算，与支付宝同口径；
        # 当日22:20前用真实工行赎回价实时标记，22:20后自动转为收盘口径。
        today_pl = None
        rows = self._daily_rows()
        if rows is None:
            self.today_cap_var.set("今日盈亏")
            self.pos_today_var.set("…")
            self.pos_today_label.configure(fg=C_TXT_2)
        elif rows:
            last_d = max(rows)
            info = rows[last_d]
            tsign = "+" if info["pl"] >= 0 else ""
            live = "实时" if info["is_today"] else "收盘"
            self.today_cap_var.set(f"{last_d[5:]} {live}盈亏")
            self.pos_today_var.set(f"{tsign}{info['pl']:,.2f} 元")
            self.pos_today_label.configure(
                fg=C_UP if info["pl"] >= 0 else C_DOWN)
            if info["is_today"]:
                today_pl = info["pl"]
        else:
            self.today_cap_var.set("今日盈亏")
            self.pos_today_var.set("--")
            self.pos_today_label.configure(fg=C_TXT_2)

        self.pos_info_var.set(
            f"持仓 {grams:g} 克｜赎回价 {sell:.2f} 元/克｜日内 "
            f"{self._latest_high:.2f} ~ {self._latest_low:.2f}")

        # 吐槽条：优先点评今日战况；今日还没数则点评总持仓
        if today_pl is not None:
            tone, txt = self._pl_roast(today_pl)
        else:
            tone, txt = self._pl_roast(pl, prefix_total=True)
        self._set_roast(txt, tone)

    def _set_roast(self, text, tone="gold"):
        """刷新吐槽条。tone: up=红（赚）、down=绿（亏）、gold=中性。"""
        self.roast_var.set(text)
        if tone == "up":
            bar_bg, strip, fg = C_ROAST_UP_BG, C_UP, C_UP
        elif tone == "down":
            bar_bg, strip, fg = C_ROAST_DN_BG, C_DOWN, C_DOWN
        else:
            bar_bg, strip, fg = C_ROAST_GOLD_BG, C_GOLD_DIM, C_GOLD_HI
        bar = self.roast_label.master
        bar.configure(bg=bar_bg)
        self.roast_label.configure(bg=bar_bg, fg=fg)
        self.roast_strip.configure(bg=strip)

    @staticmethod
    def _pl_roast(pl, prefix_total=False):
        """按盈亏金额给档位文案（适度玩梗，极端行情才上重槽点）。"""
        if prefix_total:
            if pl >= 5000:
                tone, txt = "up", "浮盈这个数，群里你先说话，腰杆硬。"
            elif pl >= 1000:
                tone, txt = "up", "累计浮盈挺体面，属于可以截图但没必要的水平。"
            elif pl >= 50:
                tone, txt = "up", "累计小有浮盈，奶茶钱到手，别贪。"
            elif pl > -50:
                tone, txt = "gold", "累计基本白玩，手续费看了都心疼。"
            elif pl > -1000:
                tone, txt = "down", "累计小幅浮亏，问题不大，就当给市场交了个朋友。"
            elif pl > -5000:
                tone, txt = "down", "累计浮亏有点扎心，但删软件也改变不了什么。"
            else:
                tone, txt = "down", "累计浮亏较大，建议群里装死，等风来。"
        elif pl >= 1000:
            tone, txt = "up", "今日暴赚！今天腰杆硬了，群里你说了算，今晚加鸡腿。"
        elif pl >= 300:
            tone, txt = "up", "今天行情给面儿，一顿火锅钱到手，可以发言了。"
        elif pl >= 50:
            tone, txt = "up", "今日小赚，一杯奶茶钱，聊胜于无。"
        elif pl > -50:
            tone, txt = "gold", "今日基本没波动，行情很尊重你，也很敷衍你。"
        elif pl > -300:
            tone, txt = "down", "今日小亏，一杯奶茶没了，问题不大。"
        elif pl > -1000:
            tone, txt = "down", "今日浮亏一顿火锅钱，关灯吃面，别让老婆看见。"
        else:
            tone, txt = "down", "今日大跌！关灯吃面，群里先潜水，我们暗中观察。"
        return (tone, txt)

    # ---- 托盘 ----
    def _make_icon_image(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (15, 16, 20, 255))  # C_BG
        d = ImageDraw.Draw(img)
        d.ellipse((5, 5, 59, 59), fill=(227, 192, 106, 255))  # C_GOLD
        d.text((17, 16), "金", fill=(23, 18, 10, 255))
        return img

    def _build_tray(self):
        try:
            import pystray
        except ImportError:
            self.tray = None
            return

        menu = pystray.Menu(
            pystray.MenuItem("出来看盘", self._tray_show, default=True),
            pystray.MenuItem("立即刷新", lambda: self.root.after(0, self._poll_once)),
            pystray.MenuItem("收摊走人", self._tray_quit),
        )
        self.tray = pystray.Icon(APP_NAME, self._make_icon_image(), APP_NAME, menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _tray_show(self, *_):
        self.root.after(0, self.show_window)

    def _tray_quit(self, *_):
        self.root.after(0, self.quit_app)

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self):
        self.root.withdraw()

    def quit_app(self):
        self.stop_event.set()
        try:
            if self.tray:
                self.tray.stop()
        except Exception:
            pass
        self._persist()
        if self._hist_dirty:
            save_history(self._hist)
        save_settles(self._settles)
        self.root.destroy()

    # ---- 取价轮询 ----
    def _poll_once(self):
        if self.stop_event.is_set():
            return
        self.status_var.set("正在获取行情…")
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self):
        try:
            data = fetch_prices()
            self.root.after(0, lambda: self._on_data(data))
        except Exception as e:
            self.failed_count += 1
            self.root.after(0, lambda: self._on_fetch_error(e))

    def _on_fetch_error(self, err):
        self._online = False
        self._dot.configure(fg=C_DOWN)
        self.status_var.set(
            f"[{datetime.now():%H:%M:%S}] 行情获取失败（连续 {self.failed_count} 次）：{err}")
        self._schedule_next()

    def _maybe_record_settle(self, sell):
        """工作日 22:20 交易截止那一刻，用工行积存金实时报价记录当日日结价。
        该价即支付宝每日收益口径（同一工银积存金、同一 22:20 截止自报价）。
        22:20 后的接口报价只是跟随夜盘变动的参考价，故只在 22:20-22:24 窗口内
        抓第一次有效报价即锁定、绝不刷新；人工录入（alipay）永不覆盖。"""
        now = datetime.now()
        hm = now.strftime("%H:%M")
        if not sell or now.weekday() >= 5 or not ("22:20" <= hm <= "22:24"):
            return
        day = now.strftime("%Y-%m-%d")
        old = self._settles.get(day)
        if old:
            return  # 当晚已锁定（icbc）或是人工精确价（alipay），都不再覆盖
        price = float(sell)
        self._settles[day] = {"price": price, "src": "icbc"}
        self._settles = dict(sorted(self._settles.items()))
        save_settles(self._settles)
        self._settle_recorded_day = day

    def _on_data(self, data):
        self.failed_count = 0
        self._online = True
        self._dot.configure(fg=C_GOLD)
        key = self.cfg["source"]
        price = data.get(key)
        ts = data.get("time", "")

        self.time_var.set(f"更新于 {ts}" + (f"   {data.get('date', '')}" if data.get("date") else ""))
        if data.get("high"):
            self.hl_var.set(f"日内最高 {data['high']:.2f}")
        if data.get("low"):
            self.ll_var.set(f"日内最低 {data['low']:.2f}")

        # 持仓估值始终按积存金赎回价（独立于监控品种）
        self._latest_data = data
        self._latest_sell = data.get("jicun_sell")
        self._latest_high = data.get("high") or 0.0
        self._latest_low = data.get("low") or 0.0
        # 本地分时采集（仅交易时段，按分钟去重，约每分钟落盘一次）
        day, hm = data.get("date"), (ts[:5] if ts else "")
        sell0 = data.get("jicun_sell")
        if sell0 and day and in_trade_session(hm):
            if append_history_point(self._hist, day, hm, sell0):
                self._hist_dirty = True
            now_ts = datetime.now().timestamp()
            if self._hist_dirty and now_ts - self._last_hist_save >= 60:
                save_history(self._hist)
                self._hist_dirty = False
                self._last_hist_save = now_ts
        # 22:20 交易截止：用工行积存金报价锁定当日日结价（支付宝同口径）
        self._maybe_record_settle(sell0)
        self._set_trade_date(data.get("date"))
        self._render_position()
        if self._trades_win is not None:
            self._refresh_trades()
        if self._pl_win is not None:
            self._refresh_pl_calendar()
        if self._chart_win is not None:
            self._draw_chart()

        if price is None:
            self.price_var.set("--")
            self.status_var.set(f"[{ts}] 当前品种暂无报价（可能不在交易时段：周一至周五 9:10-22:20）")
            self._schedule_next()
            return

        prev = self.last_price
        self.price_var.set(f"{price:.2f}")
        # 方向信息收敛成右上角 pill 徽章，大价格永远金色
        if prev is None:
            self.chg_var.set("")
            self.chg_label.configure(bg=C_CARD, fg=C_TXT_2, padx=0, pady=0)
        elif price == prev:
            self.chg_var.set("持平")
            self.chg_label.configure(bg=C_CARD, fg=C_TXT_3, padx=0, pady=0)
        else:
            diff = price - prev
            up = diff > 0
            self.chg_var.set(f"{'↑' if up else '↓'} {abs(diff):.2f} 元")
            self.chg_label.configure(bg=C_UP_BG if up else C_DOWN_BG,
                                     fg=C_UP if up else C_DOWN,
                                     padx=8, pady=2)

        # 首次成功取价：建立基线，不弹窗
        if not self.baseline_set or prev is None:
            self.baseline_set = True
            self.last_price = price
            self.cfg["last_price"] = price
            self._persist()
            bucket = math.floor(price / self.cfg["step"]) * self.cfg["step"]
            self.status_var.set(
                f"[{ts}] 已建立监控基线 {price:.2f}，跌破 {bucket:g} 或涨破 {bucket + self.cfg['step']:g} 元档时提醒。")
            # 计划补仓价同步建基线：高于基线价的计划先标记已触发（不打扰），
            # 等价格重新站上去后才重新武装；低于基线的计划正常等待下穿
            plan_base = data.get("jicun_active") or price
            self._plans_fired = {
                round(float(p.get("price") or 0), 2): float(p.get("price") or 0) >= plan_base
                for p in (self.cfg.get("plans") or []) if float(p.get("price") or 0) > 0
            }
            self._schedule_next()
            return

        # 档位穿越检测
        try:
            step = float(self.step_var.get())
            if step <= 0:
                step = self.cfg["step"]
        except ValueError:
            step = self.cfg["step"]
        if step != self.cfg["step"]:
            self.cfg["step"] = step
            self._persist()

        tiers = crossed_tiers(prev, price, step)
        if tiers:
            self._fire_alerts(price, tiers, data, ts, up=False)
        if self.alert_up_var.get():
            tiers_up = crossed_tiers_up(prev, price, step)
            if tiers_up:
                self._fire_alerts(price, tiers_up, data, ts, up=True)

        # 补仓计划价检测（按主动买入价口径：买到手的价格）
        self._check_plans(prev, price, data, ts)

        self.last_price = price
        self.cfg["last_price"] = price
        self._persist()
        self.status_var.set(f"[{ts}] 监控中：当前 {price:.2f}，每 {step:g} 元一档。")
        self._schedule_next()

    def _check_plans(self, prev, price, data, ts):
        """补仓计划价检测：按主动买入价口径，严格下穿目标价（prev >= p > price）
        时提醒一次；价格重新站回目标价上方后自动重新武装。"""
        plans = self.cfg.get("plans") or []
        if not plans:
            return
        buy = data.get("jicun_active") or price
        if self._plans_fired is None:
            self._plans_fired = {}
        fired = self._plans_fired
        hits = []
        for plan in plans:
            try:
                target = round(float(plan.get("price") or 0), 2)
                grams = float(plan.get("grams") or 0)
            except (TypeError, ValueError):
                continue
            if target <= 0:
                continue
            was = fired.get(target, False)
            if not was and prev is not None and prev >= target > buy:
                hits.append((target, grams))
                fired[target] = True
            elif was and buy > target:
                # 价格涨回目标价上方，重新武装（按买入价成交可能略高于目标，留 0.01 余量）
                fired[target] = False
        for target, grams in hits:
            self._fire_plan_alert(target, grams, buy, ts)

    def _fire_plan_alert(self, target, grams, buy, ts):
        if grams > 0:
            amount = target * grams
            g_txt = f"按计划补 {grams:g} 克，约 {amount:,.0f} 元"
        else:
            g_txt = "到计划目标价了，可以看看补多少"
        title = f"补仓计划到价 {target:g} 元"
        msg = (f"[{ts}] 主动买入价已下穿计划价\n"
               f"当前买入 {buy:.2f} 元/克\n{g_txt}")
        self.toasts.show(title, msg, tag=f"plan-{target:g}",
                         duration_ms=30000, kind="plan")
        self.status_var.set(msg.replace("\n", " "))
        if self.sound_var.get():
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                self.root.bell()
        if self.tray is not None:
            try:
                self.tray.title = f"{APP_NAME}｜补仓计划 {target:g} 元到价"
                self.tray.notify(g_txt, title)
            except Exception:
                pass
        # 微信推送：多变量模板
        holding_lines = self._holding_summary_lines()
        push_dict = {
            "header": f"补仓计划到价 {target:g} 元",
            "price_line": f"当前买入 {buy:.2f} 元/克",
            "tier_line": g_txt,
            "holding_line": holding_lines[0] if holding_lines else "",
            "pl_line": holding_lines[1] if len(holding_lines) > 1 else "",
            "remark": "",
        }
        self._wx_push_async(push_dict)

    def _open_plans(self):
        """补仓计划管理窗：目标买入价 + 计划克数，走势图同步画蓝色虚线。"""
        win = getattr(self, "_plans_win", None)
        if win is not None and tk.Toplevel.winfo_exists(win):
            win.lift()
            win.focus_force()
            return
        win = tk.Toplevel(self.root)
        win.title("补仓计划价")
        win.after(60, lambda w=win: _dark_title(w, THEME_NAME == "dark"))
        win.geometry("440x460")
        win.minsize(400, 380)
        win.configure(bg=C_BG)
        win.transient(self.root)

        tk.Label(win, text="补仓计划价", bg=C_BG, fg=C_GOLD_HI,
                 font=(FONT, 12, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tip = ("买入价下穿目标价时弹窗提醒；走势图上以蓝色虚线标记。\n"
               "可先定目标价、克数留 0（只提醒），或直接定好补多少克。")
        tk.Label(win, text=tip, bg=C_BG, fg=C_TXT_2,
                 font=(FONT, 8), justify="left").pack(anchor="w", padx=16)

        form = tk.Frame(win, bg=C_BG)
        form.pack(fill="x", padx=16, pady=10)
        tk.Label(form, text="目标价", bg=C_BG, fg=C_TXT,
                 font=(FONT, 9)).grid(row=0, column=0, sticky="e", padx=(0, 6), pady=4)
        price_entry = ttk.Entry(form, width=12)
        price_entry.grid(row=0, column=1, pady=4)
        tk.Label(form, text="元/克", bg=C_BG, fg=C_TXT_2,
                 font=(FONT, 8)).grid(row=0, column=2, sticky="w", padx=4)
        tk.Label(form, text="计划克数", bg=C_BG, fg=C_TXT,
                 font=(FONT, 9)).grid(row=0, column=3, sticky="e", padx=(16, 6))
        grams_entry = ttk.Entry(form, width=12)
        grams_entry.grid(row=0, column=4, pady=4)

        cols = ("price", "grams", "amount")
        headers = ("目标价(元/克)", "计划克数(克)", "预计金额(元)")
        widths = (130, 120, 120)
        frame = tk.Frame(win, bg=C_CARD, highlightbackground=C_LINE, highlightthickness=1)
        frame.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=8, selectmode="browse")
        for c, h, w in zip(cols, headers, widths):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="e")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        tree.tag_configure("plan", foreground=C_PLAN)
        self._plans_tree = tree

        def add_plan(_=None):
            try:
                p = float(price_entry.get().strip())
                g = float(grams_entry.get().strip() or 0)
            except ValueError:
                messagebox.showwarning("格式不对", "目标价和克数需要是数字。", parent=win)
                return
            if p <= 0 or g < 0:
                messagebox.showwarning("格式不对", "目标价需大于 0，克数不能为负。", parent=win)
                return
            plans = self.cfg.get("plans") or []
            key = round(p, 2)
            plans = [x for x in plans if round(float(x.get("price") or 0), 2) != key]
            plans.append({"price": key, "grams": g})
            plans.sort(key=lambda x: float(x.get("price") or 0), reverse=True)
            self.cfg["plans"] = plans
            self._persist()
            # 新计划：当前买入价在其下方时先标记已触发，不打扰
            buy = self._latest_data.get("jicun_active") if self._latest_data else None
            cur = buy or self.last_price
            if self._plans_fired is None:
                self._plans_fired = {}
            if cur is not None:
                self._plans_fired[key] = cur <= key
            price_entry.delete(0, "end")
            grams_entry.delete(0, "end")
            price_entry.focus_set()
            self._refresh_plans()
            if self._chart_win is not None:
                self._draw_chart()

        def del_plan():
            sel = tree.selection()
            if not sel:
                return
            key = float(sel[0])
            plans = [x for x in (self.cfg.get("plans") or [])
                     if round(float(x.get("price") or 0), 2) != round(key, 2)]
            self.cfg["plans"] = plans
            self._persist()
            if self._plans_fired is not None:
                self._plans_fired.pop(round(key, 2), None)
            self._refresh_plans()
            if self._chart_win is not None:
                self._draw_chart()

        price_entry.bind("<Return>", add_plan)
        grams_entry.bind("<Return>", add_plan)
        btns = tk.Frame(win, bg=C_BG)
        btns.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(btns, text="添加计划", style="Gold.TButton",
                   command=add_plan).pack(side="left")
        ttk.Button(btns, text="删除选中", command=del_plan).pack(side="left", padx=8)
        ttk.Button(btns, text="关闭", command=lambda: self._close_plans(win)).pack(side="right")

        self._plans_win = win
        win.protocol("WM_DELETE_WINDOW", lambda: self._close_plans(win))
        self._refresh_plans()

    def _refresh_plans(self):
        win = getattr(self, "_plans_win", None)
        tree = getattr(self, "_plans_tree", None)
        if win is None or not tk.Toplevel.winfo_exists(win) or tree is None:
            return
        tree.delete(*tree.get_children())
        for i, p in enumerate(self.cfg.get("plans") or []):
            px = float(p.get("price") or 0)
            g = float(p.get("grams") or 0)
            amt = f"{px * g:,.0f}" if g > 0 else "只提醒"
            tree.insert("", "end", iid=f"{px:g}",
                        values=(f"{px:g}", f"{g:g}" if g > 0 else "--", amt),
                        tags=("plan",))

    def _close_plans(self, win):
        self._plans_win = None
        self._plans_tree = None
        win.destroy()

    def _fire_alerts(self, price, tiers, data, ts, up=False):
        name = SOURCES[self.cfg["source"]]["name"]
        unit = SOURCES[self.cfg["source"]]["unit"]
        # 一次轮询可能跨多档：合并为一条弹窗，逐档列示
        verb = "涨破" if up else "跌破"
        lines = "\n".join(f"{verb} {t:g} 元" for t in tiers)
        msg = f"当前 {price:.2f} {unit}\n{lines}"
        # 附带持仓实时浮盈（按赎回价）
        sell = self._latest_sell
        grams = float(self.cfg.get("hold_grams") or 0)
        cost = float(self.cfg.get("hold_cost") or 0)
        avg_cost = cost / grams if grams else 0.0
        pl_txt = ""
        if self.cfg.get("privacy"):
            pl_txt = "\n持仓浮盈 ****（** 克）"
        elif sell and grams > 0 and cost > 0:
            pl = grams * sell - cost
            pl_txt = f"\n持仓浮盈 {pl:+,.2f} 元（{grams:g} 克）"
        if up:
            # 回本检测：从成本下方穿过成本均价
            if avg_cost and self.last_price is not None and self.last_price < avg_cost <= price:
                title = "回本啦！可以考虑减仓"
                msg += f"\n已站回持仓成本 {avg_cost:.2f} 元/克，熬出头了"
            else:
                title = "金价拉升，吃肉现场"
            self.toasts.show(title, f"{name}\n{msg}{pl_txt}", duration_ms=15000, kind="up")
        else:
            self.toasts.show("金价跳水现场，注意仓位", f"{name}\n{msg}{pl_txt}",
                             duration_ms=15000, kind="down")
        if self.sound_var.get():
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION if not up
                                     else winsound.MB_ICONASTERISK)
            except Exception:
                self.root.bell()

        # 更新托盘 tooltip
        if self.tray:
            try:
                tip = f"{APP_NAME}｜{price:.2f}（{verb} {tiers[0]:g}）"
                if self.cfg.get("privacy"):
                    tip += "｜浮盈 ****"
                elif sell and grams > 0 and cost > 0:
                    tip += f"｜浮盈 {grams * sell - cost:+,.0f}"
                self.tray.title = tip
            except Exception:
                pass

        # 微信推送：多变量模板（每个 {{xxx.DATA}} 在卡片上独立一行，微信沙箱正确渲染）
        holding_lines = self._holding_summary_lines()
        tier_txt = "、".join(f"{t:g} 元" for t in tiers)
        push_dict = {
            "header": f"{name} {verb}",
            "price_line": f"当前 {price:.2f} {unit}",
            "tier_line": f"{verb} {tier_txt}",
            "holding_line": holding_lines[0] if holding_lines else "",
            "pl_line": holding_lines[1] if len(holding_lines) > 1 else "",
            "remark": "",
        }
        self._wx_push_async(push_dict)

    def _test_alert(self):
        self.toasts.show(
            "金价跳水现场（测试弹）",
            f"{SOURCES[self.cfg['source']]['name']}\n当前 880.00 元/克\n跌破 890 元\n\n别慌，这是演习，钱还在。",
            duration_ms=8000,
        )
        if self.sound_var.get():
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                self.root.bell()

    # ---- 微信推送（公众平台测试号） ----
    def _holding_summary_lines(self):
        """构造持仓多行摘要（按赎回价实时浮盈）；隐私模式金额打码，无持仓返回空列表。"""
        grams = float(self.cfg.get("hold_grams") or 0)
        cost = float(self.cfg.get("hold_cost") or 0)
        if grams <= 0 or cost <= 0:
            return []
        avg = cost / grams
        sell = self._latest_sell
        if self.cfg.get("privacy"):
            return [f"持仓 {grams:g} 克｜成本均价 **** 元/克",
                    "当前浮盈 **** 元"]
        lines = [f"持仓 {grams:g} 克｜成本均价 {avg:.2f} 元/克"]
        if sell:
            pl = grams * sell - cost
            pct = pl / cost * 100
            lines.append(f"当前浮盈 {pl:+,.2f} 元（{pct:+.2f}%）")
        return lines

    def _refresh_wx_state(self):
        if not hasattr(self, "_wx_state_var"):
            return
        if self.cfg.get("wx_enabled") and self._wx_pusher.configured(self.cfg):
            self._wx_state_var.set("手机微信推送：已开启")
        elif self._wx_pusher.configured(self.cfg):
            self._wx_state_var.set("手机微信推送：已配置，未开启")
        else:
            self._wx_state_var.set("手机微信推送：未配置")

    def _wx_push_async(self, title_or_dict, content=None):
        """后台线程发送微信推送。
        调用方式一（旧，单变量模板）：_wx_push_async(title_str, content_str)
        调用方式二（新，多变量模板）：_wx_push_async(data_dict)，此时 dict 的键就是模板变量名
        """
        if not (self.cfg.get("wx_enabled") and self._wx_pusher.configured(self.cfg)):
            return
        cfg_snapshot = {k: self.cfg.get(k, "") for k in
                        ("wx_appid", "wx_secret", "wx_openid", "wx_template_id")}
        if isinstance(title_or_dict, dict):
            payload = title_or_dict
        else:
            # 旧路径兜底：拼回单变量
            payload = f"{title_or_dict}\n{content}"

        def work():
            err = self._wx_pusher.send(cfg_snapshot, payload)
            if err:
                log_error("微信推送", (RuntimeError(err), RuntimeError(err), None))
                try:
                    self.root.after(0, lambda: self.status_var.set(
                        f"[{datetime.now():%H:%M}] {err}，本地提醒不受影响"))
                except Exception:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _open_wx_settings(self):
        win = getattr(self, "_wx_win", None)
        if win is not None and tk.Toplevel.winfo_exists(win):
            win.lift()
            win.focus_force()
            return
        win = tk.Toplevel(self.root)
        self._wx_win = win
        win.title("微信推送设置")
        win.after(60, lambda w=win: _dark_title(w, THEME_NAME == "dark"))
        win.geometry("520x505")
        win.minsize(480, 470)
        win.configure(bg=C_BG)
        win.transient(self.root)

        tk.Label(win, text="微信推送（公众平台测试号）", bg=C_BG, fg=C_GOLD_HI,
                 font=(FONT, 12, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tip = ("用微信扫码申请测试号 → 关注测试号二维码拿 openid → 新建测试模板，\n"
               "把页面上的四个值填到下面。桌面程序直连微信接口，不需要服务器和域名。")
        tk.Label(win, text=tip, bg=C_BG, fg=C_TXT_2,
                 font=(FONT, 8), justify="left").pack(anchor="w", padx=16)
        link = tk.Label(win, text="点这里打开测试号申请页（mp.weixin.qq.com）",
                        bg=C_BG, fg=C_GOLD, font=(FONT, 8, "underline"), cursor="hand2")
        link.pack(anchor="w", padx=16, pady=(3, 8))
        link.bind("<Button-1>", lambda _e: webbrowser.open(WX_SANDBOX_URL))

        form = tk.Frame(win, bg=C_BG)
        form.pack(fill="x", padx=16)
        form.grid_columnconfigure(1, weight=1)
        vars_ = {}
        rows = [
            ("wx_appid", "appID"),
            ("wx_secret", "appsecret"),
            ("wx_openid", "openid（微信号）"),
            ("wx_template_id", "模板ID"),
        ]
        for r, (key, label) in enumerate(rows):
            tk.Label(form, text=label, bg=C_BG, fg=C_TXT,
                     font=(FONT, 9)).grid(row=r, column=0, sticky="w", pady=5, padx=(0, 8))
            v = tk.StringVar(value=str(self.cfg.get(key) or ""))
            vars_[key] = v
            ttk.Entry(form, textvariable=v, show="*" if key == "wx_secret" else ""
                      ).grid(row=r, column=1, sticky="ew", pady=5)

        tpl_tip = ("模板内容请填：金价提醒: {{content.DATA}}\n"
                   "（测试号模板不能只写 {{content.DATA}}，必须带前缀文案，变量名要叫 content）")
        tk.Label(win, text=tpl_tip, bg=C_BG, fg=C_TXT_3,
                 font=(FONT, 8), justify="left").pack(anchor="w", padx=16, pady=(6, 2))
        tk.Label(win, text="推送含价格档位、持仓克数、成本均价与实时浮盈；开启隐私模式后金额自动打码。",
                 bg=C_BG, fg=C_TXT_3, font=(FONT, 8)).pack(anchor="w", padx=16)

        enable_var = tk.BooleanVar(value=bool(self.cfg.get("wx_enabled")))
        dark_checkbutton(win, "开启微信推送（破档 / 补仓到价时发到手机）", enable_var,
                         bg=C_BG).pack(anchor="w", padx=14, pady=(10, 4))

        result_var = tk.StringVar(value="")
        tk.Label(win, textvariable=result_var, bg=C_BG, fg=C_TXT_2,
                 font=(FONT, 9), wraplength=470, justify="left").pack(
            anchor="w", padx=16, pady=(6, 0))

        def collect():
            return {k: vars_[k].get().strip() for k, _ in rows}

        def save():
            vals = collect()
            self.cfg.update(vals)
            self.cfg["wx_enabled"] = bool(enable_var.get())
            save_config(self.cfg)
            self._refresh_wx_state()
            self.status_var.set(
                f"[{datetime.now():%H:%M}] 微信推送设置已保存（{'已开启' if enable_var.get() else '未开启'}）")

        def do_test():
            if self._wx_busy:
                return
            vals = collect()
            if not all(vals.values()):
                messagebox.showwarning("微信推送", "四个参数都要填齐再测试。", parent=win)
                return
            self._wx_busy = True
            result_var.set("正在发送测试消息，请留意微信通知…")
            test_btn.configure(state="disabled")

            def work():
                # 多变量模板测试：用真实最新价+真实持仓构造模拟破档
                demo_price = self.last_price or 0.0
                unit = SOURCES[self.cfg["source"]]["unit"]
                if demo_price:
                    verb = "跌破"
                    tier_val = round(demo_price + 5)
                else:
                    demo_price, verb, tier_val = 880.00, "跌破", 890
                holding_lines = self._holding_summary_lines()
                push_dict = {
                    "header": "金价跌破 880 元（测试）" if not demo_price
                              else f"{SOURCES[self.cfg['source']]['name']} 跌破",
                    "price_line": f"当前 {demo_price:.2f} {unit}",
                    "tier_line": f"跌破 {tier_val:g} 元（模拟档位）",
                    "holding_line": holding_lines[0] if holding_lines else "",
                    "pl_line": holding_lines[1] if len(holding_lines) > 1 else "",
                    "remark": "—— 这是演习，钱还在 ——",
                }
                err = self._wx_pusher.send(vals, push_dict)

                def ui():
                    self._wx_busy = False
                    try:
                        test_btn.configure(state="normal")
                    except Exception:
                        pass
                    if err:
                        result_var.set(err)
                        log_error("微信测试推送", (RuntimeError(err), RuntimeError(err), None))
                    else:
                        result_var.set("发送成功！请查看微信服务通知（测试号消息可能折叠在订阅号里）。")

                try:
                    self.root.after(0, ui)
                except Exception:
                    pass

            threading.Thread(target=work, daemon=True).start()

        btns = tk.Frame(win, bg=C_BG)
        btns.pack(fill="x", padx=16, pady=14)
        ttk.Button(btns, text="保存", style="Gold.TButton",
                   command=save).pack(side="left")
        test_btn = ttk.Button(btns, text="保存并测试推送", command=lambda: (save(), do_test()))
        test_btn.pack(side="left", padx=10)
        ttk.Button(btns, text="关闭", command=win.destroy).pack(side="right")

        def on_close():
            self._wx_win = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", on_close)

    def _schedule_next(self):
        try:
            interval = max(5, int(float(self.interval_var.get())))
        except ValueError:
            interval = self.cfg["interval"]
        if interval != self.cfg["interval"]:
            self.cfg["interval"] = interval
            self._persist()
        if not self.stop_event.is_set():
            self.root.after(interval * 1000, self._poll_once)

    def _persist(self):
        self.cfg["sound"] = bool(self.sound_var.get())
        if hasattr(self, "alert_up_var"):
            self.cfg["alert_up"] = bool(self.alert_up_var.get())
        save_config(self.cfg)

    def run(self):
        self.root.mainloop()


def _enable_dpi():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def _cli_test():
    """命令行自检：python gold_monitor.py --check"""
    data = fetch_prices()
    print(json.dumps(data, ensure_ascii=False, indent=2))


def log_error(where, exc_info):
    """把异常追加写入 error.log；文件过大时滚动保留后半段，避免无限增长。"""
    try:
        tb = "".join(traceback.format_exception(*exc_info))
        block = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {where}\n{tb}\n"
        if os.path.exists(ERROR_PATH) and os.path.getsize(ERROR_PATH) > ERROR_LOG_MAX:
            try:
                with open(ERROR_PATH, "r", encoding="utf-8", errors="ignore") as f:
                    old = f.read()
                with open(ERROR_PATH, "w", encoding="utf-8") as f:
                    f.write(old[-ERROR_LOG_MAX // 2:])
            except OSError:
                pass
        with open(ERROR_PATH, "a", encoding="utf-8") as f:
            f.write(block)
    except Exception:
        pass  # 日志本身绝不能再抛出


def _install_excepthook():
    """主线程 / 后台线程未捕获异常统一落盘；主线程致命错误额外弹窗提示。"""
    def on_main(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log_error("主线程未捕获异常", (exc_type, exc, tb))
        try:
            messagebox.showerror(
                APP_NAME,
                f"程序遇到错误：{exc_type.__name__}: {exc}\n\n"
                f"详细信息已写入：\n{ERROR_PATH}")
        except Exception:
            pass

    def on_thread(args):
        log_error(f"后台线程未捕获异常（{args.thread.name if args.thread else '?'}）",
                  (args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = on_main
    threading.excepthook = on_thread
    # Tk 的 after / 事件回调异常默认只打印到控制台（windowed 下等于消失），单独接管
    tk.Tk.report_callback_exception = lambda self_, exc, val, tb: log_error(
        "界面回调未捕获异常", (exc, val, tb))


if __name__ == "__main__":
    if "--check" in sys.argv:
        _cli_test()
    else:
        _enable_dpi()
        _install_excepthook()
        try:
            GoldMonitorApp().run()
        except Exception:
            log_error("启动/运行失败", sys.exc_info())
            try:
                messagebox.showerror(
                    APP_NAME,
                    f"程序启动失败：\n{traceback.format_exc(limit=3)}\n\n"
                    f"详细信息已写入：\n{ERROR_PATH}")
            except Exception:
                raise
