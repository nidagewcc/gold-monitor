# -*- coding: utf-8 -*-
"""交易时段与价格档位判定（纯函数，便于单测）。"""
import math
from datetime import datetime

from .constants import TRADE_CLOSE_MIN, TRADE_OPEN_MIN


def is_trade_hours(now=None):
    """当前是否处于积存金交易时段（本地时间 9:10-22:20，含起点不含终点）。"""
    now = now or datetime.now()
    cur = now.hour * 60 + now.minute
    return TRADE_OPEN_MIN <= cur < TRADE_CLOSE_MIN


def in_trade_session(hm):
    """积存金交易时段：周一至周五 9:10-22:20（节假日无法本地判断，由接口有无报价兜底）。"""
    return "09:10" <= hm <= "22:20"


def trade_minutes():
    """交易时段总分钟数（9:10-22:20 连续计时，与支付宝分时横轴一致）。"""
    return (22 * 60 + 20) - (9 * 60 + 10)


def crossed_tiers(prev_price, price, step):
    """
    返回价格从 prev_price 跌到 price 过程中向下穿越的所有档位线（降序）。
    档位线为 step 的整数倍：step=10 时即 ...900, 890, 880...
    """
    if prev_price is None or price is None or price >= prev_price:
        return []
    crossed = []
    k_hi = math.floor(prev_price / step)
    k_lo = math.ceil(price / step)
    # 严格穿越：档位线 t 满足 price <= t < prev_price
    for k in range(k_hi, k_lo - 1, -1):
        t = k * step
        if price <= t < prev_price:
            crossed.append(t)
    return crossed


def crossed_tiers_up(prev_price, price, step):
    """
    返回价格从 prev_price 涨到 price 过程中向上穿越的所有档位线（升序）。
    与 crossed_tiers 对称：严格穿越，档位线 t 满足 prev_price < t <= price。
    """
    if prev_price is None or price is None or price <= prev_price:
        return []
    crossed = []
    k_lo = math.floor(prev_price / step) + 1
    k_hi = math.floor(price / step)
    for k in range(k_lo, k_hi + 1):
        t = k * step
        if prev_price < t <= price:
            crossed.append(t)
    return crossed
