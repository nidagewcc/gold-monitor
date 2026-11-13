# -*- coding: utf-8 -*-
"""注册 / 登录 / 登出 / 当前用户。"""
import os
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import auth, db
from ..schemas import LoginIn, RegisterIn

router = APIRouter(prefix="/api/auth", tags=["auth"])

INVITE_REQUIRED = os.environ.get("INVITE_REQUIRED", "true").lower() in ("1", "true", "yes")

# 登录失败节流：IP -> [时间戳]
_fails = {}
_FAIL_WINDOW = 300
_FAIL_LIMIT = 5


def _too_many(ip):
    now = time.time()
    arr = [t for t in _fails.get(ip, []) if now - t < _FAIL_WINDOW]
    _fails[ip] = arr
    return len(arr) >= _FAIL_LIMIT


def _mark_fail(ip):
    _fails.setdefault(ip, []).append(time.time())


def _user_payload(row):
    return {"id": row["id"], "username": row["username"], "is_admin": bool(row["is_admin"])}


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response):
    name = body.username.strip()
    if not name:
        raise HTTPException(400, "用户名不能为空")

    invite = None
    if INVITE_REQUIRED:
        invite = db.fetch_one(
            "SELECT code FROM invites WHERE code=? AND used_by IS NULL",
            (body.invite.strip(),),
        )
        if not invite:
            raise HTTPException(400, "邀请码无效或已被使用")

    if db.fetch_one("SELECT id FROM users WHERE username=?", (name,)):
        raise HTTPException(400, "用户名已存在")

    count = db.fetch_one("SELECT COUNT(*) AS n FROM users")["n"]
    pwd_hash, salt = auth.hash_password(body.password)
    uid = db.execute(
        "INSERT INTO users(username, pwd_hash, salt, is_admin, created_at) VALUES(?,?,?,?,?)",
        (name, pwd_hash, salt, 1 if count == 0 else 0,
         db.ts_text()),
    )
    db.execute("INSERT INTO settings(user_id) VALUES(?)", (uid,))
    db.execute("INSERT INTO positions(user_id) VALUES(?)", (uid,))
    if invite:
        db.execute("UPDATE invites SET used_by=?, used_at=? WHERE code=?",
                   (uid, db.ts_text(), invite["code"]))

    auth.set_session(response, uid)
    row = db.fetch_one("SELECT id, username, is_admin FROM users WHERE id=?", (uid,))
    return _user_payload(row)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else "-"
    if _too_many(ip):
        raise HTTPException(429, "尝试过于频繁，请 5 分钟后再试")

    row = db.fetch_one("SELECT * FROM users WHERE username=?", (body.username.strip(),))
    if not row or not auth.verify_password(body.password, row["salt"], row["pwd_hash"]):
        _mark_fail(ip)
        raise HTTPException(401, "用户名或密码错误")

    _fails.pop(ip, None)
    auth.set_session(response, row["id"])
    return _user_payload(row)


@router.post("/logout")
def logout(response: Response):
    auth.clear_session(response)
    return {"ok": True}


@router.get("/me")
def me(user=Depends(auth.current_user)):
    return _user_payload(user)


@router.get("/invite-required")
def invite_required():
    return {"required": INVITE_REQUIRED}
