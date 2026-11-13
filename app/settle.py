# -*- coding: utf-8 -*-
"""收盘日结价定格状态机（与数据库解耦，便于假时钟单测）。

规则（沿用桌面版口径）：
- 22:20 起进入收盘观察，仍正常取价；
- 赎回价连续 SETTLE_STABLE_N 次完全相同 → 判定银行报价停更，锁定当日日结价；
- 价格仍在变动则计数清零；
- 到 22:35 硬截止仍未稳定 → 用最后一笔有效价强制锁定，保证当晚一定有日结价；
- 已有人工/历史价则不覆盖（save_day 返回 False）。
"""
from app.constants import (SETTLE_DEADLINE_MIN, SETTLE_STABLE_N,
                           SETTLE_WATCH_MIN)
from app.timeutil import now_cn


class SettleMachine:
    def __init__(self, load_day, save_day, now_fn=now_cn):
        self.load_day = load_day      # (day) -> {"price":..., "src":...} | None
        self.save_day = save_day      # (day, price) -> bool（已存在返回 False）
        self.now_fn = now_fn
        self.day = None
        self.last = None
        self.same = 0
        self.locked = False

    def reset(self):
        self.day = self.last = None
        self.same = 0
        self.locked = False

    def observe(self, sell):
        """喂入一次赎回价；锁定成功时返回 {"day","price"}，否则 None。"""
        now = self.now_fn()
        day = now.strftime("%Y-%m-%d")
        cur = now.hour * 60 + now.minute
        if self.day != day:
            self.day, self.last, self.same, self.locked = day, None, 0, False
        if self.locked or cur < SETTLE_WATCH_MIN:
            return None
        if self.load_day(day):
            # 已有精确价：无需再观察
            self.locked = True
            return None
        if sell:
            s = float(sell)
            if self.last is not None and s == self.last:
                self.same += 1
            else:
                self.last, self.same = s, 1
            if self.same >= SETTLE_STABLE_N:
                return self._lock(day, s)
        if cur >= SETTLE_DEADLINE_MIN:
            return self._lock(day, self.last or sell)
        return None

    def _lock(self, day, price):
        if not price:
            return None
        self.locked = True
        if self.save_day(day, float(price)):
            return {"day": day, "price": float(price)}
        return None
