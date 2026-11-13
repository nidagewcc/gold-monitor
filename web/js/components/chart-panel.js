/* 走势图：今日分时 / 历史日K / 本地采集分时，叠加成本线与补仓计划线 */
window.ChartPanel = {
  props: ["position", "privacy", "source"],
  data() {
    return {
      mode: "trend",
      loading: true,
      error: "",
      labels: [],
      values: [],
      preClose: null,
      localDays: [],
      localDay: "",
      planPrices: [],
      trades: [],
      trendDate: "",
      chart: null,
      timer: null,
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
    privacy() { this.draw(); },
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
      else this.draw();
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
      this.$nextTick(() => this.draw());
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
          const k = (t.date || "").slice(5);
          i = idx.has(k) ? idx.get(k) : -1;
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
    setMode(m) { if (this.mode !== m) { this.mode = m; } },
    async load(quiet) {
      if (!quiet) { this.loading = true; this.error = ""; }
      try {
        if (this.mode === "trend") {
          const d = await api.get("/api/market/chart?mode=trend", 45000);
          this.applyTrend(d);
        } else if (this.mode === "daily") {
          const d = await api.get("/api/market/chart?mode=daily", 45000);
          const rows = d.rows || [];
          this.labels = rows.map((r) => r[0].slice(5));
          this.values = rows.map((r) => r[2]);
          this.preClose = null;
        } else {
          const q = this.localDay ? `&day=${this.localDay}` : "";
          const d = await api.get(`/api/market/chart?mode=local${q}`, 45000);
          this.localDays = d.days || [];
          this.localDay = d.day || "";
          const pts = d.points || [];
          this.labels = pts.map((p) => p[0]);
          this.values = pts.map((p) => p[1]);
          this.preClose = null;
        }
      } catch (e) {
        this.error = e.message;
        this.labels = [];
        this.values = [];
      } finally {
        this.loading = false;
        this.$nextTick(() => {
          if (this.chart) this.chart.resize();
          this.draw();
        });
      }
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
      this.chart.setOption({
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
      }, true);
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
