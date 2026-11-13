# -*- coding: utf-8 -*-
"""本地持久化：配置、日结价、本地分时行情。"""
import json
import os

from .constants import (
    CONFIG_PATH,
    DEFAULT_CONFIG,
    HISTORY_KEEP_DAYS,
    HISTORY_MAX_POINTS,
    HISTORY_PATH,
    SETTLE_PATH,
)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def load_history():
    """读取本地分时数据：{交易日: [["HH:MM", 价格], ...]}，按交易日升序。"""
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {d: [[t, float(p)] for t, p in pts]
                for d, pts in sorted(data.items()) if isinstance(pts, list)}
    except (OSError, ValueError, TypeError):
        return {}


def save_history(hist):
    """按日期裁剪保留最近 N 个交易日后落盘。"""
    trimmed = dict(sorted(hist.items())[-HISTORY_KEEP_DAYS:])
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, ensure_ascii=False)
    except OSError:
        pass


def load_settles():
    """读取日结价：{"YYYY-MM-DD": {"price": 价, "src": icbc|alipay|est}, ...}。
    兼容旧版仅存裸数值的写法；按日期升序返回。src=icbc 为程序22:20自动锁定，
    alipay 为按支付宝截图反推的人工精确价，est 为日K近似。"""
    out = {}
    try:
        with open(SETTLE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for d, v in data.items():
            try:
                if isinstance(v, dict):
                    out[d] = {"price": float(v["price"]), "src": str(v.get("src") or "")}
                else:
                    out[d] = {"price": float(v), "src": ""}
            except (TypeError, ValueError, KeyError):
                continue
    except (OSError, ValueError):
        pass
    return dict(sorted(out.items()))


def save_settles(settles):
    try:
        with open(SETTLE_PATH, "w", encoding="utf-8") as f:
            json.dump(settles, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def append_history_point(hist, day, hm, price):
    """追加一个分时点：同分钟去重（保留最新价），同日限长，返回是否发生变更。"""
    if not day or not price:
        return False
    pts = hist.setdefault(day, [])
    if pts and pts[-1][0] == hm:
        if pts[-1][1] != price:
            pts[-1][1] = price
            return True
        return False
    pts.append([hm, float(price)])
    if len(pts) > HISTORY_MAX_POINTS:
        del pts[:len(pts) - HISTORY_MAX_POINTS]
    return True
