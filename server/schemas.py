# -*- coding: utf-8 -*-
"""请求/响应数据结构。"""
from typing import List, Optional

from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    invite: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


class SettingsIn(BaseModel):
    source: Optional[str] = None
    step: Optional[float] = None
    interval: Optional[int] = None
    alert_up: Optional[bool] = None
    privacy: Optional[bool] = None
    theme: Optional[str] = None


class PositionIn(BaseModel):
    grams: float = 0
    cost: float = 0


class TradeIn(BaseModel):
    date: str
    grams: float
    amount: float
    kind: str = "buy"
    note: str = ""
    price: Optional[float] = None


class PlanIn(BaseModel):
    price: float
    grams: float = 0


class PlansIn(BaseModel):
    items: List[PlanIn] = []


class WxIn(BaseModel):
    enabled: Optional[bool] = None
    appid: Optional[str] = None
    secret: Optional[str] = None
    openid: Optional[str] = None
    template_id: Optional[str] = None


class SettleIn(BaseModel):
    price: float


class InviteIn(BaseModel):
    count: int = 1
