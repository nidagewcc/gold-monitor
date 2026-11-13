# -*- coding: utf-8 -*-
"""全局行情引擎：单实例轮询 → 入库 → SSE 广播 → 按用户评估提醒 / 微信推送。"""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from app import icbc, tiers
from app.constants import (SETTLE_DEADLINE_MIN, SETTLE_WATCH_MIN, SOURCES,
                           TRADE_CLOSE_MIN, TRADE_OPEN_MIN)
from app.plans import detect_plan_hits, init_fired
from app.settle import SettleMachine
from app.timeutil import now_cn, today_str
from app.wechat import WeChatPusher

from . import db


class Engine:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None
        self.loop = None
        self.lock = threading.RLock()
        self.subs = {}            # sub_id -> (user_id, asyncio.Queue)
        self._next_sub = 1
        self.latest = None        # 最近一次行情快照
        self.failed = 0
        self.users = {}           # user_id -> 运行态
        self.interval = 10
        self.market_state = {"open": False, "paused": True, "settle_locked": False}
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.pushers = {}
        self.settle = SettleMachine(self._load_settle, self._save_settle)
        self._wake = threading.Event()   # kick() 提前唤醒等待

    # ---------- 生命周期 ----------
    def start(self, loop):
        self.loop = loop
        self.stop_event.clear()
        self._wake.clear()
        self.refresh_users()
        self.thread = threading.Thread(target=self._run, name="market-engine", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self._wake.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.executor.shutdown(wait=False)

    def kick(self):
        """手动立即刷新一次（提前结束当前等待，不等满轮询间隔）。"""
        self._wake.set()

    def _wait(self, seconds):
        """可被 kick()/stop() 提前打断的等待。"""
        self._wake.wait(timeout=seconds)
        self._wake.clear()

    # ---------- 订阅 / 广播 ----------
    def subscribe(self, user_id):
        q = asyncio.Queue(maxsize=100)
        with self.lock:
            sid = self._next_sub
            self._next_sub += 1
            self.subs[sid] = (user_id, q)
        return sid, q

    def unsubscribe(self, sid):
        with self.lock:
            self.subs.pop(sid, None)

    def broadcast(self, event, user_id=None):
        with self.lock:
            targets = [(uid, q) for uid, q in self.subs.values()
                       if user_id is None or uid == user_id]
        for _, q in targets:
            try:
                self.loop.call_soon_threadsafe(self._offer, q, event)
            except RuntimeError:
                pass

    @staticmethod
    def _offer(q, event):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # ---------- 用户缓存 ----------
    def refresh_users(self):
        rows = db.fetch_all(
            "SELECT u.id AS uid, s.source, s.step, s.interval, s.alert_up, "
            "s.wx_enabled, s.wx_appid, s.wx_secret, s.wx_openid, s.wx_template_id "
            "FROM users u LEFT JOIN settings s ON s.user_id=u.id")
        plan_rows = db.fetch_all("SELECT user_id, price, grams FROM plans WHERE active=1")
        plan_map = {}
        for p in plan_rows:
            plan_map.setdefault(p["user_id"], []).append(
                (round(float(p["price"]), 2), float(p["grams"])))

        new = {}
        with self.lock:
            for r in rows:
                uid = r["uid"]
                old = self.users.get(uid)
                plans = sorted(plan_map.get(uid, []))
                sig = tuple(plans)
                prev_sig = old.get("plans_sig") if old else None
                new[uid] = {
                    "source": r["source"] or "jicun_active",
                    "step": float(r["step"] or 10),
                    "interval": int(r["interval"] or 10),
                    "alert_up": bool(r["alert_up"]) if r["alert_up"] is not None else True,
                    "wx_enabled": bool(r["wx_enabled"]),
                    "wx_appid": r["wx_appid"] or "",
                    "wx_secret": r["wx_secret"] or "",
                    "wx_openid": r["wx_openid"] or "",
                    "wx_template_id": r["wx_template_id"] or "",
                    "plans": plans,
                    "plans_sig": sig,
                    "last": old.get("last") if old else None,
                    "plans_fired": old.get("plans_fired") if old and prev_sig == sig else None,
                }
            self.users = new
            intervals = [u["interval"] for u in new.values() if u["interval"]]
            self.interval = max(3, min(intervals) if intervals else 10)

    # ---------- 主循环 ----------
    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.refresh_users()
                if not self._in_poll_window():
                    self._set_market_state()
                    self._wait(60)
                    continue
                data = icbc.fetch_prices()
                self.failed = 0
                self._on_tick(data)
            except Exception as e:
                self.failed += 1
                try:
                    from app.logs import log_error
                    log_error("行情引擎取价失败", (type(e), e, e.__traceback__))
                except Exception:
                    pass
                self.broadcast({"type": "error", "message": str(e),
                                "fail_count": self.failed})
                self._set_market_state()
            self._wait(self.interval)

    def _in_poll_window(self):
        now = now_cn()
        cur = now.hour * 60 + now.minute
        day = now.strftime("%Y-%m-%d")
        if TRADE_OPEN_MIN <= cur < TRADE_CLOSE_MIN:
            return True
        # 收盘观察期（未锁定前继续取价，才能做连续同价判定）
        if SETTLE_WATCH_MIN <= cur < SETTLE_DEADLINE_MIN and not self._load_settle(day):
            return True
        return False

    def _on_tick(self, data):
        ts = data.get("time") or now_cn().strftime("%H:%M:%S")
        day = data.get("date") or today_str()
        hm = ts[:5]
        sell = data.get("jicun_sell")

        # 本地分时入库（仅交易时段，按分钟幂等）
        if sell and day and tiers.in_trade_session(hm):
            db.execute("INSERT OR REPLACE INTO history(day, hm, price) VALUES(?,?,?)",
                       (day, hm, float(sell)))

        # 日结价定格
        locked = self.settle.observe(sell)
        if locked:
            self.broadcast({"type": "settle_locked", **locked})

        self._set_market_state()

        snap = {
            "type": "tick",
            "time": ts,
            "date": day,
            "prices": {k: data.get(k) for k in SOURCES},
            "high": data.get("high"),
            "low": data.get("low"),
            "market": dict(self.market_state),
        }
        with self.lock:
            self.latest = snap
        self.broadcast(snap)

        for uid, st in list(self.users.items()):
            try:
                self._evaluate_user(uid, st, data, ts)
            except Exception:
                pass

    def _set_market_state(self):
        now = now_cn()
        locked = self.settle.locked or bool(self._load_settle(now.strftime("%Y-%m-%d")))
        state = {
            "open": tiers.is_trade_hours(now),
            "paused": not self._in_poll_window(),
            "settle_locked": locked,
        }
        if state != self.market_state:
            self.market_state = state
            self.broadcast({"type": "market_state", **state})

    # ---------- 每用户提醒 ----------
    def _evaluate_user(self, uid, st, data, ts):
        price = data.get(st["source"])
        if price is None:
            return
        price = float(price)
        buy = data.get("jicun_active") or price
        if st["last"] is None:
            st["last"] = price
            if st["plans_fired"] is None:
                st["plans_fired"] = init_fired([p for p, _ in st["plans"]], buy)
            return

        prev = st["last"]
        step = st["step"] if st["step"] > 0 else 10
        for tier in tiers.crossed_tiers(prev, price, step):
            self._fire_tier(uid, st, price, tier, down=True, ts=ts)
        if st["alert_up"]:
            for tier in tiers.crossed_tiers_up(prev, price, step):
                self._fire_tier(uid, st, price, tier, down=False, ts=ts)

        if st["plans_fired"] is None:
            st["plans_fired"] = init_fired([p for p, _ in st["plans"]], buy)
        for target, grams in detect_plan_hits(prev, buy, st["plans"], st["plans_fired"]):
            self._fire_plan(uid, st, target, grams, buy, ts)

        st["last"] = price

    def _fire_tier(self, uid, st, price, tier, down, ts):
        title = f"金价{'跌破' if down else '涨破'} {tier:g} 元"
        message = (f"[{ts}] 当前 {price:.2f} 元/克\n"
                   f"{'向下' if down else '向上'}穿越 {tier:g} 元档位")
        ev = {"type": "alert", "kind": "down" if down else "up",
              "title": title, "message": message, "price": price, "tier": tier}
        self.broadcast(ev, user_id=uid)
        db.add_alert(uid, ev["kind"], price, json.dumps(ev, ensure_ascii=False))
        self._push_wx(uid, st, f"{title}\n当前 {price:.2f} 元/克（{ts}）")

    def _fire_plan(self, uid, st, target, grams, buy, ts):
        if grams > 0:
            g_txt = f"按计划补 {grams:g} 克，约 {target * grams:,.0f} 元"
        else:
            g_txt = "到计划目标价了，可以看看补多少"
        title = f"补仓计划到价 {target:g} 元"
        message = f"[{ts}] 主动买入价已下穿计划价\n当前买入 {buy:.2f} 元/克\n{g_txt}"
        ev = {"type": "plan", "title": title, "message": message,
              "price": buy, "target": target, "grams": grams}
        self.broadcast(ev, user_id=uid)
        db.add_alert(uid, "plan", buy, json.dumps(ev, ensure_ascii=False))
        self._push_wx(uid, st, f"{title}\n当前买入 {buy:.2f} 元/克（{ts}）")

    def _push_wx(self, uid, st, text):
        if not st["wx_enabled"]:
            return
        cfg = {
            "wx_appid": st["wx_appid"], "wx_secret": st["wx_secret"],
            "wx_openid": st["wx_openid"], "wx_template_id": st["wx_template_id"],
        }
        with self.lock:
            pusher = self.pushers.setdefault(uid, WeChatPusher())
        self.executor.submit(pusher.send, cfg, text)

    # ---------- 日结价读写 ----------
    @staticmethod
    def _load_settle(day):
        return db.fetch_one("SELECT day, price, src FROM settles WHERE day=?", (day,))

    @staticmethod
    def _save_settle(day, price):
        if db.fetch_one("SELECT day FROM settles WHERE day=?", (day,)):
            return False
        db.execute("INSERT INTO settles(day, price, src) VALUES(?,?, 'icbc')",
                   (day, float(price)))
        return True


engine = Engine()
