# -*- coding: utf-8 -*-
"""微信推送配置：读取掩码、部分更新、测试发送。"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.wechat import WeChatPusher

from .. import auth, db
from ..schemas import WxIn

router = APIRouter(prefix="/api/wx", tags=["wx"])


def _row(uid):
    r = db.fetch_one(
        "SELECT wx_enabled, wx_appid, wx_secret, wx_openid, wx_template_id "
        "FROM settings WHERE user_id=?", (uid,))
    if not r:
        db.execute("INSERT INTO settings(user_id) VALUES(?)", (uid,))
        return _row(uid)
    return r


def _payload(uid):
    r = _row(uid)
    return {
        "enabled": bool(r["wx_enabled"]),
        "appid": r["wx_appid"],
        "openid": r["wx_openid"],
        "template_id": r["wx_template_id"],
        "has_secret": bool(r["wx_secret"]),   # appsecret 永不回传
    }


def _cfg(uid):
    r = _row(uid)
    return {
        "wx_enabled": bool(r["wx_enabled"]),
        "wx_appid": r["wx_appid"],
        "wx_secret": r["wx_secret"],
        "wx_openid": r["wx_openid"],
        "wx_template_id": r["wx_template_id"],
    }


def _notify_engine():
    try:
        from ..engine import engine
        engine.refresh_users()
    except Exception:
        pass


def _test_fields(uid):
    """测试推送也用模板变量，避免模板收到空消息。"""
    from ..engine import engine
    snap = engine.latest or {}
    price = (snap.get("prices") or {}).get("jicun_active")
    st = engine.users.get(uid) or {}
    return engine._wx_fields(uid, st, {"type": "test", "price": price or 0})


@router.get("")
def get_wx(user=Depends(auth.current_user)):
    return _payload(user["id"])


@router.put("")
def put_wx(body: WxIn, user=Depends(auth.current_user)):
    uid = user["id"]
    _row(uid)
    fields, args = [], []
    if body.enabled is not None:
        fields.append("wx_enabled=?")
        args.append(1 if body.enabled else 0)
    for field, col in (("appid", "wx_appid"), ("openid", "wx_openid"),
                       ("template_id", "wx_template_id")):
        val = getattr(body, field)
        if val is not None:
            fields.append(f"{col}=?")
            args.append(val.strip())
    if body.secret:                       # 留空 = 不修改
        fields.append("wx_secret=?")
        args.append(body.secret.strip())
    if fields:
        args.append(uid)
        db.execute(f"UPDATE settings SET {', '.join(fields)} WHERE user_id=?", args)
        _notify_engine()
    return _payload(uid)


@router.post("/test")
async def test_wx(user=Depends(auth.current_user)):
    cfg = _cfg(user["id"])
    pusher = WeChatPusher()
    if not pusher.configured(cfg):
        raise HTTPException(400, "微信参数不完整（appID / appsecret / openid / 模板ID 都要填）")
    err = await asyncio.to_thread(pusher.send, cfg, _test_fields(user["id"]))
    if err:
        raise HTTPException(400, err)
    return {"ok": True}
