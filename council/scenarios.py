"""Repeatable demo scenarios. Each returns fresh market + agents (agents keep state while negotiating)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .agents import StakeholderAgent, lungo_council
from .market import ETHIOPIA, Market, lungo_market
from .protocol import Event


@dataclass
class Scenario:
    name: str
    story: str
    build: Callable[[], tuple[Market, list[StakeholderAgent]]]
    events: list[Event] = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)
    expect: str = ""


def _council(**overrides) -> Callable[[], tuple[Market, list[StakeholderAgent]]]:
    def build():
        m = lungo_market()
        agents = lungo_council()
        by_id = {a.id: a for a in agents}
        for aid, changes in overrides.items():
            for k, v in changes.items():
                if k == "constraint_limit":
                    by_id[aid].constraint.limit = v
                else:
                    setattr(by_id[aid], k, v)
        return m, agents

    return build


FROST = Event(
    round=3,
    title="Frost hits Minas Gerais: Brazil can now supply only 20% of the order, at $3.60/lb with 30% delay risk. "
    "Ethiopia registers in the directory with certified, high-grade capacity.",
    farm_updates={"brazil": {"capacity": 0.20, "price": 3.60, "delay_risk": 0.30}},
    new_farms=[ETHIOPIA],
    briefings={
        # Private reactions: Supply Risk now caps any one origin at 40% and wants more resilience.
        "risk": {"constraint_limit": 0.40, "floor": 0.50, "announce": True},
        # Procurement gets budget headroom from finance because of the shortage.
        "cost": {"constraint_limit": 3.95},
    },
)

SCENARIOS: dict[str, Scenario] = {
    "quick_deal": Scenario(
        "quick_deal",
        "Flexible season. Same limits as harvest, but every stakeholder lowers its bar by 0.35 a round, so a deal "
        "comes as soon as round 1's vetoes have revealed the hard limits.",
        _council(cost={"floor": 0.25, "concession": 0.35}, quality={"concession": 0.35},
                 green={"concession": 0.35}, risk={"concession": 0.35}),
        expect="CONSENSUS",
    ),
    "harvest": Scenario(
        "harvest",
        "Normal season. Four stakeholders open far apart, reveal hard limits through vetoes, and concede to a deal.",
        _council(cost={"floor": 0.25}),
        expect="CONSENSUS",
    ),
    "frost": Scenario(
        "frost",
        "Same start, but at round 3 a frost cuts Brazil supply and Ethiopia appears via discovery. "
        "Agents must drop the near-agreement they had and renegotiate.",
        _council(cost={"floor": 0.25}),
        events=[FROST],
        expect="CONSENSUS",
    ),
    "hard_line": Scenario(
        "hard_line",
        "Contentious season. Procurement's budget tightens to $3.50/lb, Sustainability demands at least half the "
        "order certified, Supply Risk caps any origin at 40%, and every stakeholder lowers its bar by only 0.02 a "
        "round. A deal all four accept still exists, but it takes a long negotiation to find.",
        _council(cost={"floor": 0.25, "concession": 0.02, "constraint_limit": 3.50}, quality={"concession": 0.02},
                 green={"concession": 0.02, "constraint_limit": 0.50},
                 risk={"concession": 0.02, "constraint_limit": 0.40}),
        kwargs={"max_rounds": 60},
        expect="CONSENSUS",
    ),
    "budget_clash": Scenario(
        "budget_clash",
        "Finance caps price at $3.25/lb while Quality insists on cup score 84. No plan can satisfy both: "
        "agents should detect it and ask a human instead of pretending to agree.",
        _council(cost={"constraint_limit": 3.25, "floor": 0.25}, quality={"constraint_limit": 84.0}),
        expect="ESCALATE",
    ),
    "stubborn_small": Scenario(
        "stubborn_small",
        "Procurement's walk-away floor is out of reach of everyone else's hard limits. For a small, reversible "
        "order the supermajority proceeds and records Procurement's dissent.",
        _council(),
        expect="PROCEED_WITH_DISSENT",
    ),
    "stubborn_large": Scenario(
        "stubborn_large",
        "Same disagreement, but on a 10,000 lb order (~$34k). Too big to override a dissenting stakeholder: escalate.",
        _council(),
        kwargs={"demand_lb": 10000.0},
        expect="ESCALATE",
    ),
}
