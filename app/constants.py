# -*- coding: utf-8 -*-
"""全局常量：运行路径、数据源地址、默认配置、交易时段与日结判定参数。

本模块不依赖 tkinter，供后端各模块与界面层共用。
"""
import os
import ssl
import sys

APP_NAME = "积存金价格监控"

# 打包成 exe（PyInstaller frozen）后，__file__ 指向临时解压目录，配置写那里会丢；
# 故冻结时改用 exe 所在目录，使 config.json / price_history.json 与 exe 放在一起。
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # app/constants.py -> 上跳两级回到项目根目录
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
EM_TRENDS_TTL = 120    # 分时缓存秒数（东财对高频访问会掐连接，别刷太勤）
EM_KLINE_TTL = 1800    # 日K缓存秒数

# 积存金交易时段：工行渠道每日 9:10-22:20（含周末，法定节假日以银行公告为准）
TRADE_OPEN_MIN = 9 * 60 + 10    # 9:10
TRADE_CLOSE_MIN = 22 * 60 + 20  # 22:20

# 收盘日结价定格与休市暂停（见 monitor.SettleMachine / MonitorEngine）
SETTLE_WATCH_MIN = 22 * 60 + 20   # 22:20 起进入收盘观察，不再按整点首次报价锁定
SETTLE_DEADLINE_MIN = 22 * 60 + 35  # 22:35 硬截止：凑不齐连续同价也强制锁定当晚日结价
SETTLE_STABLE_N = 3               # 赎回价连续 N 次不变即判定银行报价已停更、收盘定格
IDLE_WAKE_MS = 60000              # 休市暂停后的唤醒检查间隔（仅看时间、不发网络请求）

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
