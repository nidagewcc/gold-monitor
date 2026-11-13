# -*- coding: utf-8 -*-
"""每日盈亏重算（支付宝同口径）：日结价 + 交易明细 → 逐日盈亏行。

纯函数，不依赖数据库，便于单测。
"""
from .constants import REDEEM_FEE_RATE


def daily_pl_rows(settles, est_daily, trades, total_grams,
                  live_sell=None, today=None, now_hm=None):
    """根据每晚 22:20 日结价与买卖明细，逐日重算每日盈亏。

    settles:    {"YYYY-MM-DD": 日结价} 精确价（工行 22:20 截止自报价，管理员可改）
    est_daily:  [["YYYY-MM-DD", 开, 收, 高, 低], ...] 黄金9999日K，仅用于无日结价
                日期的近似（标记 est=True）
    trades:     [{"date": "YYYY-MM-DD", "grams": g, "amount": a,
                  "kind": "buy"|"sell"}, ...]
    total_grams: 当前总持仓克数；底仓 = 总持仓 − 净买入合计
    live_sell:  最新积存金赎回价；盘中且 today 有基准时，今日行按实时价计算
    today:      实时盈亏所属交易日
    now_hm:     当前 "HH:MM"，默认 "23:59"（视为已收盘）

    口径（与支付宝「收益明细」一致）：
      当日盈亏 = 期末持仓市值 − 期初持仓市值 − 当日买入金额 + 当日卖出净额
               = 期末克数 × 今日价 − 期初克数 × 上一交易日日结价
                 − 当日买入金额 + 当日卖出成交额 × (1 − 0.5% 手续费)
      当日有买卖时，不能用「期末持仓 × 价差」——那样新买的克数会被当成
      全天持有，结果与支付宝对不上（例如 11-12 买 7.15 克，误差约 190 元）。
    """
    est_close = {r[0]: float(r[2]) for r in (est_daily or [])}
    close = {}
    est_flag = {}
    for d, p in (settles or {}).items():
        close[d] = float(p)
        est_flag[d] = False
    for d, p in est_close.items():
        if d not in close:
            close[d] = p
            est_flag[d] = True
    if not close:
        return {}

    dates = sorted(close)
    dates2 = sorted(set(dates + [today])) if today and today not in close else dates

    # 买卖按交易日归集；非交易日成交顺延到之后首个有行情日
    buy_g, buy_a, sell_g, sell_a = {}, {}, {}, {}
    for t in trades:
        try:
            g = float(t.get("grams") or 0)
            a = float(t.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if g <= 0:
            continue
        ds = str(t.get("date") or t.get("dt") or "")[:10]
        settle = next((d for d in dates2 if d >= ds), dates2[-1])
        kind = str(t.get("kind") or t.get("side") or "buy").lower()
        if kind == "sell":
            sell_g[settle] = sell_g.get(settle, 0.0) + g
            sell_a[settle] = sell_a.get(settle, 0.0) + a
        else:
            buy_g[settle] = buy_g.get(settle, 0.0) + g
            buy_a[settle] = buy_a.get(settle, 0.0) + a

    detail_g = sum(buy_g.values()) - sum(sell_g.values())
    base_g = max(float(total_grams or 0) - detail_g, 0.0)
    today = today or dates2[-1]
    now_hm = now_hm or "23:59"

    rows = {}
    grams_open = base_g
    prev_d, prev_c = None, None
    for d in dates2:
        cur_c = close.get(d)
        is_today = bool(live_sell) and d == today and now_hm <= "22:20"
        g_t, a_t = buy_g.get(d, 0.0), buy_a.get(d, 0.0)
        s_g, s_a = sell_g.get(d, 0.0), sell_a.get(d, 0.0)
        if cur_c is None and not is_today:
            grams_open += g_t - s_g
            continue
        if prev_c is None:
            grams_open += g_t - s_g
            if cur_c is not None:
                prev_d, prev_c = d, cur_c
            continue
        mark = float(live_sell) if is_today else cur_c
        held = grams_open + g_t - s_g
        sell_px = (s_a / s_g) if s_g > 0 else 0.0
        sell_fee = s_a * REDEEM_FEE_RATE if s_g > 0 else 0.0
        sell_net = s_a - sell_fee
        # 支付宝口径：当日收益 = 期末市值 − 期初市值 − 当日净买入
        pl = held * mark - grams_open * prev_c - a_t + sell_net
        sell_pl = (s_g * (sell_px - prev_c) - sell_fee) if s_g > 0 else 0.0
        rows[d] = {
            "date": d,
            "close": cur_c if cur_c is not None else mark,
            "prev": prev_c,
            "prev_day": prev_d,
            "mark": mark,
            "grams_open": grams_open,
            "buy_g": g_t,
            "buy_cost": a_t,
            "sell_g": s_g,
            "sell_cost": s_a,
            "sell_px": sell_px,
            "sell_fee": sell_fee,
            "pl": pl,
            "is_today": is_today,
            "est": bool(est_flag.get(d) or est_flag.get(prev_d)),
        }
        grams_open += g_t - s_g
        if cur_c is not None:
            prev_d, prev_c = d, cur_c
    return rows
