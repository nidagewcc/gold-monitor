/* 根应用：加载用户/设置/持仓/快照 → 建立 SSE → 主题与提醒。 */
(function () {
  const { createApp } = Vue;
  const s = window.store;

  const App = {
    data() {
      return {
        s,
        es: null,
        tab: localStorage.getItem("gm_tab") || "market",
        tabs: [
          { key: "market", label: "行情" },
          { key: "chart", label: "走势" },
          { key: "trades", label: "交易" },
          { key: "pl", label: "盈亏" },
          { key: "plans", label: "补仓计划" },
          { key: "settings", label: "设置" },
        ],
      };
    },
    computed: {
      source() { return (s.settings && s.settings.source) || "jicun_active"; },
      sources() { return s.sources || {}; },
      sellPrice() { return (s.snapshot.prices && s.snapshot.prices.jicun_sell) || 0; },
      buyPrice() { return (s.snapshot.prices && s.snapshot.prices.jicun_active) || 0; },
      market() { return s.snapshot.market || {}; },
      privacy() { return !!(s.settings && s.settings.privacy); },
      theme() { return (s.settings && s.settings.theme) || "light"; },
      username() { return (s.user && s.user.username) || ""; },
    },
    watch: {
      theme(v) { document.documentElement.dataset.theme = v === "dark" ? "dark" : "light"; },
    },
    mounted() {
      document.documentElement.dataset.theme = this.theme;
      this.boot();
    },
    beforeUnmount() {
      if (this.es) this.es.close();
    },
    methods: {
      async boot() {
        try {
          const [me, settings, position, snap] = await Promise.all([
            api.get("/api/auth/me"),
            api.get("/api/settings"),
            api.get("/api/position"),
            api.get("/api/market/snapshot"),
          ]);
          s.user = me;
          s.settings = settings;
          s.position = position;
          s.sources = snap.sources || {};
          if (snap.prices && Object.keys(snap.prices).length) {
            this.applyTick(snap);
          }
          this.connect();
        } catch (e) {
          location.href = "/login.html";
        }
      },

      go(key) {
        this.tab = key;
        try { localStorage.setItem("gm_tab", key); } catch (e) { /* 忽略 */ }
        window.scrollTo({ top: 0, behavior: "smooth" });
      },

      applyTick(ev) {
        s.snapshot = {
          time: ev.time || s.snapshot.time,
          date: ev.date || s.snapshot.date,
          prices: ev.prices || s.snapshot.prices || {},
          high: ev.high,
          low: ev.low,
          market: ev.market || s.snapshot.market || {},
        };
        s.online = true;
        s.stale = false;
        saveCache();
      },

      connect() {
        if (this.es) this.es.close();
        const es = new EventSource("/api/stream");
        this.es = es;
        es.addEventListener("hello", (e) => {
          const d = JSON.parse(e.data);
          if (d.latest) this.applyTick(d.latest);
          if (d.market) s.snapshot.market = d.market;
          s.online = true;
        });
        es.addEventListener("tick", (e) => this.applyTick(JSON.parse(e.data)));
        es.addEventListener("market_state", (e) => {
          const d = JSON.parse(e.data);
          s.snapshot.market = { open: d.open, paused: d.paused, settle_locked: d.settle_locked };
        });
        es.addEventListener("settle_locked", (e) => {
          const d = JSON.parse(e.data);
          pushToast("日结价已定格", `${d.day} 收盘 ${Number(d.price).toFixed(2)} 元/克`, "plan");
        });
        es.addEventListener("alert", (e) => {
          const d = JSON.parse(e.data);
          pushToast(d.title, d.message, d.kind);
        });
        es.addEventListener("plan", (e) => {
          const d = JSON.parse(e.data);
          pushToast(d.title, d.message, "plan");
        });
        es.onerror = () => { s.online = false; s.stale = true; };
      },

      async toggleTheme() {
        const next = this.theme === "light" ? "dark" : "light";
        s.settings = { ...s.settings, theme: next };
        try { await api.put("/api/settings", { theme: next }); } catch (e) { /* 忽略 */ }
      },

      async logout() {
        try { await api.post("/api/auth/logout"); } catch (e) { /* 忽略 */ }
        location.href = "/login.html";
      },
    },
  };

  createApp(App)
    .component("market-badge", window.MarketBadge)
    .component("price-card", window.PriceCard)
    .component("position-card", window.PositionCard)
    .component("toast-stack", window.ToastStack)
    .component("trades-panel", window.TradesPanel)
    .component("pl-calendar", window.PlCalendar)
    .component("plans-panel", window.PlansPanel)
    .component("chart-panel", window.ChartPanel)
    .component("settings-panel", window.SettingsPanel)
    .component("wx-panel", window.WxPanel)
    .component("admin-panel", window.AdminPanel)
    .component("calc-panel", window.CalcPanel)
    .component("avg-down-panel", window.AvgDownPanel)
    .mount("#app");
})();
