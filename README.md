# 积存金价格监控（GoldMonitor）

多用户 B/S 版：服务端常驻采集 **工商银行积存金 / 如意金 / 账户黄金 / 上金所 Au99.99** 报价，浏览器输域名即可访问，手机也能用。支持价格档位提醒、持仓盈亏、补仓计划、每日盈亏日历、走势图与微信推送。

每个账号的持仓、交易、计划与设置相互隔离；日结价与本地分时由服务端统一维护，全局共享。

> 行情来自银行与行情网站的**公开页面接口**，价格与成交以工行官方为准；本程序不构成投资建议。

---

## 功能

- **实时行情**：服务端单实例轮询（默认 10 秒），SSE 推送到所有在线页面；断线自动重连并显示「离线 · 缓存」。
- **档位提醒**：按设定档位（如每 5 元）向下穿越时，页面 Toast + 微信推送；可开关上涨破档提醒。
- **持仓估值**：填写克数与累计成本，按赎回价实时估算市值、浮动盈亏、成本价；支持隐私模式打码。
- **交易记录**：逐笔买入/卖出，自动倒算成交价；每日盈亏按支付宝同口径重算。
- **每日盈亏日历**：月历展示每天盈亏，点击查看昨收、今日结算价、买卖与手续费明细；无日结价的日期用黄金9999日K近似并标注「估算」。
- **补仓计划**：维护目标价与克数，到价提醒，并在走势图上画计划线。
- **走势图**：今日分时 / 历史日K / 本地分时（ECharts），叠加成本线与计划线。
- **跑路计算器**：赎回金额、0.5% 手续费、实际到账与净盈亏。
- **加仓小天才**：补仓后的摊薄均价、回本所需涨幅与理论回本月数。
- **多用户与权限**：邀请码注册（可用 `INVITE_REQUIRED=false` 关闭），首个用户为管理员；管理员可生成邀请码、维护日结价。
- **部署**：Docker Compose + 自备 Nginx/HTTPS，SQLite 落 volume。

---

## 本地运行

```bash
pip install -r requirements.txt
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/`。默认 `INVITE_REQUIRED=true`，**首个注册用户自动成为管理员**；本地调试可设环境变量关掉邀请码：

```powershell
$env:INVITE_REQUIRED="false"; python -m uvicorn server.main:app --port 8000
```

数据库默认在 `data/gold.db`（可用 `DB_PATH` 覆盖）。前端无需 npm 构建，Vue 3 与 ECharts 已放在 `web/vendor/`。

---

## 部署到自己的服务器

服务器上只需要这些文件（旧桌面版、`data/`、`.git/`、打包产物都不用传）：

```
app/   server/   web/（含 vendor/，本地 Vue/ECharts）   scripts/
requirements.txt   .dockerignore   deploy/
```

`deploy/` 的上一级必须是完整项目根，否则构建会报 `"/web": not found`。

```bash
cd deploy
cp .env.example .env        # 按下表修改
docker compose up -d --build
```

| `.env` 项 | 说明 |
| --- | --- |
| `SECRET_KEY` | 签发登录 Cookie 的密钥，换成随机长串：`openssl rand -hex 32` |
| `COOKIE_SECURE` | HTTPS 用 `true`；纯 HTTP 调试必须 `false`，否则登录后立刻掉线 |
| `INVITE_REQUIRED` | 是否要求邀请码注册。**首个账号也需要邀请码**，首次部署先设 `false`，注册管理员后再改回 |
| `DB_PATH` | 容器内数据库路径，默认 `/app/data/gold.db` |

- 应用只监听 `127.0.0.1:8000`，**用 `http://公网IP:8000` 是访问不通的**：由宿主机 Nginx 终止 HTTPS 并反代，示例见 [deploy/nginx.example.conf](deploy/nginx.example.conf)。临时调试可把 compose 端口改为 `"8000:8000"` 并放行防火墙/安全组，验完改回。
- **SSE 必须关闭缓冲**（示例里已配 `proxy_buffering off`），否则行情会攒着一起发。
- 引擎要求单 worker；容器内已固定 `--workers 1`。
- 时区固定 `Asia/Shanghai`，日结与「今日」判定不受宿主机时区影响。
- 数据目录 `../data` 挂载进容器，重启不丢。

### 常用运维

```bash
docker compose up -d            # 改 .env / compose 后：自动重建容器，无需先 down
docker compose up -d --build    # 改代码后：重新构建镜像
docker compose restart          # 只重启进程，不重新读 .env
docker compose logs -f --tail=100
```

### 忘记密码 / 查看账号

```bash
# 查看已有账号
docker compose exec gold-monitor python -c "from server import db; print(db.fetch_all('SELECT id,username,is_admin FROM users'))"

# 交互式重置密码（不留 shell 历史）
docker compose exec -it gold-monitor python -c "import getpass; from server import db, auth; u=input('用户名: '); p=getpass.getpass('新密码: '); h,s=auth.hash_password(p); print('已重置' if db.execute_rowcount('UPDATE users SET pwd_hash=?, salt=? WHERE username=?',(h,s,u)) else '用户名不存在')"
```

---

## 导入旧桌面版数据

旧版 `gold_monitor.py` 会把数据写在同目录的 JSON 里。先注册账号，再执行：

```bash
python scripts/import_legacy.py --user 你的用户名 --dir . --dry-run   # 先看统计
python scripts/import_legacy.py --user 你的用户名 --dir .
```

