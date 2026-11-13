# -*- coding: utf-8 -*-
"""持仓：克数与累计成本。"""
from fastapi import APIRouter, Depends

from .. import auth, db
from ..schemas import PositionIn

router = APIRouter(prefix="/api/position", tags=["position"])


def position_of(uid):
    r = db.fetch_one("SELECT grams, cost FROM positions WHERE user_id=?", (uid,))
    if not r:
        db.execute("INSERT INTO positions(user_id) VALUES(?)", (uid,))
        return {"grams": 0.0, "cost": 0.0}
    return {"grams": float(r["grams"]), "cost": float(r["cost"])}


@router.get("")
def get_position(user=Depends(auth.current_user)):
    return position_of(user["id"])


@router.put("")
def put_position(body: PositionIn, user=Depends(auth.current_user)):
    uid = user["id"]
    db.execute(
        "INSERT INTO positions(user_id, grams, cost) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET grams=excluded.grams, cost=excluded.cost",
        (uid, max(0.0, float(body.grams)), max(0.0, float(body.cost))),
    )
    try:
        from ..engine import engine
        engine.refresh_users()
    except Exception:
        pass
    return position_of(uid)
