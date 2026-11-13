# -*- coding: utf-8 -*-
"""把旧桌面版的本地 JSON 数据导入 SQLite（B/S 版）。

用法：
    python scripts/import_legacy.py --user alice --dir . --dry-run
    python scripts/import_legacy.py --user alice --dir . 

幂等：交易按 (日期, 克数, 金额, 方向) 去重；补仓计划按目标价去重；
日结价冲突时遵循 manual（人工）> icbc（引擎）> est（估算），旧人工价优先。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.timeutil import ts_text  # noqa: E402
from server import db  # noqa: E402


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def src_rank(src):
    return {"manual": 3, "alipay": 3, "icbc": 2, "": 1, "est": 0}.get(src or "", 1)


def main():
    ap = argparse.ArgumentParser(description="导入旧版积存金监控数据")
    ap.add_argument("--user", required=True, help="目标用户名（需先注册）")
    ap.add_argument("--dir", default=ROOT, help="旧数据目录（含 config.json 等）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args()

    db.init_db()
    u = db.fetch_one("SELECT id FROM users WHERE username=?", (args.user,))
    if not u:
        print(f"用户 {args.user} 不存在，请先在网页注册后再导入。")
        return 1
    uid = u["id"]

    d = os.path.abspath(args.dir)
    cfg = read_json(os.path.join(d, "config.json"), {})
    settles = read_json(os.path.join(d, "settle_prices.json"), {})
    hist = read_json(os.path.join(d, "price_history.json"), {})
    if not cfg:
        print(f"未在 {d} 找到可用的 config.json")

    action = "将写入" if not args.dry_run else "试运行"
    print(f"== {action}（用户 {args.user}，目录 {d}）==")

    # 1) 设置与持仓
    if cfg:
        keys = ("source", "step", "interval", "alert_up", "privacy", "theme",
                "wx_enabled", "wx_appid", "wx_secret", "wx_openid", "wx_template_id")
        vals = [cfg.get(k) for k in keys]
        print(f"设置：品种={cfg.get('source')} 档位={cfg.get('step')} 间隔={cfg.get('interval')} "
              f"微信={'开' if cfg.get('wx_enabled') else '关'}")
        print(f"持仓：{float(cfg.get('hold_grams') or 0):.4f} 克，成本 "
              f"{float(cfg.get('hold_cost') or 0):.2f} 元")
        if not args.dry_run:
            db.execute(
                "INSERT INTO settings(user_id, source, step, interval, alert_up, privacy, theme,"
                " wx_enabled, wx_appid, wx_secret, wx_openid, wx_template_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                " source=excluded.source, step=excluded.step, interval=excluded.interval,"
                " alert_up=excluded.alert_up, privacy=excluded.privacy, theme=excluded.theme,"
                " wx_enabled=excluded.wx_enabled, wx_appid=excluded.wx_appid,"
                " wx_secret=excluded.wx_secret, wx_openid=excluded.wx_openid,"
                " wx_template_id=excluded.wx_template_id",
                (uid, cfg.get("source") or "jicun_active", float(cfg.get("step") or 10),
                 int(cfg.get("interval") or 10), int(bool(cfg.get("alert_up", True))),
                 int(bool(cfg.get("privacy"))), cfg.get("theme") or "light",
                 int(bool(cfg.get("wx_enabled"))), cfg.get("wx_appid") or "",
                 cfg.get("wx_secret") or "", cfg.get("wx_openid") or "",
                 cfg.get("wx_template_id") or ""),
            )
            db.execute(
                "INSERT INTO positions(user_id, grams, cost) VALUES(?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET grams=excluded.grams, cost=excluded.cost",
                (uid, float(cfg.get("hold_grams") or 0), float(cfg.get("hold_cost") or 0)),
            )

    # 2) 交易（旧字段：dt / side / amount / grams）
    trades = cfg.get("trades") or []
    added = skipped = 0
    rows = []
    for t in trades:
        try:
            g = float(t.get("grams") or 0)
            a = float(t.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if g <= 0 or a <= 0:
            continue
        day = str(t.get("dt") or t.get("date") or "")[:10]
        ts_full = str(t.get("dt") or t.get("date") or "").strip()
        if len(day) != 10:
            continue
        ts_val = ""
        if len(ts_full) >= 16:
            # 例如 "2026-11-12 15:32" 或 "2026-11-12T15:32:00"
            ts_val = ts_full.replace("T", " ")
            if len(ts_val) == 16:
                ts_val = ts_val + ":00"
        kind = "sell" if str(t.get("side") or t.get("kind") or "buy").lower() in ("sell", "卖出") else "buy"
        if db.fetch_one(
                "SELECT id FROM trades WHERE user_id=? AND date=? AND kind=?"
                " AND ABS(grams-?)<1e-9 AND ABS(amount-?)<0.01",
                (uid, day, kind, g, a)):
            skipped += 1
            continue
        rows.append((uid, day, ts_val or None, g, a, a / g, kind, str(t.get("note") or ""), ts_text()))
    print(f"交易：新增 {len(rows)} 条，已存在 {skipped} 条")
    if rows and not args.dry_run:
        db.executemany(
            "INSERT INTO trades(user_id, date, ts, grams, amount, price, kind, note, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)", rows)

    # 3) 补仓计划
    plans = cfg.get("plans") or []
    plan_rows = []
    for p in plans:
        try:
            price = float(p.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        if db.fetch_one("SELECT id FROM plans WHERE user_id=? AND ABS(price-?)<1e-9", (uid, price)):
            continue
        plan_rows.append((uid, price, float(p.get("grams") or 0), 1, ts_text()))
    print(f"补仓计划：新增 {len(plan_rows)} 条")
    if plan_rows and not args.dry_run:
        db.executemany(
            "INSERT INTO plans(user_id, price, grams, active, created_at) VALUES(?,?,?,?,?)",
            plan_rows)

    # 4) 日结价（全局共享，人工优先）
    ins = upd = keep = 0
    settle_rows = []
    for day, v in (settles or {}).items():
        try:
            price = float(v["price"] if isinstance(v, dict) else v)
        except (TypeError, ValueError, KeyError):
            continue
        if price <= 0:
            continue
        src = str(v.get("src") or "") if isinstance(v, dict) else ""
        old = db.fetch_one("SELECT price, src FROM settles WHERE day=?", (day,))
        new_src = "manual" if src in ("alipay", "manual") else (src or "icbc")
        if not old:
            ins += 1
            settle_rows.append((day, price, new_src))
        elif src_rank(new_src) > src_rank(old["src"]):
            upd += 1
            settle_rows.append((day, price, new_src))
        else:
            keep += 1
    print(f"日结价：新增 {ins} 条，覆盖 {upd} 条，保留现有 {keep} 条")
    if settle_rows and not args.dry_run:
        db.executemany(
            "INSERT INTO settles(day, price, src) VALUES(?,?,?)"
            " ON CONFLICT(day) DO UPDATE SET price=excluded.price, src=excluded.src",
            settle_rows)

    # 5) 本地分时
    hist_rows = []
    for day, pts in (hist or {}).items():
        if not isinstance(pts, list):
            continue
        for item in pts:
            try:
                hm, price = item[0], float(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            hist_rows.append((str(day), str(hm), price))
    print(f"本地分时：{len(hist_rows)} 个点（{len(hist or {})} 个交易日）")
    if hist_rows and not args.dry_run:
        db.executemany(
            "INSERT OR REPLACE INTO history(day, hm, price) VALUES(?,?,?)", hist_rows)

    print("== 完成 ==" if not args.dry_run else "== 试运行结束，未写入任何数据 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
