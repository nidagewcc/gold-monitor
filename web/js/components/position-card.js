/* 持仓卡：按积存金赎回价估值；支持编辑持仓与隐私模式切换 */
window.PositionCard = {
  props: ["position", "sellPrice", "privacy"],
  emits: ["saved"],
  data() {
    return { editing: false, grams: "", cost: "", saving: false };
  },
  computed: {
    gramsNum() { return Number((this.position && this.position.grams) || 0); },
    costNum() { return Number((this.position && this.position.cost) || 0); },
    sell() { return Number(this.sellPrice || 0); },
    ready() { return this.gramsNum > 0 && this.sell > 0; },
    mv() { return this.gramsNum * this.sell; },
    pl() { return this.mv - this.costNum; },
    costPx() { return this.gramsNum > 0 ? this.costNum / this.gramsNum : 0; },
    pct() { return this.costNum > 0 ? (this.pl / this.costNum) * 100 : 0; },
    plCls() { return this.pl >= 0 ? "up" : "down"; },
  },
  methods: {
    money(v) {
      return Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    },
    sign(v) { return v >= 0 ? "+" : ""; },
    openEdit() {
      this.grams = String(this.gramsNum || "");
      this.cost = String(this.costNum || "");
      this.editing = true;
    },
    async save() {
      const g = Number(this.grams);
      const c = Number(this.cost);
      if (!isFinite(g) || !isFinite(c) || g < 0 || c < 0) {
        pushToast("保存失败", "请填写有效的克数与成本", "up");
        return;
      }
      this.saving = true;
      try {
        const pos = await api.put("/api/position", { grams: g, cost: c });
        store.position = pos;
        this.editing = false;
        pushToast("持仓已保存", `${Number(pos.grams).toFixed(4)} 克 · 成本 ${this.money(pos.cost)} 元`, "plan");
        this.$emit("saved");
      } catch (e) {
        pushToast("保存失败", e.message, "up");
      } finally {
        this.saving = false;
      }
    },
    async togglePrivacy() {
      const next = !this.privacy;
      try {
        const st = await api.put("/api/settings", { privacy: next });
        store.settings = st;
      } catch (e) {
        pushToast("切换失败", e.message, "up");
      }
    },
  },
  template: `
    <div class="card pos-card">
      <div class="card-title">
        <h3>我的持仓</h3>
        <div class="row" style="gap:8px">
          <button class="btn ghost sm" @click="togglePrivacy">{{ privacy ? '亮牌' : '打码' }}</button>
          <button class="btn ghost sm" @click="openEdit">编辑</button>
        </div>
      </div>

      <div v-if="!ready" class="muted" style="padding:8px 0 4px">
        还没录入持仓，点右上角「编辑」填写持有克数与累计成本。
      </div>

      <template v-else>
        <div v-if="privacy">
          <div class="pos-grid">
            <div class="pos-item"><div class="k">持有克数</div><div class="v">****</div></div>
            <div class="pos-item"><div class="k">预估市值</div><div class="v">****</div></div>
            <div class="pos-item"><div class="k">浮动盈亏</div><div class="v">****</div></div>
            <div class="pos-item"><div class="k">成本价</div><div class="v">****</div></div>
          </div>
          <div class="muted" style="margin-top:10px;font-size:12px">
            隐私模式已开启 · 赎回价 {{ sell.toFixed(2) }} 元/克
          </div>
        </div>
        <template v-else>
          <div class="pos-pnl num" :class="plCls">
            {{ sign(pl) }}{{ money(pl) }} 元
            <span style="font-size:14px;font-weight:600">（{{ sign(pct) }}{{ pct.toFixed(2) }}%）</span>
          </div>
          <div class="pos-grid" style="margin-top:14px">
            <div class="pos-item">
              <div class="k">持有克数</div>
              <div class="v num">{{ gramsNum.toFixed(4) }} g</div>
            </div>
            <div class="pos-item">
              <div class="k">预估市值</div>
              <div class="v num">{{ money(mv) }} 元</div>
            </div>
            <div class="pos-item">
              <div class="k">累计成本</div>
              <div class="v num">{{ money(costNum) }} 元</div>
            </div>
            <div class="pos-item">
              <div class="k">成本价</div>
              <div class="v num">{{ costPx.toFixed(2) }} 元/克</div>
            </div>
          </div>
        </template>
      </template>

      <div v-if="editing" class="modal-mask" @click.self="editing = false">
        <div class="modal">
          <h3>编辑我的持仓</h3>
          <div class="field">
            <label>持有克数（克）</label>
            <input class="input" type="number" step="0.0001" min="0" v-model="grams">
          </div>
          <div class="field">
            <label>持仓成本（元）</label>
            <input class="input" type="number" step="0.01" min="0" v-model="cost">
          </div>
          <p class="hint" style="margin:0 0 14px">当前持仓对应的累计成本；减仓按均价自动结转。</p>
          <div class="row" style="justify-content:flex-end">
            <button class="btn ghost" @click="editing = false">取消</button>
            <button class="btn" :disabled="saving" @click="save">保存</button>
          </div>
        </div>
      </div>
    </div>`,
};
