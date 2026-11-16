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


def _get_position(conn, uid):
    r = conn.execute("SELECT grams, cost FROM positions WHERE user_id=?", (uid,)).fetchone()
    if r is None:
        conn.execute("INSERT INTO positions(user_id) VALUES(?)", (uid,))
        return 0.0, 0.0
    return float(r["grams"] or 0), float(r["cost"] or 0)


def _apply_to_position(conn, uid, grams, amount, kind):
    """把一笔成交联动到持仓，返回 (新克数, 新成本, 成交时均价)。

    买入：克数与成本同增，均价被自然摊薄/拉高；
    卖出：按当前持仓均价结转成本，剩余克数均价不变。
    """
    g, cost = _get_position(conn, uid)
    avg_before = (cost / g) if g > 0 else 0.0
    if kind == "sell":
        if grams > g + 1e-9:
            raise HTTPException(400, f"卖出克数超过当前持仓（当前 {g:.4f} 克）")
        g -= grams
        cost -= avg_before * grams
        if g <= 1e-9:  # 清仓后成本归零，避免浮点残差
            g, cost = 0.0, 0.0
    else:
        g += grams
        cost += amount
    conn.execute(
        "UPDATE positions SET grams=?, cost=? WHERE user_id=?", (g, cost, uid))
    return g, cost, avg_before


def _revert_position(conn, uid, grams, amount, kind, avg_at_trade):
    """删除成交时反向冲销其对持仓的影响。"""
    g, cost = _get_position(conn, uid)
    if kind == "sell":
        # 撤销卖出：把克数加回，成本按该笔成交时均价补回
        g += grams
        cost += avg_at_trade * grams
    else:
        # 撤销买入：克数与成本同减
        if grams > g + 1e-9:
            raise HTTPException(400, "回滚后持仓克数为负，请先核对交易记录")
        g -= grams
        cost -= amount
        if g <= 1e-9:
            g, cost = 0.0, 0.0
    conn.execute(
        "UPDATE positions SET grams=?, cost=? WHERE user_id=?", (g, cost, uid))
    return g, cost


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
    uid = user["id"]
    grams, amount = float(body.grams), float(body.amount)

    with db.transaction() as conn:
        # 减仓时先取成交当时的持仓均价，再联动扣减持仓
        pos = conn.execute(
            "SELECT grams, cost FROM positions WHERE user_id=?", (uid,)).fetchone()
        cost = 0.0
        if kind == "sell" and pos and float(pos["grams"] or 0) > 0:
            cost = float(pos["cost"] or 0) / float(pos["grams"])
        _apply_to_position(conn, uid, grams, amount, kind)
        tid = conn.execute(
            "INSERT INTO trades(user_id, date, ts, grams, amount, price, kind, note, cost, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (uid, date, ts or None, grams, amount, float(price), kind,
             body.note.strip(), cost, db.ts_text()),
        ).lastrowid

    try:
        from ..engine import engine
        engine.refresh_users()
    except Exception:
        pass
    row = db.fetch_one(
        "SELECT id, date, ts, grams, amount, price, kind, note, cost, created_at "
        "FROM trades WHERE id=? AND user_id=?", (tid, uid))
    return _enrich([row])[0]


@router.delete("/{trade_id}")
def delete_trade(trade_id: int, user=Depends(auth.current_user)):
    uid = user["id"]
    with db.transaction() as conn:
        r = conn.execute(
            "SELECT id, grams, amount, kind, cost FROM trades "
            "WHERE id=? AND user_id=?", (trade_id, uid)).fetchone()
        if r is None:
            raise HTTPException(404, "记录不存在")
        _revert_position(conn, uid, float(r["grams"]), float(r["amount"]),
                         r["kind"], float(r["cost"] or 0))
        conn.execute("DELETE FROM trades WHERE id=? AND user_id=?", (trade_id, uid))
    return {"ok": True}
