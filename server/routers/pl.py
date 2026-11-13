# -*- coding: utf-8 -*-
"""每日盈亏：日结价 + 交易明细逐日重算（支付宝口径）。"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from app import eastmoney, tiers
from app.constants import EM_KLINE_TTL
from app.portfolio import daily_pl_rows
from app.timeutil import now_cn, today_str

from .. import auth, db
from ..engine import engine
from .market import _cached

router = APIRouter(prefix="/api", tags=["pl"])


def _est_daily(beg, end):
    """黄金9999日K（仅用于无日结价日期的估算），失败时降级为空。"""
    if beg > end:
        return []
    try:
        return _cached(f"plk:{beg}:{end}", EM_KLINE_TTL,
                       lambda: eastmoney.fetch_em_daily(beg, end))
    except Exception:
        return []


@router.get("/daily_pl")
def daily_pl(from_: str = "", to: str = "", user=Depends(auth.current_user)):
    now = now_cn()
    today = today_str()
    to = (to or today)[:10]
    frm = (from_ or now.replace(day=1).strftime("%Y-%m-%d"))[:10]

    settles = {r["day"]: float(r["price"]) for r in
               db.fetch_all("SELECT day, price FROM settles")}
    trades = db.fetch_all(
        "SELECT date, grams, amount, kind FROM trades WHERE user_id=? ORDER BY date, id",
        (user["id"],))
    pos = db.fetch_one("SELECT grams FROM positions WHERE user_id=?", (user["id"],))
    grams = float(pos["grams"]) if pos else 0.0

    # 估算区间：请求区间前 15 天 ~ 今天（有日结价的日期不会用到估算）
    try:
        beg_dt = datetime.strptime(frm, "%Y-%m-%d") - timedelta(days=15)
    except ValueError:
        beg_dt = datetime.now() - timedelta(days=180)
    end_dt = datetime.strptime(to if to <= today else today, "%Y-%m-%d")
    est = _est_daily(beg_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d"))

    live = None
    hm = now.strftime("%H:%M")
    if frm <= today <= to and now.weekday() < 5 and tiers.in_trade_session(hm):
        snap = engine.latest or {}
        live = (snap.get("prices") or {}).get("jicun_sell")

    rows_map = daily_pl_rows(settles, est, trades, grams,
                             live_sell=live, today=today, now_hm=hm)
    rows = [rows_map[d] for d in sorted(rows_map) if frm <= d <= to]

    total = sum(r["pl"] for r in rows)
    best = max(rows, key=lambda r: r["pl"], default=None)
    worst = min(rows, key=lambda r: r["pl"], default=None)
    return {
        "from": frm,
        "to": to,
        "today": today,
        "rows": rows,
        "has_settle": bool(settles),
        "summary": {
            "total_pl": total,
            "days": len(rows),
            "best": {"date": best["date"], "pl": best["pl"]} if best else None,
            "worst": {"date": worst["date"], "pl": worst["pl"]} if worst else None,
        },
    }
