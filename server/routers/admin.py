# -*- coding: utf-8 -*-
"""管理员：邀请码管理、日结价维护。"""
import secrets

from fastapi import APIRouter, Depends, HTTPException

from app.timeutil import ts_text

from .. import auth, db
from ..schemas import InviteIn, SettleIn

router = APIRouter(prefix="/api", tags=["admin"])


# ---------- 邀请码 ----------
@router.get("/admin/invites")
def list_invites(user=Depends(auth.require_admin)):
    return db.fetch_all(
        "SELECT i.code, i.created_at, i.used_at, u.username AS used_by "
        "FROM invites i LEFT JOIN users u ON u.id=i.used_by "
        "ORDER BY i.created_at DESC")


@router.post("/admin/invites")
def create_invites(body: InviteIn, user=Depends(auth.require_admin)):
    n = max(1, min(20, int(body.count)))
    rows = []
    for _ in range(n):
        code = secrets.token_hex(4)
        rows.append((code, user["id"], ts_text()))
    db.executemany("INSERT OR IGNORE INTO invites(code, created_by, created_at) VALUES(?,?,?)", rows)
    return {"codes": [r[0] for r in rows]}


# ---------- 日结价（全局共享，读对所有用户开放，写仅管理员） ----------
@router.get("/settles")
def list_settles(from_: str = "", to: str = "", user=Depends(auth.current_user)):
    sql = "SELECT day, price, src FROM settles"
    args = []
    cond = []
    if from_:
        cond.append("day>=?")
        args.append(from_)
    if to:
        cond.append("day<=?")
        args.append(to)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY day"
    return db.fetch_all(sql, tuple(args))


@router.put("/settles/{day}")
def put_settle(day: str, body: SettleIn, user=Depends(auth.require_admin)):
    if body.price <= 0:
        raise HTTPException(400, "价格必须大于 0")
    db.execute(
        "INSERT INTO settles(day, price, src) VALUES(?,?, 'manual') "
        "ON CONFLICT(day) DO UPDATE SET price=excluded.price, src='manual'",
        (day.strip(), float(body.price)),
    )
    return db.fetch_one("SELECT day, price, src FROM settles WHERE day=?", (day.strip(),))
