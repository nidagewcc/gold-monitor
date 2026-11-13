# -*- coding: utf-8 -*-
"""SSE 实时推送：tick / alert / plan / settle_locked / market_state。"""
import asyncio
import json

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from .. import auth
from ..engine import engine

router = APIRouter(prefix="/api", tags=["stream"])


@router.get("/stream")
async def stream(request: Request, user=Depends(auth.current_user)):
    sid, q = engine.subscribe(user["id"])

    async def gen():
        try:
            yield {
                "event": "hello",
                "data": json.dumps({"market": engine.market_state,
                                    "latest": engine.latest},
                                   ensure_ascii=False),
            }
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=20)
                except asyncio.TimeoutError:
                    continue          # 心跳由 EventSourceResponse 的 ping 负责
                yield {"event": ev.get("type", "message"),
                       "data": json.dumps(ev, ensure_ascii=False)}
        finally:
            engine.unsubscribe(sid)

    return EventSourceResponse(gen(), ping=15)
