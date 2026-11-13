# -*- coding: utf-8 -*-
"""行情：当前快照、分时/日K/本地分时图表数据。"""
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException

from app import eastmoney, tiers
from app.constants import EM_KLINE_TTL, EM_TRENDS_TTL, SOURCES
from app.timeutil import now_cn, today_str

from .. import auth, db
from ..engine import engine

router = APIRouter(prefix="/api/market", tags=["market"])

_cache = {}   # key -> (过期时间戳, 数据)


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        val = fn()
    except Exception as e:
        # 东财边缘节点偶发掐连接：有旧数据先顶上，避免整块图表失败
        if hit:
            return hit[1]
        raise HTTPException(502, f"行情数据获取失败：{e}")
    _cache[key] = (time.time() + ttl, val)
    return val


@router.get("/snapshot")
def snapshot(user=Depends(auth.current_user)):
    snap = engine.latest or {}
    day = today_str()
    settle = db.fetch_one("SELECT day, price, src FROM settles WHERE day=?", (day,))
    return {
        "time": snap.get("time"),
        "date": snap.get("date") or day,
        "prices": snap.get("prices") or {},
        "high": snap.get("high"),
        "low": snap.get("low"),
        "market": snap.get("market") or {
            "open": tiers.is_trade_hours(now_cn()),
            "paused": not tiers.is_trade_hours(now_cn()),
            "settle_locked": bool(settle),
        },
        "settle": settle,
        "sources": {k: v["name"] for k, v in SOURCES.items()},
        "server_time": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/chart")
def chart(mode: str = "trend", day: str = "", beg: str = "", end: str = "",
          user=Depends(auth.current_user)):
    if mode == "trend":
        return _cached("trend", EM_TRENDS_TTL, eastmoney.fetch_em_trends)

    if mode == "daily":
        today = now_cn()
        beg = beg or (today - timedelta(days=370)).strftime("%Y%m%d")
        end = end or today.strftime("%Y%m%d")
        rows = _cached(f"daily:{beg}:{end}", EM_KLINE_TTL,
                       lambda: eastmoney.fetch_em_daily(beg, end))
        return {"beg": beg, "end": end, "rows": rows}

    if mode == "local":
        if not day:
            latest = db.fetch_one(
                "SELECT day FROM history ORDER BY day DESC LIMIT 1")
            day = latest["day"] if latest else today_str()
        rows = db.fetch_all(
            "SELECT hm, price FROM history WHERE day=? ORDER BY hm", (day,))
        days = [r["day"] for r in db.fetch_all(
            "SELECT DISTINCT day FROM history ORDER BY day DESC LIMIT 40")]
        return {"day": day, "days": days,
                "points": [[r["hm"], r["price"]] for r in rows]}

    raise HTTPException(400, "未知图表模式")
