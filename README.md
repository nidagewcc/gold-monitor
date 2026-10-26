# 积存金价格监控（GoldMonitor）

一个 Windows 桌面小程序，用来盯 **工商银行积存金 / 如意金 / 账户黄金 / 上金所 Au99.99** 的实时报价，支持价格档位跌破提醒、持仓盈亏估值、补仓与卖出（赎回）手续费测算，以及分时 / 日 K 走势图。纯 Python + tkinter 实现，单文件，零数据库，无需登录、无需任何 API Key。

> 适合自己日常看盘辅助。行情来自银行与行情网站的**公开页面接口**，价格与成交以工行官方为准；本程序不构成投资建议。

---

## 功能特性

- **实时报价轮询**：默认 10 秒一次，可切换 7 个监控品种（积存金买入价 / 赎回价、如意金、人民币账户黄金、上金所 Au99.99）。
- **档位跌破提醒**：按设定的整数档位（如每 10 元一档），价格向下穿越时弹窗 + 右下角 Toast + 系统提示音。
- **系统托盘**：关闭窗口后最小化到托盘常驻，托盘菜单可显示窗口 / 立即刷新 / 退出；支持开机自启（写注册表 `HKCU\...\Run`）。
- **持仓估值**：填写持有克数与累计成本后，按当前**赎回价**实时估算市值、浮动盈亏、今日盈亏。
- **补仓回本测算**：给定补仓金额与买入价，计算摊薄均价、回本所需涨幅，以及在不同假设月涨幅下的理论回本时间。
- **卖出测算**：给定卖出克数与赎回价，计算赎回金额、**赎回手续费（赎回金额 × 0.5%，工行个人积存金标准费率）**、扣费后实际到账与净盈亏；支持部分卖出。
- **交易记录**：维护逐笔买入明细，自动倒算每笔买入单价，查看逐笔 / 整体盈亏。
- **走势图**（Canvas 手绘，无第三方图表库）：
  - 今日分时：东方财富上金所 Au99.99 在线分时；
  - 历史日 K：东方财富多年日 K；
  - 本地分时：程序常驻期间自行采集累积（工行无公开历史分时接口）。
  - 图上叠加买入点标签与持仓加权平均成本线。
- **隐私模式**：一键对金额 / 克数 / 盈亏打码，并隐藏图上买点与成本线，适合截图或工位展示。

---

## 快速开始

### 环境要求

- Windows（用到了 `winreg` / `winsound` / `ctypes` 等；核心取数与界面理论上跨平台，但仅在 Windows 11 + Python 3.12 上验证）。
- Python 3.9+，安装时勾选 **tcl/tk（tkinter）**——Windows 官方安装器默认自带。

### 安装与运行

```bat
git clone https://github.com/nidagewcc/gold-monitor.git
cd gold-monitor
pip install -r requirements.txt
python gold_monitor.py
```

或直接双击 `启动监控.bat`。

依赖只有两个，且**都是可选的**（仅用于系统托盘图标；缺失时主程序照常运行，只是没有托盘图标）：

- `pystray` —— 系统托盘
- `Pillow` —— 托盘图标绘图

### 命令行自检（不拉界面，验证取数）

```bat
python gold_monitor.py --check
```

会直接抓取一次各品种报价并以 JSON 打印，适合排查网络 / 接口问题。

---

## 配置文件 `config.json`

配置与行情数据文件都放在**脚本（或打包后的 exe）所在目录**，首次运行时若不存在会自动用默认值生成。**该文件含个人持仓与交易记录，已在 `.gitignore` 中排除，不会提交。**

主要字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `source` | string | 当前监控品种 key，见 `SOURCES`（默认 `jicun_active`） |
| `step` | float | 提醒档位间隔（元），默认 10 |
| `interval` | int | 轮询间隔（秒），默认 10 |
| `sound` | bool | 提醒是否播放系统提示音 |
| `autostart` | bool | 是否开机自启（Windows 注册表） |
| `hold_grams` | float | 持仓克数（在界面“编辑”中填写） |
| `hold_cost` | float | 累计投入成本（元） |
| `today_base` | float/null | 今日盈亏基准价（上一交易日收盘价，跨日自动滚动） |
| `last_close` / `last_close_date` | - | 最近交易日最后赎回价，用于跨日更新基准 |
| `trades` | array | 逐笔买入 `{dt, amount, grams}`，买入单价由 `amount/grams` 倒算 |
| `privacy` | bool | 隐私（打码）模式 |
| `last_price` | float/null | 上次价格（内部状态） |

一份最小示例：

```json
{
  "source": "jicun_active",
  "step": 10.0,
  "interval": 10,
  "sound": true,
  "autostart": false,
  "hold_grams": 0.0,
  "hold_cost": 0.0,
  "trades": [],
  "privacy": false
}
```

`price_history.json` 为程序运行期间本地采集的分时数据，结构为 `{交易日: [["HH:MM", 价格], ...]}`，默认保留近 15 个交易日、每日最多约 900 个点，无需手工维护。

