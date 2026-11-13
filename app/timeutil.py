# -*- coding: utf-8 -*-
"""统一时间口径：积存金业务一律按北京时间（UTC+8，无夏令时）。

服务器/容器即使系统时区不对，业务判定也不会错位；
Linux 部署时仍建议同时设置 TZ=Asia/Shanghai 方便看日志。
"""
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))


def now_cn():
    """当前北京时间（带时区）。"""
    return datetime.now(CN_TZ)


def today_str():
    return now_cn().strftime("%Y-%m-%d")


def ts_text():
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")
