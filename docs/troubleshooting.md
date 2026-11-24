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

> 后续进展（2026-11-24，见第五节）：本节的 `loadSeq` 代际令牌只解决了「慢响应覆盖新数据」，并未覆盖「数据已正确写入但 ECharts 实例没重绘」这一类问题；`setMode` 里 dispose 重建也只在切模式时生效，本地分一切日期复用同一实例，最终暴露的是 `resize()` 抛异常中断渲染。排查同类「图不更新」时，务必先区分是**数据没写对**还是**写对了没画出来**。

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

前端无构建，`web/index.html` 里所有 CSS/JS 引用带 `?v=YYYYMMDDx` 查询串。任何 `web/` 下文件改动后，必须把 index.html 中对应资源的版本号递增（当前 chart-panel.js 为 `20261124d`），否则浏览器沿用旧缓存、线上看似「没生效」。

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

---

## 五、本地分一切换日期图表不更新 + 首屏卡「正在取数」（2026-11-24，commit 9b35157）

### 现象

本地分时模式下，日期下拉切到别的交易日，标题与下拉选中值都变了，网络请求 200，但 ECharts 画布仍停留在切之前那天的数据，用户感知「本地分时每天长得都一样」。另外首次点进本地分时会一直卡在「正在取数…」，需手动点刷新才出图。控制台在失败瞬间有一条报错：

```
Uncaught TypeError: Cannot read properties of undefined (reading 'type')
    at echarts.min.js:45
```

### 排查链路（关键是先分清「数据错」还是「没重绘」）

1. **后端排除**：`market.py` 的 local 分支按 `day` 参数查 `history` 表，线上库 7 个交易日每天 791 点、价格区间各异；直接 fetch `mode=local&day=2026-11-17` 返回正确。
2. **请求 URL 取证**：`browser_network_requests` 看到切换后请求**确实带了正确的 `day=2026-11-17`**，推翻了「v-model 时序导致 day 还是旧值」的假设。
3. **组件 data 取证（决定性）**：通过 `document.getElementById('app').__vue_app__._container._vnode.component` 递归子树找到 ChartPanel 实例，发现响应式 `data.values` 已是 11-17 的 791 点（824.88–834.6），而 `echarts.getInstanceByDom(box).getOption().series[0].data` 还是旧日的 769.14 区间。**数据链路全对，问题在渲染没刷进实例。**
4. **受控实验**：在同一浏览器上下文手动调一次组件的 `draw()`，图表立刻从 769 跳到 824——证明数据没问题，是自动链路没走到 `setOption`。
5. **临时埋点定位中断点**：在 `scheduleDraw` 的 RAF 回调里打 `console.log`，复现后看到回调先执行 `this.chart.resize()`，随即抛出上面的 `undefined.type`，异常未捕获、整个回调中断，后面的 `this.draw()` 永远不执行。

> 取证时一个误导点：跨 `browser_evaluate` 调用比较 `proxy.chart === getInstanceByDom(box)` 会得到 false，但两者 `id` 相同、`getDom()` 同指一个节点——这是评估上下文对象包装导致的身份比较失真，**不是真的有两个实例**。判断实例唯一性用 `inst.id` 和 `getDom()`，别用跨上下文的 `===`。

### 根因（三处叠加）

1. **resize 异常中断渲染（主因）**：本地分一切日期复用同一 ECharts 实例（`setMode` 的 dispose 重建只在切模式时触发），notMerge 下 series 结构（散点、markLine、类目长度）变化后内部 model 残留，`resize()` 触发内部 reset 时遍历到失效 series 项抛 `undefined.type`。旧代码在 `scheduleDraw` 里把 `resize()` 放在 `draw()` 之前且无 try/catch，一抛错 `setOption` 就被跳过。
2. **渲染只靠一次性调度，无补渲染机制**：`load()` 的 finally 用「双 requestAnimationFrame 调一次 draw」，这一帧若被守卫或异常跳过，之后没有任何 watcher/重试再触发，画面永久停留旧图。
3. **维度令牌 stale 自我污染（首屏卡死）**：旧 `loadToken={mode,day}` 的 `stale()` 依赖 `this.localDay`，而 local 分支会用响应回填 `this.localDay`。首屏进入本地分时 token 捕获的是空 day（data 初始值 `""`），响应回填最新日后 finally 里 `stale()` 判定 `"" !== "2026-11-24"` 为真直接 return，`loading` 永不复位、图不渲染。

### 修复（web/js/components/chart-panel.js）

- **resize 容错并下沉到 draw**：`scheduleDraw` 不再单独 resize，只负责触发 `draw()`；`draw()` 内对已存在实例先 try `resize()`，抛错即 `dispose()` 后重新 `echarts.init`，保证后续 `setOption` 在干净实例上必然执行。window 的 `resize()` 入口同样 try/catch，失败降级为重建式重绘。
- **渲染与取数解耦**：新增 `scheduleDraw()`（同轮去重 + 双 RAF 等布局），由 `values/labels/loading/privacy` 的 watcher 统一驱动；只要数据变且容器可见就一定补一次重绘，取代 finally 的一次性调度。
- **代际控制改回纯序号 `loadSeq`**：每次 load 自增，await 回来后 `seq !== this.loadSeq` 即丢弃，免疫任何字段回写污染，也防旧请求的错误/空数据覆盖新状态。
- **localDay 仅首屏初始化**：local 分支只在 `this.localDay` 为空时用后端返回的最新日赋值，不再回写覆盖用户已选日期。
- index.html 版本号一路递增到 `20261124d` 破缓存（期间 b/c 为中间验证版，c 带临时埋点，已在 d 中移除）。

### 验证

通过 ECharts 实例反查 + 组件 data 双向比对（`Math.min(values) === Math.min(instSeriesData)` 即 `match`）：

- 首屏进本地分时不再卡「正在取数」，loading 正常复位、图直接渲染。
- 11-17 / 11-23 / 11-13 互切，实例数据随日期真实变化（824.88 / 769.14 / 834.38 三个区间分明），每次 `match=true`。
- 今日分时 / 历史日K / 本地分时三模式互切不回退；刷新按钮后数据保持。
- `browser_console_messages` 全程零 error、零 warning。

### 教训

- 「图不更新」要先做**数据层与渲染层的分离取证**：读组件响应式 data 对比 ECharts 实例 option，能立刻区分是请求/写入问题还是 setOption 没跑，避免在令牌、v-model 时序等方向上空转（本次前两次修复 loadSeq→loadToken 都改错了层）。
- 任何在异步回调里、`draw/setOption` 之前的前置调用（典型如 `resize()`）都必须容错，否则一次抛错会让真正的渲染语句永远到不了。
- 一次性 RAF 调度不可靠，响应式框架里优先用 watcher 驱动渲染，让「数据变 → 必然重绘」成为不变量。
