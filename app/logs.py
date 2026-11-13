# -*- coding: utf-8 -*-
"""崩溃日志与全局异常钩子。"""
import os
import sys
import threading
import traceback
from datetime import datetime

from .constants import ERROR_LOG_MAX, ERROR_PATH


def log_error(where, exc_info):
    """把异常追加写入 error.log；文件过大时滚动保留后半段，避免无限增长。"""
    try:
        tb = "".join(traceback.format_exception(*exc_info))
        block = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {where}\n{tb}\n"
        if os.path.exists(ERROR_PATH) and os.path.getsize(ERROR_PATH) > ERROR_LOG_MAX:
            try:
                with open(ERROR_PATH, "r", encoding="utf-8", errors="ignore") as f:
                    old = f.read()
                with open(ERROR_PATH, "w", encoding="utf-8") as f:
                    f.write(old[-ERROR_LOG_MAX // 2:])
            except OSError:
                pass
        with open(ERROR_PATH, "a", encoding="utf-8") as f:
            f.write(block)
    except Exception:
        pass  # 日志本身绝不能再抛出


def install_excepthook(on_fatal=None):
    """主线程 / 后台线程未捕获异常统一落盘；主线程致命错误可交 on_fatal 提示（界面层负责）。"""
    def on_main(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log_error("主线程未捕获异常", (exc_type, exc, tb))
        if on_fatal:
            try:
                on_fatal(exc_type, exc)
            except Exception:
                pass

    def on_thread(args):
        log_error(f"后台线程未捕获异常（{args.thread.name if args.thread else '?'}）",
                  (args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = on_main
    threading.excepthook = on_thread