- `config.json` → 设置、持仓、交易、补仓计划、微信配置
- `settle_prices.json` → 日结价（全局；人工价优先于引擎价与估算价）
- `price_history.json` → 本地分时
- 可重复执行：交易按日期/克数/金额去重，计划按目标价去重。

Docker 部署时在容器里执行：先把旧版退出，将三个 JSON 上传到项目根的 `data/legacy/`（该目录已挂载进容器），然后：

```bash
docker compose -f deploy/docker-compose.yml exec gold-monitor \
  python scripts/import_legacy.py --user 你的用户名 --dir /app/data/legacy --dry-run
docker compose -f deploy/docker-compose.yml exec gold-monitor \
  python scripts/import_legacy.py --user 你的用户名 --dir /app/data/legacy
```

导入会**覆盖该账号的设置与持仓**；网页核对持仓、交易、盈亏无误后删除 `data/legacy/`（内含微信 secret）。此后以网页版为准，不要再用旧桌面版记交易。

---

## 目录结构

```
app/                共享内核（无 Web 依赖）
├─ constants.py     常量、数据源地址、默认配置、费率
├─ icbc.py          工行报价
├─ eastmoney.py     东财黄金9999 分时/日K
├─ tiers.py         档位穿越、交易时段
├─ settle.py        22:20 日结状态机
├─ portfolio.py     每日盈亏重算
├─ wechat.py        微信模板消息
└─ timeutil.py      UTC+8 时间口径

server/             FastAPI
├─ main.py          入口、CSRF 头校验、静态文件
├─ db.py            SQLite（WAL）与建表
├─ auth.py          账号、scrypt、签名 Cookie
├─ engine.py        全局轮询引擎 + SSE 广播
└─ routers/         auth market portfolio trades plans settings wx admin pl stream

web/                Vue 3 无构建前端（vendor 内置 Vue/ECharts）
scripts/            import_legacy.py
deploy/             Dockerfile / compose / .env 示例 / Nginx 示例
gold_monitor.py     旧桌面版，保留作对照
```

---

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/register` `/api/auth/login` `/api/auth/logout` | 注册 / 登录 / 退出 |
| GET | `/api/market/snapshot` | 当前价、时段、日结状态 |
| GET | `/api/market/chart?mode=trend\|daily\|local` | 分时 / 日K / 本地分时 |
| GET | `/api/stream` | SSE：`tick` `alert` `plan` `settle_locked` `market_state` |
| GET/PUT | `/api/position` | 持仓 |
| GET/POST/DELETE | `/api/trades` `/api/trades/{id}` | 交易 |
| GET | `/api/daily_pl?from=&to=` | 每日盈亏 |
| GET/PUT | `/api/plans` | 补仓计划 |
| GET/PUT | `/api/settings` | 每用户设置 |
| GET/PUT/POST | `/api/wx` `/api/wx/test` | 微信推送（appsecret 不回传） |
| GET/PUT | `/api/settles` `/api/settles/{day}` | 日结价（写仅管理员） |
| GET/POST | `/api/admin/invites` | 邀请码 |

所有非 GET 的 `/api/*` 请求必须带 `X-Requested-With: fetch`（配合 SameSite Cookie 防 CSRF）。

---

## 数据来源

| 用途 | 来源 |
| --- | --- |
| 积存金 / 如意金 / 账户黄金 | 工行 `icbcphp.icbc.com.cn`（旧 TLS 需放开 `OP_LEGACY_SERVER_CONNECT`） |
| 上金所 Au99.99 现价 | 工行 m 站 `papi.icbc.com.cn` |
| 今日分时 / 历史日K | 东方财富 `push2his.eastmoney.com`，`secid=118.AU9999` |

---

## 常见问题

- **登录成功又跳回登录页**：不是密码错，是 Cookie 没写入。HTTP 访问时 `.env` 里必须 `COOKIE_SECURE=false`，改完 `docker compose up -d`。
- **提示「尝试过于频繁」**：登录接口按 IP 节流，连续失败 5 次锁 5 分钟；等待或 `docker compose restart`。
- **构建报 `failed to compute cache key: "/web": not found`**：构建上下文里没有 `web/`。确认在完整项目根下的 `deploy/` 中执行，并且 `web/`（含 `vendor/`）已上传。
- **走势图偶发 502 / 正在取数很久**：东财边缘节点会抖动式掐 TCP（`RemoteDisconnected`），服务端已做退避重试并缓存上次成功数据；刷新即可，不影响主报价与 SSE。
- **日结价怎么来**：每个交易日 22:20 起观察赎回价，连续 3 次相同即定格；22:35 硬截止强制写入。管理员可在「设置 → 日结价维护」补录或覆盖（人工价优先）。
- **收不到微信**：需公众号测试号的 appID / appsecret / openid / 模板 ID，且模板内容包含 `content.DATA`；配置保存后点「发送测试」验证。
- **换服务器**：备份 `data/` 即可，账号与数据都在 SQLite 里。

---

## 免责声明

本项目仅用于个人看盘与技术学习，行情数据来自公开网络接口，可能存在延迟、缺失或变动。黄金价格波动风险自负，程序中的盈亏测算均为数学参考，**不构成任何投资建议**。赎回手续费率、到账时间等业务规则以中国工商银行官方最新公告为准。
