# -*- coding: utf-8 -*-
"""持仓：克数、累计成本，以及今日实时盈亏上下文。"""
from fastapi import APIRouter, Depends

from app import tiers
from app.constants import REDEEM_FEE_RATE
from app.portfolio import daily_pl_rows
from app.timeutil import now_cn, today_str

from .. import auth, db
from ..engine import engine
from ..schemas import PositionIn

router = APIRouter(prefix="/api/position", tags=["position"])


def _today_pl_ctx(uid):
    """今日盈亏上下文。

    只要今天是交易日且存在上一交易日日结基准，就返回基准数据：
      - 盘中取到实时价时 in_session=true，前端按 SSE 实时价跳动重算；
      - 实时价暂缺（开盘前后、轮询空窗）时 in_session=false，前端按昨结价
        给出预估，等实时价到达后自动切换；
      - 收盘/周末无实时价时，直接给后端按日结价算好的结果。
    无任何昨结基准（首个交易日建仓等）返回 None。
    """
    now = now_cn()
    today = today_str()
    hm = now.strftime("%H:%M")
    is_weekday = now.weekday() < 5

    live = None
    if is_weekday and tiers.in_trade_session(hm):
        px = ((engine.latest or {}).get("prices") or {}).get("jicun_sell")
        if px:
            live = float(px)

    settles = {r["day"]: float(r["price"])
               for r in db.fetch_all("SELECT day, price FROM settles")}
    trades = db.fetch_all(
        "SELECT date, grams, amount, kind FROM trades WHERE user_id=? ORDER BY date, id",
        (uid,))
    pos = db.fetch_one("SELECT grams FROM positions WHERE user_id=?", (uid,))
    grams = float(pos["grams"]) if pos else 0.0

    rows = daily_pl_rows(settles, [], trades, grams,
                         live_sell=live, today=today, now_hm=hm)
    row = rows.get(today)
    if not row:
        return None

    fee_rate = REDEEM_FEE_RATE
    if is_weekday:
        # 交易日：给前端基准数据；live 到达即跳动，否则按昨结预估
        return {
            "in_session": live is not None,
            "prev_close": row["prev"],
            "grams_open": row["grams_open"],
            "buy_g": row["buy_g"],
            "buy_a": row["buy_cost"],
            "sell_g": row["sell_g"],
            "sell_a": row["sell_cost"],
            "fee_rate": fee_rate,
        }
    # 非交易日（周末/节假日）：日结价已定格，后端直接给结果，前端原样展示
    return {"in_session": False, "pl": round(row["pl"], 2)}


def position_of(uid):
    r = db.fetch_one("SELECT grams, cost FROM positions WHERE user_id=?", (uid,))
    if not r:
        db.execute("INSERT INTO positions(user_id) VALUES(?)", (uid,))
        base = {"grams": 0.0, "cost": 0.0}
    else:
        base = {"grams": float(r["grams"]), "cost": float(r["cost"])}
    base["today"] = _today_pl_ctx(uid)
    return base


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
        engine.refresh_users()
    except Exception:
        pass
    return position_of(uid)
