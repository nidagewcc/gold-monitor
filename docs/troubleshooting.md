# Web 端排障手册

记录 B/S 版排查过的真实问题、取证过程与工具链约定，避免重复摸索。新问题按「现象 → 排除法 → 根因 → 修复 → 验证」的结构追加。

---

## 一、走势图切换模式后图表内容不更新（2026-11-17，commit f95c99b）

### 现象

点击「今日分时 / 历史日K / 本地分时」切换后，标题和按钮高亮正确、网络请求 200，但 ECharts 画布仍显示上一个模式的数据，用户感知为「今日分时和历史日K长得一模一样」。浏览器控制台间歇报错：

```
Uncaught TypeError: Cannot read properties of undefined (reading 'type')
    at Object.reset (echarts.min.js:45)
```

故障间歇性：同一页面有时切换成功，有时失败，失败后画面永久停留旧图。

### 排查链路（排除法，每步都有证据）

1. **后端数据源排除**：`server/routers/market.py` 的 `/api/market/chart` 三个分支分别走东财 `trends2`（分时，约 300 个分钟点）与 `kline`（日K，约 246 个交易日）接口，URL、解析函数、TTL 均不同；实测返回数据迥异。
2. **网络层排除**：浏览器 network 面板确认 daily 请求全部 200 且返回行结构正确。
3. **线上版本排除**：对比服务器与本地 `chart-panel.js` 内容、长度一致，不是部署旧文件。
4. **容器尺寸排除**：故障态 `.chart-box` 实测 581×320，`draw()` 的尺寸守卫没有误拦。
5. **手动复现失败**：在控制台用真实 daily 数据手动 `setOption`（最简 line、markLine、含散点、先分时后日K 的完整 notMerge 序列）全部成功——说明问题不在单次调用的数据形状，而在组件真实运行时的调用时序。
6. **锁定前端渲染期竞态**：梳理 `chart-panel.js` 的全部 draw 触发源——`watch mode` 的 load、180 秒定时器静默 `load(true)`、`loadTrades()` 回调里的 `this.$nextTick(() => this.draw())`、load 的 finally 里双 `requestAnimationFrame` 调度的 draw——多条异步路径并发，且 `load()` 对返回响应不做任何代际校验。

### 根因（两个缺陷叠加）

1. **异步响应无代际保护（主因）**：`load()` 发出请求后，返回时无条件写入 `this.labels/values`。东财边缘节点会掐 TCP 并退避重试（见 `app/eastmoney.py` 注释），慢请求可能比后发的请求更晚返回。例如定时器的分时请求在途时用户切到日K，分时慢响应最后返回并覆盖数据，于是按钮显示「历史日K」、图里画的却是分时。
2. **ECharts 实例内部状态损坏不自愈**：分时（2 个 series、带面积渐变）切到日K（3 个 series、无面积、类目轴从分钟变日期）时，`setOption(opt, true)` notMerge 下内部 series reset 偶发抛 `undefined.type`；一次抛错后该实例后续所有 setOption 都失败，画面永久卡死。

### 修复（web/js/components/chart-panel.js）

- **代际令牌**：data 增加 `loadSeq`，每次 `load()` 开头 `const seq = ++this.loadSeq`；三个分支的 await 之后、catch、finally 以及双 rAF 回调内都校验 `seq !== this.loadSeq` 即直接 return，过期响应不碰任何数据与渲染状态。思路与旧桌面版 `gold_monitor.py` 的 `_chart_seq` 一致。
- **切模式重建实例**：`setMode()` 中先 `chart.dispose(); chart = null`，下次 draw 全新 `echarts.init`，规避 series 结构切换的内部 reset 异常。
- **draw 自愈**：把 option 抽成局部变量 `opt`，`setOption` 包 try/catch；捕获异常后 dispose + 重新 init 再重试一次，并 `console.error` 留痕。

### 验证方法（浏览器自动化）

通过 ECharts 实例反查真实渲染状态，比截图可靠：

```js
const el = document.querySelector('.chart-box');
const o = echarts.getInstanceByDom(el).getOption();
// 核对：o.series.length、o.series[0].data.length、o.xAxis[0].data[0]/末项、!!o.series[0].areaStyle
```

