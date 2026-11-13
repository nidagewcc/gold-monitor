# -*- coding: utf-8 -*-
"""微信公众平台测试号模板消息推送。"""
import json
import threading
import time
import urllib.parse
import urllib.request

from .constants import HEADERS, SSL_CONTEXT, WX_API_BASE


class WeChatPusher:
    """测试号模板消息推送：access_token 内存缓存（有效期 7200 秒，提前 5 分钟刷新）。
    所有方法均为阻塞式网络调用，调用方需自行放到后台线程。"""

    def __init__(self):
        self._token = ""
        self._token_exp = 0.0
        self._lock = threading.Lock()

    def configured(self, cfg):
        return all(str(cfg.get(k) or "").strip() for k in
                   ("wx_appid", "wx_secret", "wx_openid", "wx_template_id"))

    def _get_token(self, cfg):
        with self._lock:
            if self._token and time.time() < self._token_exp:
                return self._token
            url = (f"{WX_API_BASE}/cgi-bin/token?grant_type=client_credential"
                   f"&appid={urllib.parse.quote(cfg['wx_appid'].strip())}"
                   f"&secret={urllib.parse.quote(cfg['wx_secret'].strip())}")
            req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            if "access_token" not in d:
                raise RuntimeError(f"获取 access_token 失败：{d.get('errmsg') or d}（errcode={d.get('errcode')}）")
            self._token = d["access_token"]
            self._token_exp = time.time() + int(d.get("expires_in", 7200)) - 300
            return self._token

    def send(self, cfg, content_or_dict):
        """发送一条模板消息。第二个参数可以是 str（单变量 content，向后兼容）或 dict（多变量）。"""
        if not self.configured(cfg):
            return "微信推送参数不完整（appID/appsecret/openid/模板ID 都要填）"
        try:
            token = self._get_token(cfg)
            if isinstance(content_or_dict, dict):
                # 多变量模板：dict 的键就是模板里的 {{xxx.DATA}} 变量名（不含 .DATA 后缀）
                data = {}
                for k, v in content_or_dict.items():
                    data[k] = {"value": str(v) if v is not None else ""}
            else:
                # 单变量 content（旧模板兜底）
                data = {"content": {"value": str(content_or_dict), "color": "#9c6b1f"}}
            payload = {
                "touser": cfg["wx_openid"].strip(),
                "template_id": cfg["wx_template_id"].strip(),
                "data": data,
            }
            url = f"{WX_API_BASE}/cgi-bin/message/template/send?access_token={token}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8",
                         "User-Agent": HEADERS["User-Agent"]},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            if d.get("errcode", 0) != 0:
                # token 失效时清缓存，下次发送自动重新获取
                if d.get("errcode") in (40001, 40014, 42001):
                    with self._lock:
                        self._token, self._token_exp = "", 0.0
                return f"微信接口返回错误：{d.get('errmsg')}（errcode={d.get('errcode')}）"
            return None
        except Exception as e:
            return f"微信推送失败：{e}"
