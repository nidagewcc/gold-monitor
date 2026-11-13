# -*- coding: utf-8 -*-
"""SQLite 访问层：连接、建表、通用查询封装。

单进程 + WAL + 全局锁，个人/小团队量级足够。
"""
import os
import sqlite3
import threading

from app.timeutil import ts_text

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DB_PATH") or os.path.join(BASE_DIR, "data", "gold.db")

_conn = None
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    pwd_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invites(
    code TEXT PRIMARY KEY,
    created_by INTEGER,
    used_by INTEGER,
    created_at TEXT NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS settings(
    user_id INTEGER PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'jicun_active',
    step REAL NOT NULL DEFAULT 10,
    interval INTEGER NOT NULL DEFAULT 10,
    alert_up INTEGER NOT NULL DEFAULT 1,
    privacy INTEGER NOT NULL DEFAULT 0,
    theme TEXT NOT NULL DEFAULT 'light',
    wx_enabled INTEGER NOT NULL DEFAULT 0,
    wx_appid TEXT NOT NULL DEFAULT '',
    wx_secret TEXT NOT NULL DEFAULT '',
    wx_openid TEXT NOT NULL DEFAULT '',
    wx_template_id TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS positions(
    user_id INTEGER PRIMARY KEY,
    grams REAL NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS trades(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    ts TEXT,
    grams REAL NOT NULL,
    amount REAL NOT NULL,
    price REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'buy',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id, date);
CREATE TABLE IF NOT EXISTS plans(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    price REAL NOT NULL,
    grams REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_plans_user ON plans(user_id);
CREATE TABLE IF NOT EXISTS settles(
    day TEXT PRIMARY KEY,
    price REAL NOT NULL,
    src TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS history(
    day TEXT NOT NULL,
    hm TEXT NOT NULL,
    price REAL NOT NULL,
    PRIMARY KEY(day, hm)
);
CREATE TABLE IF NOT EXISTS alerts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    kind TEXT NOT NULL,
    ts TEXT NOT NULL,
    price REAL,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id, id);
"""


def get_conn():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        _conn = c
    return _conn


def init_db():
    with _lock:
        c = get_conn()
        c.executescript(SCHEMA)
        # 增量迁移：为旧库补齐 trades.ts 列（可选的精确时间戳）
        cols = {row[1] for row in c.execute("PRAGMA table_info(trades)").fetchall()}
        if "ts" not in cols:
            c.execute("ALTER TABLE trades ADD COLUMN ts TEXT")
            c.commit()
        # 增量迁移：记录成交当时的持仓均价（用于减仓已实现盈亏）
        if "cost" not in cols:
            c.execute("ALTER TABLE trades ADD COLUMN cost REAL")
            c.commit()


def fetch_all(sql, args=()):
    with _lock:
        return [dict(r) for r in get_conn().execute(sql, args).fetchall()]


def fetch_one(sql, args=()):
    with _lock:
        r = get_conn().execute(sql, args).fetchone()
        return dict(r) if r else None


def execute(sql, args=()):
    """执行写操作，返回 lastrowid。"""
    with _lock:
        c = get_conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def execute_rowcount(sql, args=()):
    """执行写操作，返回受影响行数（用于鉴权式删除/更新）。"""
    with _lock:
        c = get_conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.rowcount


def executemany(sql, rows):
    with _lock:
        c = get_conn()
        c.executemany(sql, rows)
        c.commit()


def add_alert(user_id, kind, price, payload=""):
    execute(
        "INSERT INTO alerts(user_id, kind, ts, price, payload) VALUES(?,?,?,?,?)",
        (user_id, kind, ts_text(), price, payload),
    )
