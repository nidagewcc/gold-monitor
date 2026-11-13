/* 跑路计算器：赎回手续费与净盈亏测算 */
window.CalcPanel = {
  props: ["sellPrice", "position", "privacy"],
  data() {
    return { grams: "", price: "" };
  },
  computed: {
    holdG() { return Number((this.position && this.position.grams) || 0); },
    holdCost() { return Number((this.position && this.position.cost) || 0); },
    px() {
      const p = Number(this.price);
      if (p > 0) return p;
      const s = Number(this.sellPrice || 0);
      return s > 0 ? s : 0;
    },
    g() { return Number(this.grams); },
    valid() { return this.g > 0 && this.px > 0 && this.holdG > 0 && this.g <= this.holdG + 1e-9; },
    redeem() { return this.g * this.px; },
    fee() { return this.redeem * 0.005; },
    arrived() { return this.redeem - this.fee; },
    costPart() { return this.holdG > 0 ? this.holdCost * this.g / this.holdG : 0; },
    pl() { return this.arrived - this.costPart; },
    feeEff() { return this.g > 0 ? this.fee / this.g : 0; },
    restG() { return Math.max(this.holdG - this.g, 0); },
    restCost() { return Math.max(this.holdCost - this.costPart, 0); },
    maskComputed() { return !!this.privacy; },
  },
  methods: {
    money(v) { return Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); },
    fillAll() { this.grams = String(this.holdG || ""); },
    sign(v) { return v >= 0 ? "+" : ""; },
  },
  template: `
    <div class="card panel">
      <div class="panel-head">
        <h3>跑路计算器</h3>
        <span class="hint">按赎回金额 0.5% 计手续费</span>
      </div>

      <div class="form-row" style="grid-template-columns:repeat(2,minmax(0,1fr))">
        <div class="field">
          <label>卖出克数（克）</label>
          <div class="row" style="gap:8px">
            <input class="input grow" type="number" step="0.0001" min="0" v-model="grams" :placeholder="holdG ? holdG.toFixed(4) : '0.0000'">
            <button class="btn ghost sm" v-if="holdG" @click="fillAll">全部</button>
          </div>
        </div>
        <div class="field">
          <label>赎回价（元/克）</label>
          <input class="input" type="number" step="0.01" min="0" v-model="price" :placeholder="px ? px.toFixed(2) : '0.00'">
        </div>
      </div>

      <div v-if="holdG <= 0" class="empty">先在「行情 → 我的持仓」录入持仓克数。</div>
      <div v-else-if="g <= 0" class="empty">填入卖出克数即可测算（留空价格时使用实时赎回价）。</div>
      <div v-else-if="g > holdG + 1e-9" class="empty">卖出克数不能超过当前持仓 {{ holdG.toFixed(4) }} 克。</div>
      <div v-else class="calc-result">
        <div class="line"><span>卖出克数 × 赎回价</span>
          <span class="v">{{ maskComputed ? '****' : g.toFixed(4) }} 克 × {{ px.toFixed(2) }}</span></div>
        <div class="line"><span>赎回金额（扣费前）</span>
          <span class="v">{{ maskComputed ? '****' : money(redeem) + ' 元' }}</span></div>
        <div class="line"><span>手续费（0.5%）</span>
          <span class="v">{{ maskComputed ? '****' : money(fee) + ' 元（约 ' + feeEff.toFixed(2) + ' 元/克）' }}</span></div>
        <div class="line"><span>实际到账</span>
          <span class="v">{{ maskComputed ? '****' : money(arrived) + ' 元' }}</span></div>
        <div class="line"><span>对应成本</span>
          <span class="v">{{ maskComputed ? '****' : money(costPart) + ' 元' }}</span></div>
        <div class="line"><span>扣费后净盈亏</span>
          <span class="v" :class="pl >= 0 ? 'up' : 'down'">{{ maskComputed ? '****' : sign(pl) + money(pl) + ' 元' }}</span></div>
        <div class="line" v-if="g < holdG - 1e-9">
          <span>部分卖出后剩余</span>
          <span class="v">{{ maskComputed ? '****' : restG.toFixed(4) + ' 克 · 成本 ' + money(restCost) + ' 元' }}</span>
        </div>
      </div>
      <p class="hint" style="margin:auto 0 0;padding-top:12px">
        按当日实时赎回价成交，资金一般 T+5 个工作日到账；兑换实物金条不按此收费。实际以工行办理时为准。
      </p>
    </div>`,
};
