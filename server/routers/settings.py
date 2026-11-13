# -*- coding: utf-8 -*-
"""用户偏好设置（含微信配置，secret 永不回传）。"""
from fastapi import APIRouter, Depends, HTTPException

from .. import auth, db
from ..schemas import SettingsIn

router = APIRouter(prefix="/api/settings", tags=["settings"])

ALLOWED_SOURCES = ("jicun_active", "jicun_sell", "ruyi_active", "ruyi_sell",
                   "account_buy", "account_sell", "au9999")


def _row(uid):
    r = db.fetch_one("SELECT * FROM settings WHERE user_id=?", (uid,))
    if not r:
        db.execute("INSERT INTO settings(user_id) VALUES(?)", (uid,))
        r = db.fetch_one("SELECT * FROM settings WHERE user_id=?", (uid,))
    return r


@router.get("")
def get_settings(user=Depends(auth.current_user)):
    r = _row(user["id"])
    r.pop("wx_secret", None)
    r["wx_has_secret"] = bool(db.fetch_one(
        "SELECT wx_secret FROM settings WHERE user_id=?", (user["id"],))["wx_secret"])
    r["alert_up"] = bool(r["alert_up"])
    r["privacy"] = bool(r["privacy"])
    r["wx_enabled"] = bool(r["wx_enabled"])
    return r


@router.put("")
def put_settings(body: SettingsIn, user=Depends(auth.current_user)):
    uid = user["id"]
    _row(uid)
    fields, args = [], []
    data = body.model_dump(exclude_none=True)
    if "source" in data:
        if data["source"] not in ALLOWED_SOURCES:
            raise HTTPException(400, "未知监控品种")
        fields.append("source=?")
        args.append(data["source"])
    if "step" in data:
        if data["step"] <= 0:
            raise HTTPException(400, "档位必须大于 0")
        fields.append("step=?")
        args.append(float(data["step"]))
    if "interval" in data:
        if data["interval"] < 3:
            raise HTTPException(400, "间隔不能小于 3 秒")
        fields.append("interval=?")
        args.append(int(data["interval"]))
    for key in ("alert_up", "privacy"):
        if key in data:
            fields.append(f"{key}=?")
            args.append(1 if data[key] else 0)
    if "theme" in data:
        if data["theme"] not in ("light", "dark"):
            raise HTTPException(400, "未知主题")
        fields.append("theme=?")
        args.append(data["theme"])
    if fields:
        args.append(uid)
        db.execute(f"UPDATE settings SET {', '.join(fields)} WHERE user_id=?", args)
        _notify_engine()
    return get_settings(user)


def _notify_engine():
    try:
        from ..engine import engine
        engine.refresh_users()
    except Exception:
        pass
