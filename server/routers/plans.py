# -*- coding: utf-8 -*-
"""补仓计划：整体替换式保存。"""
from fastapi import APIRouter, Depends, HTTPException

from .. import auth, db
from ..schemas import PlansIn

router = APIRouter(prefix="/api/plans", tags=["plans"])


@router.get("")
def list_plans(user=Depends(auth.current_user)):
    return db.fetch_all(
        "SELECT id, price, grams, active FROM plans WHERE user_id=? ORDER BY price DESC",
        (user["id"],),
    )


@router.put("")
def put_plans(body: PlansIn, user=Depends(auth.current_user)):
    uid = user["id"]
    rows = []
    for it in body.items:
        if it.price <= 0:
            raise HTTPException(400, "计划价必须大于 0")
        rows.append((uid, float(it.price), max(0.0, float(it.grams)), db.ts_text()))
    db.execute("DELETE FROM plans WHERE user_id=?", (uid,))
    if rows:
        db.executemany(
            "INSERT INTO plans(user_id, price, grams, active, created_at) VALUES(?,?,?,1,?)",
            rows,
        )
    try:
        from ..engine import engine
        engine.refresh_users()
    except Exception:
        pass
    return list_plans(user)
