/* 加仓小天才：补仓后的摊薄均价与回本测算 */
window.AvgDownPanel = {
  props: ["position", "sellPrice", "buyPrice", "privacy"],
  data() {
    return { money: "5000", buy: "" };
  },
  computed: {
    g0() { return Number((this.position && this.position.grams) || 0); },
    cost0() { return Number((this.position && this.position.cost) || 0); },
    buyPx() {
      const v = Number(this.buy);
      if (v > 0) return v;
      return Number(this.buyPrice || 0);
    },
    sellPx() { return Number(this.sellPrice || 0); },
    addMoney() { return Number(this.money); },
    valid() { return this.g0 > 0 && this.addMoney > 0 && this.buyPx > 0; },
    addGrams() { return this.addMoney / this.buyPx; },
    newGrams() { return this.g0 + this.addGrams; },
    newCost() { return this.cost0 + this.addMoney; },
    avg() { return this.newGrams > 0 ? this.newCost / this.newGrams : 0; },
    avgOld() { return this.g0 > 0 ? this.cost0 / this.g0 : 0; },
    curSell() { return this.sellPx || this.buyPx; },
    spread() { return this.sellPx ? this.buyPx - this.sellPx : 0; },
    needGap() { return this.avg - this.curSell; },
    needPct() { return this.curSell > 0 ? (this.needGap / this.curSell) * 100 : 0; },
    plNow() { return this.g0 * this.curSell - this.cost0; },
    horizon() {
      if (this.needGap <= 0) return [];
      return [0.01, 0.02, 0.03, 0.05].map((r) => {
        const m = Math.log(this.avg / this.curSell) / Math.log(1 + r);
        return { rate: r * 100, months: m.toFixed(1), years: (m / 12).toFixed(1) };
      });
    },
    mask() { return !!this.privacy; },
  },
  methods: {
    money2(v) { return Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); },
    sign(v) { return v >= 0 ? "+" : ""; },
  },
  template: `
    <div class="card panel">
      <div class="panel-head">
        <h3>加仓小天才</h3>
        <span class="hint">补仓后的摊薄均价与理论回本月数</span>
      </div>

      <div class="form-row" style="grid-template-columns:repeat(2,minmax(0,1fr))">
        <div class="field">
          <label>补仓金额（元）</label>
          <input class="input" type="number" step="100" min="0" v-model="money" placeholder="5000">
        </div>
        <div class="field">
          <label>买入价（元/克）</label>
          <input class="input" type="number" step="0.01" min="0" v-model="buy" :placeholder="Number(buyPrice) ? Number(buyPrice).toFixed(2) : '0.00'">
        </div>
      </div>

      <div v-if="g0 <= 0" class="empty">先在「行情 → 我的持仓」录入持仓克数。</div>
      <div v-else-if="!valid" class="empty">填入补仓金额即可测算（买入价留空时取实时主动买入价）。</div>
      <div v-else class="calc-result">
        <div class="line"><span>本次补仓</span>
          <span class="v">{{ mask ? '****' : money2(addMoney) + ' 元 ≈ ' + addGrams.toFixed(4) + ' 克' }}</span></div>
        <div class="line"><span>补仓后持仓 / 总成本</span>
          <span class="v">{{ mask ? '****' : newGrams.toFixed(4) + ' 克 / ' + money2(newCost) + ' 元' }}</span></div>
        <div class="line"><span>摊薄均价</span>
          <span class="v">{{ mask ? '****' : avgOld.toFixed(2) + ' → ' + avg.toFixed(2) + ' 元/克（降低 ' + (avgOld - avg).toFixed(2) + '）' }}</span></div>
        <div class="line"><span>当前赎回价 / 买卖点差</span>
          <span class="v">{{ curSell.toFixed(2) }} / {{ spread.toFixed(2) }}</span></div>
        <div class="line" v-if="needGap > 0">
          <span>回本还需上涨</span>
          <span class="v">{{ needGap.toFixed(2) }} 元（{{ sign(needPct) }}{{ needPct.toFixed(2) }}%）</span>
        </div>
        <div class="line" v-else><span>回本状态</span><span class="v down">按当前赎回价已回本</span></div>
        <div class="line"><span>补仓前浮动盈亏</span>
          <span class="v" :class="plNow >= 0 ? 'up' : 'down'">{{ mask ? '****' : sign(plNow) + money2(plNow) + ' 元' }}</span></div>
        <div v-if="horizon.length" style="margin-top:6px">
          <div class="hint" style="margin-bottom:2px">理论回本时间（假设匀速月涨）</div>
          <div class="line" v-for="h in horizon" :key="h.rate">
            <span class="hint">月涨 {{ h.rate }}%</span>
            <span class="v">约 {{ h.months }} 个月（{{ h.years }} 年）</span>
          </div>
        </div>
      </div>
      <p class="hint" style="margin:auto 0 0;padding-top:12px">
        回本 = 赎回价回到摊薄均价；月数为纯数学参考，金价走势无法预测。
      </p>
    </div>`,
};
