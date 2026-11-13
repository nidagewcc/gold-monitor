/* 交易时段徽章：交易中 / 已休市 / 收盘定格 */
window.MarketBadge = {
  props: ["market"],
  computed: {
    text() {
      const m = this.market || {};
      if (m.settle_locked) return "收盘定格";
      return m.open ? "交易中" : "已休市";
    },
    cls() {
      const m = this.market || {};
      if (m.settle_locked) return "locked";
      return m.open ? "open" : "closed";
    },
  },
  template: `
    <span class="badge" :class="cls" :title="market && market.paused ? '休市暂停取价' : ''">
      <span class="dot"></span>{{ text }}
    </span>`,
};
