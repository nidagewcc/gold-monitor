/* 设置：监控品种 / 档位 / 轮询间隔 / 提醒与隐私 / 主题；含微信与管理员面板 */
window.SettingsPanel = {
  data() {
    const st = store.settings || {};
    return {
      saving: false,
      form: {
        source: st.source || "jicun_active",
        step: st.step || 10,
        interval: st.interval || 10,
        alert_up: st.alert_up !== false,
        privacy: !!st.privacy,
        theme: st.theme || "light",
      },
    };
  },
  computed: {
    sources() { return store.sources || {}; },
    isAdmin() { return !!(store.user && store.user.is_admin); },
  },
  methods: {
    async save() {
      const step = Number(this.form.step);
      const interval = Number(this.form.interval);
      if (!(step > 0)) { pushToast("保存失败", "档位必须大于 0", "up"); return; }
      if (!(interval >= 3)) { pushToast("保存失败", "轮询间隔不能小于 3 秒", "up"); return; }
      this.saving = true;
      try {
        const st = await api.put("/api/settings", {
          source: this.form.source,
          step, interval,
          alert_up: this.form.alert_up,
          privacy: this.form.privacy,
          theme: this.form.theme,
        });
        store.settings = st;
        document.documentElement.dataset.theme = st.theme === "dark" ? "dark" : "light";
        pushToast("设置已保存", "引擎已按新配置刷新", "plan");
      } catch (e) {
        pushToast("保存失败", e.message, "up");
      } finally {
        this.saving = false;
      }
    },
  },
  template: `
    <div class="stack">
      <div class="card panel">
        <div class="panel-head">
          <h3>监控设置</h3>
          <span class="hint">行情由服务端统一采集，多用户共用同一份报价</span>
        </div>

        <div class="form-row" style="grid-template-columns:repeat(3,minmax(0,1fr))">
          <div class="field">
            <label>监控品种</label>
            <select class="input" v-model="form.source">
              <option v-for="(v, k) in sources" :key="k" :value="k">{{ v }}</option>
            </select>
          </div>
          <div class="field">
            <label>档位间隔（元）</label>
            <input class="input" type="number" step="1" min="1" v-model="form.step">
          </div>
          <div class="field">
            <label>轮询间隔（秒，≥3）</label>
            <input class="input" type="number" step="1" min="3" v-model="form.interval">
          </div>
        </div>

        <div class="row" style="margin:16px 0 4px;gap:26px">
          <label class="switch">
            <input type="checkbox" v-model="form.alert_up">
            <span class="track"></span>
            <span>上涨破档也提醒</span>
          </label>
          <label class="switch">
            <input type="checkbox" v-model="form.privacy">
            <span class="track"></span>
            <span>隐私模式（隐藏持仓数字）</span>
          </label>
          <label class="switch">
            <input type="checkbox" :checked="form.theme === 'dark'"
                   @change="form.theme = $event.target.checked ? 'dark' : 'light'">
            <span class="track"></span>
            <span>深色主题</span>
          </label>
        </div>

        <div class="row" style="justify-content:flex-end;margin-top:14px">
          <button class="btn" :disabled="saving" @click="save">保存设置</button>
        </div>
      </div>

      <wx-panel></wx-panel>
      <admin-panel v-if="isAdmin"></admin-panel>
    </div>`,
};
