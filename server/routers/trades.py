# -*- coding: utf-8 -*-
"""交易记录 CRUD（严格按 user_id 隔离）。"""
from fastapi import APIRouter, Depends, HTTPException

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


@router.get("")
def list_trades(user=Depends(auth.current_user)):
    return db.fetch_all(
        "SELECT id, date, ts, grams, amount, price, kind, note, created_at "
        "FROM trades WHERE user_id=? "
        "ORDER BY COALESCE(NULLIF(ts,''), date, '') DESC, id DESC",
        (user["id"],),
    )


@router.post("")
def add_trade(body: TradeIn, user=Depends(auth.current_user)):
    if body.grams <= 0:
        raise HTTPException(400, "克数必须大于 0")
    if body.amount <= 0:
        raise HTTPException(400, "金额必须大于 0")
    price = body.price if body.price and body.price > 0 else body.amount / body.grams
    date = _norm_date(body.date) or db.ts_text()[:10]
    ts = _norm_ts(body.ts)
    tid = db.execute(
        "INSERT INTO trades(user_id, date, ts, grams, amount, price, kind, note, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (user["id"], date, ts or None, float(body.grams), float(body.amount),
         float(price), body.kind or "buy", body.note.strip(), db.ts_text()),
    )
    try:
        from ..engine import engine
        engine.refresh_users()
    except Exception:
        pass
    return db.fetch_one(
        "SELECT id, date, ts, grams, amount, price, kind, note, created_at "
        "FROM trades WHERE id=? AND user_id=?", (tid, user["id"]))


@router.delete("/{trade_id}")
def delete_trade(trade_id: int, user=Depends(auth.current_user)):
    n = db.execute_rowcount("DELETE FROM trades WHERE id=? AND user_id=?",
                            (trade_id, user["id"]))
    if n == 0:
        raise HTTPException(404, "记录不存在")
    return {"ok": True}
