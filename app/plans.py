# -*- coding: utf-8 -*-
"""补仓计划命中判定（纯函数）。"""


def init_fired(plans, base):
    """首次建基线：计划价高于基准价的先标记已触发，等重新站上去再武装。"""
    return {round(float(p), 2): float(p) >= float(base) for p in plans if float(p) > 0}


def detect_plan_hits(prev, buy, plans, fired):
    """严格下穿目标价（prev >= target > buy）时命中一次；价格回到目标价上方后重新武装。
    plans 为 [(target, grams), ...]，fired 会被原地更新。返回 [(target, grams), ...]。"""
    hits = []
    for target, grams in plans:
        was = fired.get(target, False)
        if not was and prev is not None and prev >= target > buy:
            hits.append((target, grams))
            fired[target] = True
        elif was and buy > target:
            fired[target] = False
    return hits
