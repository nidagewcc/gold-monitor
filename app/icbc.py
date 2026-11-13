# -*- coding: utf-8 -*-
"""工行公开行情接口取价。"""
import json
import urllib.parse
import urllib.request

from .constants import HEADERS, ICBC_SERVLET, PAPI_PRODUCT_LIST, SSL_CONTEXT
from .timeutil import now_cn


def _post_trancode(tran_code, extra=None, timeout=10):
    data = {"tranCode": tran_code}
    if extra:
        data.update(extra)
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(ICBC_SERVLET, data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gbk"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise RuntimeError("行情返回内容无法解析")


def fetch_prices():
    """
    返回 dict：各品种当前价，以及附加信息（最高/最低/时间）。
    任何网络异常向上抛出，由调用方处理。
    """
    result = {"time": now_cn().strftime("%H:%M:%S")}

    # 1) 积存金 A00622
    try:
        d = _post_trancode("A00622")
        rf = (d.get("rf") or [{}])[0]
        result["jicun_active"] = float(rf.get("ActivePrice") or 0) or None
        result["jicun_sell"] = float(rf.get("SellPrice") or 0) or None
        result["high"] = float(rf.get("HighPrice") or 0) or None
        result["low"] = float(rf.get("LowPrice") or 0) or None
        result["date"] = rf.get("RegDate", "")
    except Exception:
        pass

    # 2) 如意金积存 A00505（价格单位 0.01 元）
    try:
        d = _post_trancode("A00505")
        p = (d.get("pronoinfo") or [{}])[0]
        bp = float(p.get("buyprice") or 0)
        sp = float(p.get("sellprice") or 0)
        result["ruyi_active"] = bp / 100.0 if bp else None
        result["ruyi_sell"] = sp / 100.0 if sp else None
    except Exception:
        pass

    # 3) 人民币账户黄金 A00500
    try:
        d = _post_trancode(
            "A00500",
            {"Area_code": "0200", "trademode": "1", "proIdsIn": "", "isFirstTime": "1"},
        )
        market = d.get("market") or []
        for m in market:
            if m.get("metalname") == "人民币账户黄金":
                result["account_buy"] = float(m.get("buyprice") or 0) or None
                result["account_sell"] = float(m.get("sellprice") or 0) or None
                break
    except Exception:
        pass

    # 4) 上金所 Au99.99（m 站公开接口）
    try:
        url = PAPI_PRODUCT_LIST + "?type=0"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": HEADERS["User-Agent"],
                "Referer": "https://m.icbc.com.cn/",
            },
        )
        with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        for it in d.get("data") or []:
            if it.get("id") == "Au99.99":
                result["au9999"] = float(it.get("newPrice") or 0) or None
                break
    except Exception:
        pass

    return result
