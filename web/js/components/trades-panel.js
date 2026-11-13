/* 交易记录：录入买卖、列表、删除 */
window.TradesPanel = {
  data() {
    const d = new Date();
    const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    return {
      rows: [],
      loading: true,
      saving: false,
      form: {
        date: today,
        time: `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`,
        kind: "buy",
        grams: "",
        amount: "",
        note: "",
      },
    };
  },
  computed: {
    price() {
      const g = Number(this.form.grams);
      const a = Number(this.form.amount);
      return g > 0 && a > 0 ? a / g : 0;
    },
  },
  mounted() { this.load(); },
  methods: {
    async load() {
      this.loading = true;
      try {
        this.rows = await api.get("/api/trades");
      } catch (e) {
        pushToast("加载失败", e.message, "up");
      } finally {
        this.loading = false;
      }
    },
    async add() {
      const g = Number(this.form.grams);
      const a = Number(this.form.amount);
      if (!this.form.date) { pushToast("添加失败", "请选择日期", "up"); return; }
      if (!(g > 0)) { pushToast("添加失败", "克数必须大于 0", "up"); return; }
      if (!(a > 0)) { pushToast("添加失败", "金额必须大于 0", "up"); return; }

      const body = {
        date: this.form.date,
        kind: this.form.kind,
        grams: g,
        amount: a,
        note: this.form.note.trim(),
      };
      if (this.form.time) {
        // 拼 YYYY-MM-DD HH:MM:00，后端规整
        body.ts = `${this.form.date} ${this.form.time}:00`;
      }

      this.saving = true;
      try {
        const row = await api.post("/api/trades", body);
        this.rows.unshift(row);
        this.form.grams = "";
        this.form.amount = "";
        this.form.note = "";
        pushToast("已添加", this.labelOf(row) + " " + row.grams.toFixed(4) + " 克", "plan");
      } catch (e) {
        pushToast("添加失败", e.message, "up");
      } finally {
        this.saving = false;
      }
    },
    async remove(row) {
      const when = row.ts ? row.ts : row.date;
      if (!confirm(`删除 ${when} 这笔记录？`)) return;
      try {
        await api.del(`/api/trades/${row.id}`);
        this.rows = this.rows.filter((r) => r.id !== row.id);
      } catch (e) {
        pushToast("删除失败", e.message, "up");
      }
    },
    labelOf(row) {
      return row.kind === "sell" ? "卖出" : "买入";
    },
    fmtWhen(row) {
      // 表格里显示紧凑的日期/时间戳
      if (row.ts) {
        // 2026-11-12 14:32:00 -> 11-12 14:32
        const m = row.ts.match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})/);
        if (m) return `${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
        return row.ts.slice(5, 16);
      }
      // 只有日期：2026-11-12 -> 11-12
      return (row.date || "").slice(5, 10);
    },
    whenTitle(row) {
      // hover 时显示完整值
      return row.ts ? row.ts : row.date;
    },
    fmt(v, n) { return Number(v).toFixed(n === undefined ? 2 : n); },
    money(v) { return Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); },
  },
  template: `
    <div class="card panel">
      <div class="panel-head">
        <h3>交易记录</h3>
        <span class="hint">买入影响成本与每日盈亏重算，卖出按赎回金额 0.5% 计手续费</span>
      </div>

      <div class="form-row" style="grid-template-columns:repeat(5,minmax(0,1fr))">
        <div class="field">
          <label>日期</label>
          <input class="input" type="date" v-model="form.date">
        </div>
        <div class="field">
          <label>时间（可选）</label>
          <input class="input" type="time" step="60" v-model="form.time">
        </div>
        <div class="field">
          <label>方向</label>
          <select class="input" v-model="form.kind">
            <option value="buy">买入 / 补仓</option>
            <option value="sell">卖出 / 赎回</option>
          </select>
        </div>
        <div class="field">
          <label>克数（克）</label>
          <input class="input" type="number" step="0.0001" min="0" v-model="form.grams" placeholder="0.0000">
        </div>
        <div class="field">
          <label>金额（元）</label>
          <input class="input" type="number" step="0.01" min="0" v-model="form.amount" placeholder="0.00">
        </div>
      </div>
      <div class="row" style="margin-top:8px">
        <input class="input grow" v-model="form.note" placeholder="备注（可选）" style="max-width:320px">
        <span class="hint" v-if="price">折合 {{ price.toFixed(2) }} 元/克</span>
        <div class="grow"></div>
        <button class="btn" :disabled="saving" @click="add">添加记录</button>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="!rows.length" class="empty">还没有交易记录。录入后每日盈亏会自动重算。</div>
      <div v-else class="table-wrap">
        <table class="data">
          <thead>
            <tr>
              <th>日期 / 时间</th>
              <th>方向</th>
              <th class="num-col">克数</th>
              <th class="num-col">金额</th>
              <th class="num-col">成交价</th>
              <th>备注</th>
              <th class="op-col"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.id">
              <td class="when" :title="whenTitle(r)">{{ fmtWhen(r) }}</td>
              <td><span class="tag" :class="r.kind">{{ r.kind === 'sell' ? '卖出' : '买入' }}</span></td>
              <td class="num-col">{{ fmt(r.grams, 4) }}</td>
              <td class="num-col">{{ money(r.amount) }}</td>
              <td class="num-col">{{ fmt(r.price) }}</td>
              <td class="hint">{{ r.note }}</td>
              <td class="op-col"><button class="btn ghost sm" @click="remove(r)">删除</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>`,
};
