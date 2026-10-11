"""Deterministic local search over order shares (stdlib only, no LP solver needed).

The search moves share between pairs of farms with shrinking step sizes, so every
intermediate plan respects capacity and sums to 1. Five farms keeps this cheap.
"""

from __future__ import annotations

from typing import Callable

from .market import Market, Plan

STEPS = (0.2, 0.1, 0.05, 0.02, 0.01, 0.005)


def _fill(m: Market, order: list[str]) -> Plan:
    plan = {fid: 0.0 for fid in m.available_ids()}
    left = 1.0
    for fid in order:
        take = min(left, m.farms[fid].capacity)
        plan[fid] += take
        left -= take
    return plan


def starting_points(m: Market) -> list[Plan]:
    ids = m.available_ids()
    starts = []
    # Proportional to capacity: a neutral start.
    cap = sum(m.farms[i].capacity for i in ids)
    starts.append({i: m.farms[i].capacity / cap for i in ids})
    # One greedy start per attribute extreme, so each stakeholder's ideal is reachable.
    for key in (
        lambda f: f.price,
        lambda f: -f.quality,
        lambda f: f.carbon,
        lambda f: f.delay_risk,
    ):
        starts.append(_fill(m, [f.id for f in sorted(m.discover(), key=key)]))
    return starts


def maximize(m: Market, objective: Callable[[Plan], float], starts: list[Plan] | None = None) -> Plan:
    if not m.is_supplyable():
        raise ValueError("available capacity cannot cover the order")
    ids = m.available_ids()
    best_plan, best_val = None, float("-inf")
    for start in starts or starting_points(m):
        plan = {i: start.get(i, 0.0) for i in ids}
        val = objective(plan)
        for step in STEPS:
            improved = True
            while improved:
                improved = False
                move, move_val = None, val
                for a in ids:
                    if plan[a] <= 1e-12:
                        continue
                    d = min(step, plan[a])
                    for b in ids:
                        if a == b:
                            continue
                        room = m.farms[b].capacity - plan[b]
                        dd = min(d, room)
                        if dd <= 1e-12:
                            continue
                        plan[a] -= dd
                        plan[b] += dd
                        v = objective(plan)
                        plan[a] += dd
                        plan[b] -= dd
                        if v > move_val + 1e-9:
                            move, move_val = (a, b, dd), v
                if move:
                    a, b, dd = move
                    plan[a] -= dd
                    plan[b] += dd
                    val = move_val
                    improved = True
        if val > best_val:
            best_plan, best_val = dict(plan), val
    return {k: round(v, 4) for k, v in best_plan.items()}
