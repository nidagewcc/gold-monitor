/* 管理员：邀请码生成、日结价维护（全局共享） */
window.AdminPanel = {
  data() {
    return {
      invites: [],
      codes: [],
      settles: [],
      day: "",
      price: "",
      saving: false,
    };
  },
  mounted() { this.loadInvites(); this.loadSettles(); },
  methods: {
    async loadInvites() {
      try { this.invites = await api.get("/api/admin/invites"); }
      catch (e) { pushToast("邀请码加载失败", e.message, "up"); }
    },
    async genInvites() {
      try {
        const d = await api.post("/api/admin/invites", { count: 1 });
        this.codes = d.codes || [];
        this.loadInvites();
      } catch (e) {
        pushToast("生成失败", e.message, "up");
      }
    },
    async loadSettles() {
      try {
        const rows = await api.get("/api/settles");
        this.settles = rows.slice().reverse().slice(0, 60);
      } catch (e) {
        pushToast("日结价加载失败", e.message, "up");
      }
    },
    async saveSettle() {
      if (!this.day) { pushToast("保存失败", "请选择日期", "up"); return; }
      const p = Number(this.price);
      if (!(p > 0)) { pushToast("保存失败", "价格必须大于 0", "up"); return; }
      this.saving = true;
      try {
        await api.put(`/api/settles/${this.day}`, { price: p });
        pushToast("日结价已保存", `${this.day} → ${p.toFixed(2)} 元/克（标记为人工）`, "plan");
        this.price = "";
        this.loadSettles();
      } catch (e) {
        pushToast("保存失败", e.message, "up");
      } finally {
        this.saving = false;
      }
    },
    edit(row) {
      this.day = row.day;
      this.price = String(row.price);
    },
    copyCode(code) {
      navigator.clipboard && navigator.clipboard.writeText(code);
      pushToast("已复制", code, "plan");
    },
  },
  template: `
    <div class="stack">
      <div class="card panel">
        <div class="panel-head">
          <h3>邀请码</h3>
          <div class="row" style="gap:8px">
            <button class="btn ghost sm" @click="genInvites">生成一个</button>
          </div>
        </div>
        <div class="row" v-if="codes.length" style="margin-bottom:12px">
          <span class="hint">新生成：</span>
          <span class="tag sell" v-for="c in codes" :key="c" style="cursor:pointer" @click="copyCode(c)">{{ c }}</span>
          <span class="hint">点一下复制，发给对方注册时填写</span>
        </div>
        <div v-if="!invites.length" class="empty">还没有邀请码。</div>
        <div v-else class="table-wrap" style="margin-top:0">
          <table class="data">
            <thead><tr><th>邀请码</th><th>创建时间</th><th>使用者</th><th>使用时间</th></tr></thead>
            <tbody>
              <tr v-for="i in invites" :key="i.code">
                <td class="num">{{ i.code }}</td>
                <td class="hint">{{ i.created_at }}</td>
                <td>{{ i.used_by || '未使用' }}</td>
                <td class="hint">{{ i.used_at || '--' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="card panel">
        <div class="panel-head">
          <h3>日结价维护</h3>
          <span class="hint">日结价为全局共享；人工写入优先，引擎不再覆盖</span>
        </div>
        <div class="form-row" style="grid-template-columns:repeat(3,minmax(0,1fr))">
          <div class="field">
            <label>日期</label>
            <input class="input" type="date" v-model="day">
          </div>
          <div class="field">
            <label>日结价（元/克）</label>
            <input class="input" type="number" step="0.01" min="0" v-model="price" placeholder="0.00">
          </div>
          <div class="field">
            <button class="btn" :disabled="saving" @click="saveSettle">保存 / 覆盖</button>
          </div>
        </div>
        <div v-if="!settles.length" class="empty">还没有日结价记录。</div>
        <div v-else class="table-wrap">
          <table class="data">
            <thead><tr><th>日期</th><th>日结价</th><th>来源</th><th></th></tr></thead>
            <tbody>
              <tr v-for="s in settles" :key="s.day">
                <td>{{ s.day }}</td>
                <td>{{ Number(s.price).toFixed(2) }}</td>
                <td><span class="tag" :class="s.src === 'manual' ? 'buy' : ''">{{ s.src === 'manual' ? '人工' : '引擎' }}</span></td>
                <td style="text-align:right"><button class="btn ghost sm" @click="edit(s)">修改</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>`,
};
