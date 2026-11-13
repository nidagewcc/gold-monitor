# -*- coding: utf-8 -*-
"""交易记录 CRUD（严格按 user_id 隔离）。"""
from fastapi import APIRouter, Depends, HTTPException

from app.constants import REDEEM_FEE_RATE

from .. import auth, db
from ..schemas import TradeIn

router = APIRouter(prefix="/api/trades", tags=["trades"])


def _norm_date(s: str) -> str:
    """取前 10 位作为 YYYY-MM-DD，便于 portfolio 聚合按日期。"""
    return (s or "").strip()[:10]


def _norm_ts(s: str) -> str:
    """把前端传来的 datetime-local（YYYY-MM-DDTHH:MM 或 THH:MM:SS）规成 YYYY-MM-DD HH:MM:SS。"""
    v = (s or "").strip()
    if not v:
        return ""
    if "T" in v:
        v = v.replace("T", " ")
    # 如果只有 HH:MM，补秒
    parts = v.split()
    if len(parts) == 2:
        t = parts[1]
        if len(t) == 5:
            v = f"{parts[0]} {t}:00"
    return v


def _live_sell():
    """当前积存金赎回价，用于补仓浮动盈亏。"""
    try:
        from ..engine import engine
        px = ((engine.latest or {}).get("prices") or {}).get("jicun_sell")
        return float(px) if px else None
    except Exception:
        return None


def _enrich(rows):
    """补上每笔的手续费、盈亏与收益率（减仓按成交时持仓均价，扣 0.5% 赎回费）。"""
    live = _live_sell()
    for r in rows:
        g = float(r.get("grams") or 0)
        amt = float(r.get("amount") or 0)
        px = float(r.get("price") or 0)
        base = float(r.get("cost") or 0)
        r["pl"] = None
        r["pct"] = None
        if r.get("kind") == "sell":
            fee = amt * REDEEM_FEE_RATE
            r["fee"] = round(fee, 2)
            if base > 0 and g > 0:
                pl = (px - base) * g - fee
                r["pl"] = round(pl, 2)
                r["pct"] = round(pl / (base * g) * 100, 2)
        else:
            r["fee"] = 0.0
            if live and live > 0 and px > 0 and g > 0:
                r["pl"] = round((live - px) * g, 2)
                r["pct"] = round((live / px - 1) * 100, 2)
    return rows


@router.get("")
def list_trades(user=Depends(auth.current_user)):
    rows = db.fetch_all(
        "SELECT id, date, ts, grams, amount, price, kind, note, cost, created_at "
        "FROM trades WHERE user_id=? "
        "ORDER BY COALESCE(NULLIF(ts,''), date, '') DESC, id DESC",
        (user["id"],),
    )
    return _enrich(rows)


@router.post("")
def add_trade(body: TradeIn, user=Depends(auth.current_user)):
    if body.grams <= 0:
        raise HTTPException(400, "克数必须大于 0")
    if body.amount <= 0:
        raise HTTPException(400, "金额必须大于 0")
    price = body.price if body.price and body.price > 0 else body.amount / body.grams
    date = _norm_date(body.date) or db.ts_text()[:10]
    ts = _norm_ts(body.ts)
    kind = body.kind or "buy"
    # 减仓时记下成交当时的持仓均价，用于事后算这一笔的已实现盈亏
    cost = 0.0
    if kind == "sell":
        pos = db.fetch_one("SELECT grams, cost FROM positions WHERE user_id=?", (user["id"],))
        if pos and float(pos["grams"] or 0) > 0:
            cost = float(pos["cost"] or 0) / float(pos["grams"])
    tid = db.execute(
        "INSERT INTO trades(user_id, date, ts, grams, amount, price, kind, note, cost, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (user["id"], date, ts or None, float(body.grams), float(body.amount),
         float(price), kind, body.note.strip(), cost, db.ts_text()),
    )
    try:
        from ..engine import engine
        engine.refresh_users()
    except Exception:
        pass
    row = db.fetch_one(
        "SELECT id, date, ts, grams, amount, price, kind, note, cost, created_at "
        "FROM trades WHERE id=? AND user_id=?", (tid, user["id"]))
    return _enrich([row])[0]


@router.delete("/{trade_id}")
def delete_trade(trade_id: int, user=Depends(auth.current_user)):
    n = db.execute_rowcount("DELETE FROM trades WHERE id=? AND user_id=?",
                            (trade_id, user["id"]))
    if n == 0:
        raise HTTPException(404, "记录不存在")
    return {"ok": True}
