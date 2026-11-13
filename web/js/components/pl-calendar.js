/* 每日盈亏日历：月度网格 + 当日明细 + 区间合计 */
window.PlCalendar = {
  data() {
    const d = new Date();
    return {
      y: d.getFullYear(),
      m: d.getMonth() + 1,
      rows: {},
      summary: null,
      hasSettle: true,
      loading: true,
      selected: "",
      timer: null,
    };
  },
  computed: {
    title() { return `${this.y} 年 ${this.m} 月`; },
    cells() {
      const first = new Date(this.y, this.m - 1, 1);
      const lead = (first.getDay() + 6) % 7;
      const days = new Date(this.y, this.m, 0).getDate();
      const today = this.todayStr();
      const out = [];
      for (let i = 0; i < lead; i++) out.push({ empty: true });
      for (let d = 1; d <= days; d++) {
        const ds = `${this.y}-${this.pad(this.m)}-${this.pad(d)}`;
        const row = this.rows[ds];
        out.push({
          day: d, date: ds, row: row || null,
          isToday: ds === today,
          cls: row ? (row.pl >= 0 ? "up-cell" : "down-cell") : "nodata",
        });
      }
      return out;
    },
    detail() { return this.rows[this.selected] || null; },
  },
  mounted() {
    this.load();
    this.timer = setInterval(() => this.load(true), 60000);
  },
  beforeUnmount() { clearInterval(this.timer); },
  methods: {
    pad(n) { return String(n).padStart(2, "0"); },
    todayStr() {
      const d = new Date();
      return `${d.getFullYear()}-${this.pad(d.getMonth() + 1)}-${this.pad(d.getDate())}`;
    },
    range() {
      const from = `${this.y}-${this.pad(this.m)}-01`;
      const last = new Date(this.y, this.m, 0).getDate();
      return [from, `${this.y}-${this.pad(this.m)}-${this.pad(last)}`];
    },
    async load(quiet) {
      if (!quiet) this.loading = true;
      try {
        const [from, to] = this.range();
        const d = await api.get(`/api/daily_pl?from=${from}&to=${to}`);
        const map = {};
        (d.rows || []).forEach((r) => { map[r.date] = r; });
        this.rows = map;
        this.summary = d.summary;
        this.hasSettle = d.has_settle;
        if (this.selected && !map[this.selected]) this.selected = "";
      } catch (e) {
        if (!quiet) pushToast("盈亏加载失败", e.message, "up");
      } finally {
        this.loading = false;
      }
    },
    shift(n) {
      let y = this.y, m = this.m + n;
      if (m < 1) { m = 12; y -= 1; }
      if (m > 12) { m = 1; y += 1; }
      this.y = y; this.m = m; this.selected = "";
      this.load();
    },
    goToday() {
      const d = new Date();
      this.y = d.getFullYear(); this.m = d.getMonth() + 1; this.selected = "";
      this.load();
    },
    select(c) {
      if (!c.row) return;
      this.selected = this.selected === c.date ? "" : c.date;
    },
    money(v) { return Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); },
    sign(v) { return v >= 0 ? "+" : ""; },
    short(v) {
      const n = Number(v);
      if (Math.abs(n) >= 10000) return `${n >= 0 ? "+" : ""}${(n / 10000).toFixed(2)}万`;
      return `${n >= 0 ? "+" : ""}${n.toFixed(0)}`;
    },
    weekday(ds) { return "一二三四五六日"[new Date(ds + "T00:00:00").getDay()]; },
  },
  template: `
    <div class="card panel">
      <div class="panel-head">
        <h3>每日盈亏</h3>
        <div class="row" style="gap:8px">
          <button class="btn ghost sm" @click="shift(-1)">上月</button>
          <span class="num" style="font-weight:700">{{ title }}</span>
          <button class="btn ghost sm" @click="shift(1)">下月</button>
          <button class="btn ghost sm" @click="goToday">回到本月</button>
        </div>
      </div>

      <div class="row" v-if="summary" style="margin-bottom:10px">
        <span class="hint">本月合计</span>
        <strong class="num" :class="summary.total_pl >= 0 ? 'up' : 'down'">
          {{ sign(summary.total_pl) }}{{ money(summary.total_pl) }} 元
        </strong>
        <span class="hint" v-if="summary.best">最佳 {{ summary.best.date }}（{{ sign(summary.best.pl) }}{{ money(summary.best.pl) }}）</span>
        <span class="hint" v-if="summary.worst">最差 {{ summary.worst.date }}（{{ sign(summary.worst.pl) }}{{ money(summary.worst.pl) }}）</span>
      </div>

      <div v-if="!hasSettle" class="empty" style="padding:14px">
        还没有任何日结价记录。管理员可在「设置 → 日结价维护」补录历史，或等引擎在 22:20 自动定格。
      </div>

      <div v-else-if="loading" class="empty">加载中…</div>

      <template v-else>
        <div class="cal">
          <div class="wd" v-for="w in ['一','二','三','四','五','六','日']" :key="w">{{ '周' + w }}</div>
          <div v-for="(c, i) in cells" :key="i"
               class="cal-cell" :class="[c.empty ? 'empty' : c.cls, { today: c.isToday, sel: c.date === selected }]"
               @click="select(c)">
            <template v-if="!c.empty">
              <span class="d">{{ c.day }}</span>
              <span v-if="c.row" class="pl" :class="c.row.pl >= 0 ? 'up' : 'down'">{{ short(c.row.pl) }}</span>
              <span v-if="c.row && c.row.est" class="est">估算</span>
            </template>
          </div>
        </div>

        <div v-if="detail" class="calc-result">
          <div class="line">
            <span>{{ detail.date }} 周{{ weekday(detail.date) }}
              <span class="hint">{{ detail.is_today ? '（今日实时）' : (detail.est ? '（估算 · 黄金9999 日K近似）' : '（收盘 · 22:20 结算价）') }}</span>
            </span>
          </div>
          <div class="line"><span>昨收 → 今值</span>
            <span class="v">{{ detail.prev.toFixed(2) }} → {{ detail.mark.toFixed(2) }}
              <span :class="detail.mark >= detail.prev ? 'up' : 'down'">
                （{{ sign(detail.mark - detail.prev) }}{{ (detail.mark - detail.prev).toFixed(2) }}）
              </span>
            </span>
          </div>
          <div class="line"><span>昨日存量</span><span class="v">{{ detail.grams_open.toFixed(4) }} 克</span></div>
          <div class="line" v-if="detail.buy_g > 0">
            <span>当日买入</span>
            <span class="v">{{ detail.buy_g.toFixed(4) }} 克 @ {{ (detail.buy_cost / detail.buy_g).toFixed(2) }}</span>
          </div>
          <div class="line" v-if="detail.sell_g > 0">
            <span>当日卖出</span>
            <span class="v">{{ detail.sell_g.toFixed(4) }} 克 @ {{ detail.sell_px.toFixed(2) }}，手续费 −{{ money(detail.sell_fee) }}</span>
          </div>
          <div class="line"><span>当日盈亏</span>
            <span class="v" :class="detail.pl >= 0 ? 'up' : 'down'">{{ sign(detail.pl) }}{{ money(detail.pl) }} 元</span>
          </div>
        </div>
      </template>
    </div>`,
};
