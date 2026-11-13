# -*- coding: utf-8 -*-
"""FastAPI 入口：装配路由、校验写请求头、挂载前端静态文件、启动全局行情引擎。"""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.timeutil import now_cn

from . import db
from .engine import engine
from .routers import (admin, auth as auth_routes, market, pl, plans, portfolio,
                      settings, stream, trades, wx)

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    print(f"[启动] 服务器时间 {now_cn().strftime('%Y-%m-%d %H:%M:%S')}（UTC+8，与系统时区无关）")
    engine.start(asyncio.get_running_loop())
    try:
        yield
    finally:
        engine.stop()


app = FastAPI(title="积存金价格监控", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def fetch_header_guard(request: Request, call_next):
    """写接口要求 X-Requested-With: fetch，配合 SameSite=Lax 防 CSRF。"""
    if request.method not in SAFE_METHODS and request.url.path.startswith("/api/"):
        if request.headers.get("x-requested-with", "").lower() != "fetch":
            return JSONResponse({"detail": "缺少请求头 X-Requested-With"}, status_code=403)
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"ok": True, "time": now_cn().strftime("%Y-%m-%d %H:%M:%S")}


for r in (auth_routes.router, market.router, stream.router, portfolio.router,
          trades.router, plans.router, settings.router, wx.router, admin.router,
          pl.router):
    app.include_router(r)

if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
