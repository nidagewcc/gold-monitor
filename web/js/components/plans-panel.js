/* 补仓计划：维护目标价与计划克数，命中时服务端推送提醒 */
window.PlansPanel = {
  props: ["currentPrice"],
  data() {
    return { items: [], loading: true, saving: false };
  },
  computed: {
    buy() { return Number(this.currentPrice || 0); },
  },
  mounted() { this.load(); },
  methods: {
    async load() {
      this.loading = true;
      try {
        const rows = await api.get("/api/plans");
        this.items = rows.map((r) => ({ price: r.price, grams: r.grams }));
      } catch (e) {
        pushToast("计划加载失败", e.message, "up");
      } finally {
        this.loading = false;
      }
    },
    add() { this.items.push({ price: "", grams: "" }); },
    remove(i) { this.items.splice(i, 1); },
    gap(item) {
      const p = Number(item.price);
      if (!(p > 0) || !(this.buy > 0)) return "--";
      return ((this.buy - p) / p * 100).toFixed(2) + "%";
    },
    spaced(item) {
      const p = Number(item.price);
      return p > 0 && this.buy > 0 && this.buy <= p;
    },
    async save() {
      const items = this.items
        .map((it) => ({ price: Number(it.price), grams: Number(it.grams) || 0 }))
        .filter((it) => it.price > 0);
      this.saving = true;
      try {
        const rows = await api.put("/api/plans", { items });
        this.items = rows.map((r) => ({ price: r.price, grams: r.grams }));
        pushToast("计划已保存", `共 ${rows.length} 条，到价时通过页面与微信提醒`, "plan");
      } catch (e) {
        pushToast("保存失败", e.message, "up");
      } finally {
        this.saving = false;
      }
    },
    money(v) { return Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); },
  },
  template: `
    <div class="card panel">
      <div class="panel-head">
        <h3>补仓计划</h3>
        <span class="hint">主动买入价下穿目标价时提醒；计划价会画在走势图上</span>
      </div>

      <div class="row" style="margin-bottom:6px">
        <span class="hint" v-if="buy">当前主动买入价 <strong class="num">{{ buy.toFixed(2) }}</strong> 元/克</span>
        <div class="grow"></div>
        <button class="btn ghost sm" @click="add">新增一行</button>
        <button class="btn" :disabled="saving" @click="save">保存计划</button>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="!items.length" class="empty">
        还没有补仓计划。点「新增一行」填写目标价，例如 780 / 760 / 740。
      </div>
      <div v-else class="table-wrap">
        <table class="data">
          <thead>
            <tr><th>目标买入价（元/克）</th><th>计划克数</th><th>计划金额</th><th>距现价</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="(it, i) in items" :key="i">
              <td><input class="input" style="min-height:34px;width:130px" type="number" step="0.01" v-model="it.price"></td>
              <td><input class="input" style="min-height:34px;width:110px" type="number" step="0.0001" v-model="it.grams"></td>
              <td>{{ Number(it.price) > 0 && Number(it.grams) > 0 ? money(Number(it.price) * Number(it.grams)) + ' 元' : '--' }}</td>
              <td>
                <span :class="spaced(it) ? 'down' : ''">{{ gap(it) }}</span>
                <span v-if="spaced(it)" class="tag sell" style="margin-left:6px">已到价</span>
              </td>
              <td style="text-align:right"><button class="btn ghost sm" @click="remove(i)">删除</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>`,
};
