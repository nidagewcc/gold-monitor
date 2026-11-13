/* 行情卡：当前监控品种大数字 + 四个主要品种速览 */
window.PriceCard = {
  props: ["prices", "sources", "source", "time", "date", "high", "low", "market", "stale"],
  computed: {
    name() {
      return (this.sources && this.sources[this.source]) || this.source || "行情";
    },
    value() {
      return this.prices ? this.prices[this.source] : null;
    },
    cells() {
      const keys = ["jicun_active", "jicun_sell", "ruyi_active", "au9999"];
      const p = this.prices || {};
      return keys.map((k) => ({
        key: k,
        name: (this.sources && this.sources[k]) || k,
        value: p[k],
      }));
    },
  },
  methods: {
    fmt(v) {
      return v === null || v === undefined || v === "" ? "--" : Number(v).toFixed(2);
    },
  },
  template: `
    <div class="card price-card">
      <div class="price-head">
        <div class="price-name">
          {{ name }}
          <span v-if="stale" class="muted">· 离线缓存</span>
        </div>
        <market-badge :market="market"></market-badge>
      </div>
      <div class="price-main">
        <span class="price-value num">{{ fmt(value) }}</span>
        <span class="price-unit">元/克</span>
      </div>
      <div class="price-meta">
        更新于 {{ time || '--' }}<span v-if="date"> · {{ date }}</span>
        <span v-if="high"> · 日内最高 {{ fmt(high) }}</span>
        <span v-if="low"> · 日内最低 {{ fmt(low) }}</span>
      </div>
      <div class="price-grid">
        <div class="price-cell" v-for="c in cells" :key="c.key">
          <div class="k">{{ c.name }}</div>
          <div class="v num">{{ fmt(c.value) }}</div>
        </div>
      </div>
    </div>`,
};
