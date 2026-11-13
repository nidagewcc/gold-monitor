/* 全局状态：行情快照、用户、持仓、在线状态；离线时保留上次缓存。 */
(function () {
  const KEY = "gm_cache_v1";

  function readCache() {
    try { return JSON.parse(localStorage.getItem(KEY) || "null") || {}; } catch (e) { return {}; }
  }

  const cached = readCache();

  window.store = Vue.reactive({
    user: null,
    settings: null,
    position: null,
    trades: [],
    plans: [],
    snapshot: cached.snapshot || { prices: {}, market: {}, time: "", date: "" },
    online: false,          // SSE 是否已连上
    stale: false,           // 展示的是缓存快照
    toasts: [],
  });

  let saveTimer = null;
  window.saveCache = function () {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      try {
        localStorage.setItem(KEY, JSON.stringify({ snapshot: store.snapshot }));
      } catch (e) { /* 忽略配额错误 */ }
    }, 300);
  };

  window.pushToast = function (title, message, kind) {
    const item = { id: Date.now() + Math.random(), title, message, kind: kind || "down" };
    store.toasts.push(item);
    setTimeout(() => {
      const i = store.toasts.findIndex((t) => t.id === item.id);
      if (i >= 0) store.toasts.splice(i, 1);
    }, 6000);
  };
})();
