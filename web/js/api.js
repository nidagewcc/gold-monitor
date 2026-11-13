/* 统一请求封装：带 CSRF 头、超时、401 跳登录、错误信息提取。 */
(function () {
  const HDR = { "X-Requested-With": "fetch" };

  async function request(method, url, body, timeoutMs = 15000) {
    const opt = { method, credentials: "same-origin", headers: { ...HDR } };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const ctrl = new AbortController();
    opt.signal = ctrl.signal;
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    let resp;
    try {
      resp = await fetch(url, opt);
    } catch (e) {
      if (e.name === "AbortError") throw new Error("请求超时，请稍后重试");
      throw new Error("网络异常：" + e.message);
    } finally {
      clearTimeout(timer);
    }
    let data = null;
    try { data = await resp.json(); } catch (e) { /* 空响应 */ }
    if (resp.status === 401 && !url.startsWith("/api/auth/")) {
      location.href = "/login.html";
      throw new Error("未登录");
    }
    if (!resp.ok) {
      throw new Error((data && data.detail) || "请求失败（" + resp.status + "）");
    }
    return data;
  }

  window.api = {
    get: (u, t) => request("GET", u, undefined, t),
    post: (u, b, t) => request("POST", u, b, t),
    put: (u, b, t) => request("PUT", u, b, t),
    del: (u, t) => request("DELETE", u, undefined, t),
  };
})();
