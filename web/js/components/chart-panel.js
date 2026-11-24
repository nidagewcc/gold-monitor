/* 走势图：今日分时 / 历史日K / 本地采集分时，叠加成本线与补仓计划线 */
window.ChartPanel = {
  props: ["position", "privacy", "source"],
  data() {
    return {
      mode: "trend",
      loading: true,
      error: "",
      labels: [],
      // daily 模式下与 labels 等长的完整 YYYY-MM-DD，用于成交点定位，避免 MM-DD 跨年撞键
      fullLabels: [],
      values: [],
      preClose: null,
      localDays: [],
      localDay: "",
      planPrices: [],
      trades: [],
      trendDate: "",
      chart: null,
      timer: null,
      // 取数代际序号：每次 load 自增，只有最新一次请求允许写响应式状态与渲染，
      // 旧请求（切模式/切日期/定时器交错产生）回来后一律忽略，避免旧数据或旧错误覆盖新状态
      loadSeq: 0,
      drawQueued: false,
    };
  },
  computed: {
    costPx() {
      const g = Number((this.position && this.position.grams) || 0);
      const c = Number((this.position && this.position.cost) || 0);
      return g > 0 ? c / g : 0;
    },
  },
  watch: {
    mode() { this.load(); },
    privacy() { this.scheduleDraw(); },
    // 数据变化后由 watcher 统一负责重绘，与 load 的取数流程解耦：
    // 无论 load 的 finally 处于什么时序，只要 values/labels 更新且容器可见，就一定补一次渲染，
    // 根治“数据已更新但 ECharts 实例停留在旧图”的问题
    values() { this.scheduleDraw(); },
    labels() { this.scheduleDraw(); },
    // loading 由 true 变 false 时容器才从 display:none 恢复，此时再尝试一次，兜住首屏/切模式后的渲染
    loading(v) { if (!v) this.scheduleDraw(); },
  },
  mounted() {
    window.addEventListener("resize", this.resize);
    this.loadPlans();
    this.loadTrades();
    this.load();
    this.timer = setInterval(() => this.load(true), 180000);
  },
  beforeUnmount() {
    clearInterval(this.timer);
    window.removeEventListener("resize", this.resize);
    if (this.chart) this.chart.dispose();
  },
  methods: {
    resize() {
      if (this.chart) this.chart.resize();
      else this.scheduleDraw();
    },
    async loadPlans() {
      try {
        const rows = await api.get("/api/plans");
        this.planPrices = rows.map((r) => Number(r.price)).filter((p) => p > 0);
      } catch (e) { /* 忽略 */ }
    },
    async loadTrades() {
      try {
        this.trades = await api.get("/api/trades");
      } catch (e) { this.trades = []; }
      this.scheduleDraw();
    },
    // 把买卖成交映射到当前图的横轴位置
    tradePoints() {
      const buy = [];
      const sell = [];
      if (this.privacy || !this.trades.length || !this.labels.length) return { buy, sell };
      const idx = new Map();
      this.labels.forEach((l, i) => { if (!idx.has(l)) idx.set(l, i); });
      const toMin = (s) => {
        const m = /(\d{1,2}):(\d{2})/.exec(s || "");
        return m ? Number(m[1]) * 60 + Number(m[2]) : -1;
      };
      const near = (hm) => {
        const t = toMin(hm);
        let best = -1;
        let bd = 1e9;
        this.labels.forEach((l, i) => {
          const v = toMin(l);
          if (v < 0) return;
          const d = Math.abs(v - t);
          if (d < bd) { bd = d; best = i; }
        });
        return best;
      };
      this.trades.forEach((t) => {
        const px = Number(t.price);
        if (!(px > 0)) return;
        let i = -1;
        if (this.mode === "daily") {
          // 用完整 YYYY-MM-DD 建索引，避免横跨整年时 MM-DD 跨年重复导致定位到上一年
          const keys = this.fullLabels.length ? this.fullLabels : this.labels;
          const key = (t.date || "").slice(0, 10);
          i = keys.indexOf(key);
        } else {
          const day = this.mode === "trend" ? this.trendDate : this.localDay;
          if ((t.date || "") !== day) return;
          const hm = (t.ts || "").slice(11, 16);
          if (!hm) return;
          i = idx.has(hm) ? idx.get(hm) : near(hm);
        }
        if (i < 0) return;
        (t.kind === "sell" ? sell : buy).push({ value: [i, px], raw: t });
      });
      return { buy, sell };
    },
    setMode(m) {
      if (this.mode === m) return;
      this.mode = m;
      // 切模式后 series 结构（面积、散点数量、类目轴长度）变化大，先销毁旧实例，
      // 让下一次 draw 全新 init，规避 notMerge 下旧 series 残留引发的 ECharts 内部 reset 异常
      if (this.chart) { this.chart.dispose(); this.chart = null; }
    },
    async load(quiet) {
      // 取数代际：每次 load 自增并捕获本次序号，await 回来后只有“仍是最新请求”才允许写状态。
      // 用纯序号而非维度对象，避免 local 分支回写 localDay 造成的自我污染（首屏空 day 被回填后误判过期）。
      const seq = ++this.loadSeq;
      const isCurrent = () => seq === this.loadSeq;
      if (!quiet) { this.loading = true; this.error = ""; }
      try {
        if (this.mode === "trend") {
          const d = await api.get("/api/market/chart?mode=trend", 45000);
          if (!isCurrent()) return;
          this.fullLabels = [];
          this.applyTrend(d);
        } else if (this.mode === "daily") {
          const d = await api.get("/api/market/chart?mode=daily", 45000);
          if (!isCurrent()) return;
          const rows = d.rows || [];
          this.fullLabels = rows.map((r) => r[0]);
          this.labels = rows.map((r) => r[0].slice(5));
          this.values = rows.map((r) => r[2]);
          this.preClose = null;
        } else {
          const q = this.localDay ? `&day=${this.localDay}` : "";
          const d = await api.get(`/api/market/chart?mode=local${q}`, 45000);
          if (!isCurrent()) return;
          this.fullLabels = [];
          this.localDays = d.days || [];
          // 仅在没有已选日期（首屏进入）时用后端返回的最新日初始化，避免回写覆盖用户刚选择的日期
          if (!this.localDay) this.localDay = d.day || "";
          const pts = d.points || [];
          this.labels = pts.map((p) => p[0]);
          this.values = pts.map((p) => p[1]);
          this.preClose = null;
        }
      } catch (e) {
        if (!isCurrent()) return;
        this.error = e.message;
        this.labels = [];
        this.values = [];
      } finally {
        // 过期请求不触碰任何状态；loading 收尾与重绘都只由最新请求负责
        if (!isCurrent()) return;
        this.loading = false;
        // 重绘已由 values/labels 的 watcher（scheduleDraw）统一驱动，这里不再做一次性调度，
        // 避免 finally 时序下容器尺寸为 0 导致 draw 被守卫拦下后再无补渲染机会
      }
    },
    // 在下一帧 DOM 布局完成后重绘；同一轮内多次触发只排一次，避免重复 init/setOption
    scheduleDraw() {
      if (this.drawQueued) { console.log("[DBG] scheduleDraw skip: already queued"); return; }
      this.drawQueued = true;
      console.log("[DBG] scheduleDraw enqueue", { mode: this.mode, loading: this.loading, vlen: this.values.length });
      this.$nextTick(() => requestAnimationFrame(() => requestAnimationFrame(() => {
        this.drawQueued = false;
        const el = this.$refs.box;
        console.log("[DBG] raf fire", {
          loading: this.loading, error: this.error, vlen: this.values.length,
          hasEl: !!el, w: el && el.clientWidth, h: el && el.clientHeight,
          hasChart: !!this.chart, chartDisposed: this.chart && this.chart.isDisposed()
        });
        if (this.loading || this.error || !this.values.length) return;
        // 容器尚不可见（display:none 或尺寸为 0）时跳过本次，等待下一次数据/状态变化再补
        if (!el || !el.clientWidth || !el.clientHeight) return;
        if (this.chart) this.chart.resize();
        this.draw();
        console.log("[DBG] draw called, instLen=", echarts.getInstanceByDom(el).getOption().series[0].data.length);
      })));
    },
    applyTrend(d) {
      const all = d.points || [];
      const dayDays = [...new Set(all.filter((p) => p[0].slice(11, 16) >= "09:00")
        .map((p) => p[0].slice(0, 10)))].sort();
      const date = dayDays.length ? dayDays[dayDays.length - 1]
        : (all.length ? all[all.length - 1][0].slice(0, 10) : "");
      const pts = all.filter((p) => {
        const hm = p[0].slice(11, 16);
        return p[0].slice(0, 10) === date &&
          ((hm >= "08:59" && hm <= "15:31") || (hm >= "19:59" && hm <= "22:20"));
      });
      this.labels = pts.map((p) => p[0].slice(11, 16));
      this.values = pts.map((p) => p[1]);
      this.preClose = d.pre_close ? Number(d.pre_close) : null;
      this.trendDate = date;
    },
    draw() {
      const el = this.$refs.box;
      if (!el || !el.clientWidth || !el.clientHeight) return;
      if (!this.chart || this.chart.isDisposed()) {
        this.chart = echarts.init(el);
      }
      const css = getComputedStyle(document.documentElement);
      const col = (n) => css.getPropertyValue(n).trim() || "#888";
      const accent = col("--accent");
      const txt3 = col("--txt-3");
      const line = col("--line");

      const marks = [];
      if (!this.privacy && this.costPx > 0) {
        marks.push({
          yAxis: this.costPx,
          lineStyle: { color: txt3, type: "dashed", width: 1 },
          label: { formatter: `成本 ${this.costPx.toFixed(2)}`, color: txt3 },
        });
      }
      if (!this.privacy) {
        this.planPrices.forEach((p) => {
          marks.push({
            yAxis: p,
            lineStyle: { color: accent, type: "dashed", width: 1, opacity: 0.6 },
            label: { formatter: `计划 ${p}`, color: accent },
          });
        });
      }
      if (this.preClose) {
        marks.push({
          yAxis: this.preClose,
          lineStyle: { color: txt3, type: "dotted", width: 1 },
          label: { formatter: `昨收 ${this.preClose.toFixed(2)}`, color: txt3 },
        });
      }

      const step = Math.max(1, Math.ceil(this.labels.length / 8));
      const pts = this.tradePoints();
      const extra = [];
      if (pts.buy.length) {
        extra.push({
          type: "scatter", name: "补仓", data: pts.buy,
          symbol: "triangle", symbolSize: 10, symbolOffset: [0, 8], z: 6,
          itemStyle: { color: col("--up"), borderColor: col("--card"), borderWidth: 1 },
        });
      }
      if (pts.sell.length) {
        extra.push({
          type: "scatter", name: "减仓", data: pts.sell,
          symbol: "triangle", symbolRotate: 180, symbolSize: 10, symbolOffset: [0, -8], z: 6,
          itemStyle: { color: col("--down"), borderColor: col("--card"), borderWidth: 1 },
        });
      }
      const opt = {
        animation: false,
        grid: { left: 58, right: 72, top: 24, bottom: 40 },
        tooltip: {
          trigger: "axis",
          backgroundColor: col("--card"),
          borderColor: line,
          textStyle: { color: col("--txt"), fontSize: 12 },
          formatter: (ps) => {
            const main = ps.filter((p) => p.seriesType === "line")[0] || ps[0];
            let s = `${main.axisValue}<br/>价格 <b>${Number(main.data).toFixed(2)}</b> 元/克`;
            ps.filter((p) => p.seriesType === "scatter").forEach((p) => {
              const t = p.data.raw;
              const g = Number(t.grams).toFixed(4);
              const px = Number(t.price).toFixed(2);
              s += `<br/>${t.kind === "sell" ? "减仓" : "补仓"} ${g} 克 @ ${px}`;
              if (t.pl !== null && t.pl !== undefined) {
                s += `（${Number(t.pl) >= 0 ? "+" : "-"}${Math.abs(Number(t.pl)).toFixed(2)} 元）`;
              }
            });
            return s;
          },
        },
        xAxis: {
          type: "category",
          data: this.labels,
          boundaryGap: false,
          axisLine: { lineStyle: { color: line } },
          axisTick: { show: false },
          axisLabel: { color: txt3, fontSize: 11, interval: step },
        },
        yAxis: {
          type: "value",
          scale: true,
          axisLabel: { color: txt3, fontSize: 11 },
          splitLine: { lineStyle: { color: line } },
        },
        series: [{
          type: "line",
          data: this.values,
          showSymbol: false,
          lineStyle: { width: 1.8, color: accent },
          areaStyle: this.mode === "trend" ? {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: this.rgba(accent, 0.22) },
              { offset: 1, color: this.rgba(accent, 0.02) },
            ]),
          } : undefined,
          markLine: {
            silent: true,
            symbol: "none",
            label: { position: "insideEndTop", fontSize: 11 },
            data: marks,
          },
        }, ...extra],
      };
      try {
        this.chart.setOption(opt, true);
      } catch (e) {
        // 实例内部状态损坏时（旧 series 残留导致 reset 异常），重建实例后重试一次
        console.error("走势图渲染失败，重建实例后重试：", e);
        this.chart.dispose();
        this.chart = echarts.init(el);
        this.chart.setOption(opt, true);
      }
    },
    rgba(hex, a) {
      const h = hex.replace("#", "");
      if (h.length !== 6) return `rgba(0,95,184,${a})`;
      const r = parseInt(h.slice(0, 2), 16);
      const g = parseInt(h.slice(2, 4), 16);
      const b = parseInt(h.slice(4, 6), 16);
      return `rgba(${r},${g},${b},${a})`;
    },
    title() {
      if (this.mode === "trend") return "黄金9999 · 今日分时";
      if (this.mode === "daily") return "黄金9999 · 历史日K（收盘价）";
      return `本地分时 · ${this.localDay || "暂无采集"}`;
    },
  },
  template: `
    <div class="card panel">
      <div class="panel-head">
        <h3>{{ title() }}</h3>
        <div class="row" style="gap:8px">
          <div class="seg">
            <button :class="{on: mode === 'trend'}" @click="setMode('trend')">今日分时</button>
            <button :class="{on: mode === 'daily'}" @click="setMode('daily')">历史日K</button>
            <button :class="{on: mode === 'local'}" @click="setMode('local')">本地分时</button>
          </div>
          <select v-if="mode === 'local' && localDays.length" class="input" style="min-height:34px;width:130px"
                  v-model="localDay" @change="load()">
            <option v-for="d in localDays" :key="d" :value="d">{{ d }}</option>
          </select>
          <button class="btn ghost sm" @click="load()">刷新</button>
        </div>
      </div>

      <div v-if="loading" class="empty">
        正在取数…<span class="hint">（在线行情偶发连接被掐，会自动重试）</span>
      </div>
      <div v-else-if="error" class="empty">
        行情获取失败：{{ error }}<br>
        <span class="hint">在线数据来自东方财富黄金9999；休市或网络受限时可切「本地分时」。</span>
      </div>
      <div v-else-if="!values.length" class="empty">
        暂无数据。周末/节假日无在线分时；本地分时需引擎在交易时段运行后逐步积累。
      </div>
      <div ref="box" class="chart-box" v-show="!loading && !error && values.length"></div>

      <p class="hint" style="margin:12px 0 0">
        虚线：灰色为持仓成本与昨收，蓝色为补仓计划价。三角标记为成交点位：红色朝上为补仓，绿色朝下为减仓。在线行情为黄金9999（Au99.99），与积存金走势基本重合。
      </p>
    </div>`,
};
