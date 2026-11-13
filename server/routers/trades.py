# -*- coding: utf-8 -*-
"""交易记录 CRUD（严格按 user_id 隔离）。"""
from fastapi import APIRouter, Depends, HTTPException

from .. import auth, db
from ..schemas import TradeIn

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("")
def list_trades(user=Depends(auth.current_user)):
    return db.fetch_all(
        "SELECT id, date, grams, amount, price, kind, note, created_at "
        "FROM trades WHERE user_id=? ORDER BY date DESC, id DESC",
        (user["id"],),
    )


@router.post("")
def add_trade(body: TradeIn, user=Depends(auth.current_user)):
    if body.grams <= 0:
        raise HTTPException(400, "克数必须大于 0")
    if body.amount <= 0:
        raise HTTPException(400, "金额必须大于 0")
    price = body.price if body.price and body.price > 0 else body.amount / body.grams
    tid = db.execute(
        "INSERT INTO trades(user_id, date, grams, amount, price, kind, note, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (user["id"], body.date.strip(), float(body.grams), float(body.amount),
         float(price), body.kind or "buy", body.note.strip(), db.ts_text()),
    )
    try:
        from ..engine import engine
        engine.refresh_users()
    except Exception:
        pass
    return db.fetch_one(
        "SELECT id, date, grams, amount, price, kind, note, created_at "
        "FROM trades WHERE id=? AND user_id=?", (tid, user["id"]))


@router.delete("/{trade_id}")
def delete_trade(trade_id: int, user=Depends(auth.current_user)):
    n = db.execute_rowcount("DELETE FROM trades WHERE id=? AND user_id=?",
                            (trade_id, user["id"]))
    if n == 0:
        raise HTTPException(404, "记录不存在")
    return {"ok": True}
