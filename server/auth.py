# -*- coding: utf-8 -*-
"""账号与会话：scrypt 密码哈希 + 签名 Cookie。"""
import hashlib
import hmac
import os
import secrets

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import db

SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-insecure-change-me"
COOKIE_NAME = "gm_session"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
SESSION_MAX_AGE = 30 * 24 * 3600

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="gm-session")


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                        n=2 ** 14, r=8, p=1, dklen=32)
    return dk.hex(), salt


def verify_password(password, salt, expected):
    dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                        n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(dk.hex(), expected)


def set_session(response: Response, user_id):
    token = _serializer.dumps({"uid": user_id})
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_MAX_AGE,
        httponly=True, samesite="lax", secure=COOKIE_SECURE, path="/",
    )


def clear_session(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")


def current_user(request: Request):
    """FastAPI 依赖：未登录抛 401。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except BadSignature:
        raise HTTPException(status_code=401, detail="登录状态无效")
    row = db.fetch_one("SELECT id, username, is_admin FROM users WHERE id=?",
                       (data.get("uid"),))
    if not row:
        raise HTTPException(status_code=401, detail="账号不存在")
    return row


def require_admin(user=Depends(current_user)):
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