---

## 数据来源

| 用途 | 来源 | 说明 |
| --- | --- | --- |
| 积存金买 / 赎价、当日高低 | 工行 `icbcphp.icbc.com.cn/servlet/AsynGetDataServlet`（tranCode `A00622`） | 老站点，需放开旧版 TLS 重协商（`OP_LEGACY_SERVER_CONNECT`） |
| 如意金积存 | 同上，tranCode `A00505` | 返回价格单位为 0.01 元，代码中 /100 |
| 人民币账户黄金 | 同上，tranCode `A00500` | |
| 上金所 Au99.99 现价 | 工行 m 站 `papi.icbc.com.cn/.../goldMarket/productList` | |
| 今日分时 / 历史日 K | 东方财富 `push2his.eastmoney.com`，`secid=118.AU9999` | 免鉴权；分时 `trends2/get`、日 K `kline/get` |

上金所 Au99.99 与积存金同源、走势基本重合（点差通常 0~1 元/克），所以走势图直接用东财的 Au9999 行情，无需本机长期采集。

---

## 代码结构（给 AI Agent / 二次开发者）

整个程序是单文件 `gold_monitor.py`，约 2400 行，按“常量 → 取数 → 配置/本地数据 → 业务工具 → UI”顺序组织：

- **常量与配置**：`SOURCES`（可选品种）、`DEFAULT_CONFIG`（默认配置模板）、`REDEEM_FEE_RATE`（赎回费率 0.005）、各行情 URL 与缓存 TTL、`SSL_CONTEXT`。
- **数据抓取（无界面依赖，可独立调用）**
  - `_post_trancode(tran_code, extra)`：POST 工行 servlet。
  - `fetch_prices()`：抓取全部品种，返回 `{品种key: 价格, ...}`，网络异常向上抛。
  - `fetch_em_trends()` / `fetch_em_daily(beg, end)`：东财分时 / 日 K，经 `_get_em_json()`（带连接错误重试）。
- **配置与本地数据**：`load_config/save_config`、`load_history/save_history/append_history_point`、交易时段 `in_trade_session/trade_minutes`。
- **业务工具**：`crossed_tiers(prev, now, step)`（计算向下穿越的档位）、`set_autostart(enable)`（注册表自启）。
- **UI（tkinter）**
  - `GoldMonitorApp`：主应用，负责构建界面、轮询调度、弹窗、托盘、持仓区渲染。
    - 轮询模型：后台 daemon 线程 `_fetch_worker` 取数 → `root.after(0, _on_data)` 回主线程更新 UI。
    - 测算弹窗：`_avg_down_calculator`（补仓）、`_sell_calculator`（卖出/赎回）。
    - 持仓与交易：`_edit_position`、`_open_trades`、`_render_position`、`_avg_cost`。
    - 走势图：`_open_chart` / `_draw_chart` 及 `_draw_em_trend` / `_render_em_daily` / `_draw_local`；在线数据经 `_ensure_em()` 做代际校验 + TTL 缓存。
  - `ToastManager`：右下角非阻塞通知窗。
  - `_ChartLabels` / `_round_rect`：走势图买点标签防碰撞布局与圆角绘制辅助。

线程安全约定：**所有 tkinter 控件操作只在主线程**，工作线程只做网络 IO，结果通过 `root.after` 回传。

---

## 打包成单个 Windows exe

```bat
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name GoldMonitor ^
  --collect-submodules pystray gold_monitor.py
```

产物在 `dist\GoldMonitor.exe`。打包后程序通过 `sys.frozen` 判断运行形态，会把 `config.json` / `price_history.json` 写到 **exe 同目录**，因此建议把 exe 放在一个可写的普通文件夹（如桌面），不要放进 `C:\Program Files`。分发时**只发 exe**，对方首次运行会自动生成空配置；切勿把自己的 `config.json` 一起发出。

---

## 常见问题

- **走势图偶尔提示“远端断开连接 / 正在重试”**：东方财富行情域的边缘节点对访问会抖动式掐 TCP（`RemoteDisconnected`），并非账号限流。`_get_em_json()` 已对连接类错误做退避重试；多刷新几次或稍后即恢复，不影响主报价提醒。
- **非交易时段没有价格**：积存金交易时段约为周一至周五 09:10–22:30，法定节假日由“接口是否返回报价”自然兜底。
- **没有托盘图标**：未安装 `pystray` / `Pillow` 所致，`pip install -r requirements.txt` 即可；不装也能正常使用主窗口。
- **抓工行接口报 TLS 错**：程序已对工行旧站点放开 `OP_LEGACY_SERVER_CONNECT`；自行改造时注意保留该 SSL 上下文。

---

## 免责声明

本项目仅用于个人看盘与技术学习，行情数据来自公开网络接口，可能存在延迟、缺失或变动。黄金价格波动风险自负，程序中的回本 / 盈亏测算均为数学参考，**不构成任何投资建议**。赎回手续费率、到账时间等业务规则以中国工商银行官方最新公告为准。
