"""Simpler decision procedures to compare the council against, plus a common scorecard."""

from __future__ import annotations

from .agents import PENALTY, TOL, StakeholderAgent, hard_violation
from .market import SCORERS, Market, Plan
from .solver import maximize


def weighted_sum(m: Market, agents: list[StakeholderAgent]) -> Plan:
    """Central optimizer with equal weights. Sees only the public objectives, not private constraints."""
    return maximize(m, lambda p: sum(SCORERS[a.role](m, p) for a in agents) / len(agents))


def majority_vote(m: Market, agents: list[StakeholderAgent]) -> Plan:
    """One-shot: each agent submits its selfish best plan, everyone votes once, most approvals wins."""
    opening = {a.id: maximize(m, lambda p, a=a: a.score(m, p) - PENALTY * a.constraint.violation(m, p)) for a in agents}

    def approvals(p: Plan) -> int:
        return sum(1 for a in agents if a.constraint.violation(m, p) <= TOL and a.score(m, p) >= a.floor)

    return max(opening.values(), key=lambda p: (approvals(p), sum(a.score(m, p) for a in agents)))


def oracle(m: Market, agents: list[StakeholderAgent]) -> Plan:
    """Full-information central planner: knows every private floor and constraint. An upper bound, not a peer."""
    constraints = [a.constraint for a in agents]
    return maximize(
        m,
        lambda p: min(a.score(m, p) - a.floor for a in agents) - PENALTY * hard_violation(m, p, constraints),
    )


def scorecard(m: Market, agents: list[StakeholderAgent], plan: Plan | None) -> dict:
    """Judge a plan against everyone's *true* private preferences on the current market."""
    if plan is None:
        return {"plan": None}
    undeliverable = sum(max(0.0, s - (m.farms[f].capacity if f in m.farms else 0.0)) for f, s in plan.items())
    raw = {a.id: a.score(m, plan) for a in agents}
    scores = {k: round(v, 3) for k, v in raw.items()}
    violated = [a.id for a in agents if a.constraint.violation(m, plan) > TOL]
    satisfied = [a.id for a in agents if a.id not in violated and raw[a.id] >= a.floor - 1e-9]
    return {
        "plan": plan,
        "scores": scores,
        "min_score": min(scores.values()),
        "mean_score": round(sum(scores.values()) / len(scores), 3),
        "violated_constraints": violated,
        "satisfied": satisfied,
        "undeliverable_share": round(undeliverable, 3),
    }


def zopa_exists(m: Market, agents: list[StakeholderAgent]) -> bool:
    """Is there a plan every agent would truly accept (all constraints met, all floors cleared)?"""
    card = scorecard(m, agents, oracle(m, agents))
    return len(card["satisfied"]) == len(agents)


def hard_feasible(m: Market, agents: list[StakeholderAgent]) -> bool:
    cs = [a.constraint for a in agents]
    p = maximize(m, lambda q: -hard_violation(m, q, cs))
    return hard_violation(m, p, cs) <= TOL * len(cs)
