# -*- coding: utf-8 -*-
"""东方财富公开行情：黄金9999（Au99.99）分时与历史日K。"""
import json
import time
import urllib.request

from .constants import EM_HEADERS, EM_KLINE_URL, EM_TRENDS_URL


def _get_em_json(url, timeout=6, retries=6):
    """取东财 JSON。该行情域边缘节点对高频访问会直接掐 TCP（RemoteDisconnected，
    属 ConnectionResetError），多节点轮询下时好时坏（实测约 1/8 放行），
    故对连接类错误做几次短退避重试；HTTP 错误响应（403/429 等）不重试。"""
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=EM_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except (ConnectionError, TimeoutError) as e:
            last_err = e
            if i < retries - 1:
                time.sleep(0.35 * (i + 1))
    raise last_err


def fetch_em_trends():
    """
    黄金9999最新会话分时（东财交易日=前一夜盘20:00-次日2:30 + 当日白盘，
    周一的会话还含周五夜盘）。
    返回 {"date": 东财交易日(夜盘起始日), "pre_close": 昨收,
          "points": [["YYYY-MM-DD HH:MM", 价格], ...]}（保留完整时间戳，
          由渲染方按自然日与积存金时段 9:10-15:30/20:00-22:20 自行过滤）。
    只保留价格大于 0 的分钟点（集合竞价/无成交时段会出现 0 值）。
    """
    d = (_get_em_json(EM_TRENDS_URL) or {}).get("data") or {}
    pts = []
    for row in d.get("trends") or []:
        seg = row.split(",")
        if len(seg) < 2:
            continue
        try:
            p = float(seg[1])  # f52 分钟成交价（seg[2] f53 为分钟均价）
        except (TypeError, ValueError):
            continue
        if p > 0 and len(seg[0]) >= 16:
            pts.append([seg[0][:16], p])
    date = d.get("date") or (d.get("trends") or [""])[0][:10]
    return {"date": date, "pre_close": d.get("preClose"),
            "name": d.get("name") or "黄金9999", "points": pts}


def fetch_em_daily(beg, end):
    """
    黄金9999历史日K，beg/end 为 "YYYYMMDD"。
    返回 [["YYYY-MM-DD", 开, 收, 高, 低], ...]（均为 float）。
    """
    d = (_get_em_json(EM_KLINE_URL.format(beg=beg, end=end)) or {}).get("data") or {}
    out = []
    for row in d.get("klines") or []:
        seg = row.split(",")
        if len(seg) < 6:
            continue
        try:
            out.append([seg[0]] + [float(x) for x in seg[1:5]])  # 开/收/高/低
        except ValueError:
            continue
    return out