三模式基准（2026-11-17 实测）：分时 303 点、类目 `09:01` 起、有面积、2 series；日K 246 点、类目 `11-12` 起、无面积、3 series；本地 294 点、`09:10` 起。另做快速连点（日K→分时→日K）竞态压测，最终状态必须与最后点击的模式一致；`browser_console_messages` 全程为空数组。

### 调试中踩过的坑（下次直接绕行）

- 生产构建 `echarts.ECharts` 不暴露，`echarts.ECharts.prototype` 是 undefined；要 hook 实例方法用 `Object.getPrototypeOf(echarts.getInstanceByDom(el))`，其 `constructor.name` 为压缩后的 `"e"`。
- 组件 dispose + init 会重建实例，实例级 hook 随之丢失；要持续捕获需 hook `echarts.init` 包装新实例。
- `browser_evaluate` 裸 `return` 偶发 `Illegal return statement`，脚本用 IIFE 包裹最稳。
- Exec（code_mode）沙箱里没有 `window`/`document` 全局，浏览器全局操作必须放进 `browser_evaluate` 的脚本内，不能写在 Exec 顶层。
- Exec 拼接浏览器脚本时 `\n` 容易被错误转义导致 `Invalid or unexpected token`，多行用 `lines.join(String.fromCharCode(10))` 规避。
- 读取 hook 捕获的 option 时，series 数组要先 `.filter(Boolean)` 再 map，压缩/空洞元素会让读取脚本自己抛错。

---

## 二、grid2 卡片间距与通用 .panel + .panel 规则（2026-11-17，commit ba79f8a）

`.panel + .panel { margin-top: 18px; }` 是全站 10 个堆叠面板依赖的通用垂直间距规则，不能删。设置页等纵向面板仍由它提供 18px 间距；行情页 `.grid2` 两列布局内，用 `.grid2 > .card { margin-top: 0; }` 在作用域内重置，间距完全交给 grid 的 `gap: 18px`。验证手段：`getComputedStyle(el).marginTop`，grid2 内卡片为 `0px`、设置页面板为 `18px`。

---

## 三、工具链与环境约定

### 本机 PowerShell 5 语法坑

- 不支持 `&&` 链式命令，用分号 `;` 或 `if ($LASTEXITCODE -eq 0) {...}`。
- 不支持 bash heredoc（`<<'EOF'`）。多行 git 提交信息改为写临时文件再 `git commit -F 文件`，提交后删除临时文件。
- 中文命令报错输出可能是 GBK 乱码，不影响退出码判断。

### 静态资源破缓存

前端无构建，`web/index.html` 里所有 CSS/JS 引用带 `?v=YYYYMMDDx` 查询串。任何 `web/` 下文件改动后，必须把 index.html 中**全部**资源版本号统一递增（当前 `20261117d`），否则浏览器沿用旧缓存、线上看似「没生效」。

### 部署链路（192.168.3.103）

- 远端项目目录：`/data/gold-monitor`；compose 文件：`deploy/docker-compose.yml`；容器名 `gold-monitor`，端口 8000。
- 标准更新：`git fetch origin main && git reset --hard origin/main` → `docker compose -f deploy/docker-compose.yml up -d --build` → `ps` 确认 Up。
- 本机用 paramiko 临时脚本 SSH（root）执行；脚本含明文密码，**跑完必须删除**，不得提交 git（仓库历史也不能留）。
- 数据在挂载卷 `../data`（容器内 `/app/data`），重建容器不丢。

### 浏览器自动化（TRAE-browseruse）要点

- 所有 `browser_*` 工具必须在 code_mode 的 Exec 内 `await tools.xxx()` 调用；仅 `browser_waiting_for_user_interaction` 独立调用。
- 同一 Exec 内浏览器调用串行，禁止 `Promise.all`（共享 tab/ref 状态会竞态）。
- 工具返回带 `Browser View ID...` 包装头，不是纯 JSON，Exec 内不要对返回值直接 `JSON.parse`；用 `r.content[0].text` 取正文后再处理，或直接 `text()` 输出。
- ref 在导航/AJAX 重渲染后会失效，点击失败先重新 `browser_snapshot` 取新 ref。
- 硬上限：同一任务 30 次浏览器 Exec 或 10 分钟，到点收尾汇报。

---

## 四、待处理小项

- `web/js/components/calc-panel.js` 的「全部」按钮填充持仓克数时存在浮点尾巴（如 `53.43000000000001`），建议输出统一 `toFixed(4)`；已向用户提出两次，待拍板。
