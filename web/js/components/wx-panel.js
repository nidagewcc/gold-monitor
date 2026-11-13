/* 微信推送设置：appsecret 不回传，留空即不修改 */
window.WxPanel = {
  data() {
    return {
      loading: true,
      saving: false,
      testing: false,
      hasSecret: false,
      form: { enabled: false, appid: "", secret: "", openid: "", template_id: "" },
    };
  },
  mounted() { this.load(); },
  methods: {
    async load() {
      this.loading = true;
      try {
        const d = await api.get("/api/wx");
        this.hasSecret = !!d.has_secret;
        this.form = {
          enabled: !!d.enabled, appid: d.appid || "",
          secret: "", openid: d.openid || "", template_id: d.template_id || "",
        };
      } catch (e) {
        pushToast("微信配置加载失败", e.message, "up");
      } finally {
        this.loading = false;
      }
    },
    async save() {
      this.saving = true;
      try {
        const body = {
          enabled: this.form.enabled,
          appid: this.form.appid.trim(),
          openid: this.form.openid.trim(),
          template_id: this.form.template_id.trim(),
        };
        if (this.form.secret.trim()) body.secret = this.form.secret.trim();
        const d = await api.put("/api/wx", body);
        this.hasSecret = !!d.has_secret;
        this.form.secret = "";
        pushToast("微信配置已保存", d.enabled ? "破档与补仓到价时会同时推送" : "推送开关当前关闭", "plan");
      } catch (e) {
        pushToast("保存失败", e.message, "up");
      } finally {
        this.saving = false;
      }
    },
    async test() {
      this.testing = true;
      try {
        await api.post("/api/wx/test");
        pushToast("测试已发送", "去微信看看有没有收到消息", "plan");
      } catch (e) {
        pushToast("测试失败", e.message, "up");
      } finally {
        this.testing = false;
      }
    },
  },
  template: `
    <div class="card panel">
      <div class="panel-head">
        <h3>微信推送</h3>
        <span class="hint">页面关闭后仍可收到破档 / 补仓提醒（公众号测试号模板消息）</span>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <template v-else>
        <div class="row" style="margin-bottom:14px">
          <label class="switch">
            <input type="checkbox" v-model="form.enabled">
            <span class="track"></span>
            <span>启用微信推送</span>
          </label>
        </div>
        <div class="form-row" style="grid-template-columns:repeat(2,minmax(0,1fr))">
          <div class="field">
            <label>appID</label>
            <input class="input" v-model="form.appid" placeholder="wx 开头">
          </div>
          <div class="field">
            <label>appsecret</label>
            <input class="input" type="password" v-model="form.secret"
                   :placeholder="hasSecret ? '已保存，留空则不修改' : '未设置'">
          </div>
          <div class="field">
            <label>openid</label>
            <input class="input" v-model="form.openid" placeholder="关注测试号后的 openid">
          </div>
          <div class="field">
            <label>模板 ID</label>
            <input class="input" v-model="form.template_id" placeholder="模板内容需含 content.DATA 字段">
          </div>
        </div>
        <div class="row" style="justify-content:flex-end;margin-top:14px">
          <button class="btn ghost" :disabled="testing" @click="test">发送测试</button>
          <button class="btn" :disabled="saving" @click="save">保存</button>
        </div>
        <p class="hint" style="margin:12px 0 0">
          appsecret 只保存在服务端数据库，不会再回传到浏览器。测试号申请入口：微信公众平台 → 接口测试号。
        </p>
      </template>
    </div>`,
};
