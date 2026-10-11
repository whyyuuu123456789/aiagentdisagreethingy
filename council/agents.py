"""Autonomous stakeholder agents.

Each agent owns:
  * a public objective (its scorer in market.SCORERS),
  * a private walk-away floor and concession rate (how fast it lowers its bar),
  * a private hard constraint it will veto over, revealed only when it vetoes.

Agents learn about each other only from bus messages: the bars they announce with their
proposals and the constraints revealed by vetoes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bus import Message
from .market import SCORERS, Market, Plan, describe_plan
from .solver import maximize

PENALTY = 50.0


@dataclass
class HardConstraint:
    owner: str
    kind: str  # max_price | min_quality | min_certified | max_share
    limit: float

    def violation(self, m: Market, p: Plan) -> float:
        """Relative amount by which the plan breaks the constraint (0 = satisfied)."""
        if self.kind == "max_price":
            return max(0.0, m.avg(p, "price") - self.limit) / self.limit
        if self.kind == "min_quality":
            return max(0.0, self.limit - m.avg(p, "quality")) / 10.0
        if self.kind == "min_certified":
            return max(0.0, self.limit - m.avg(p, "certified"))
        if self.kind == "max_share":
            return max(0.0, max(p.values()) - self.limit)
        raise ValueError(self.kind)

    def describe(self) -> str:
        return {
            "max_price": f"average price must stay at or below ${self.limit:.2f}/lb",
            "min_quality": f"average cup score must be at least {self.limit:.1f}",
            "min_certified": f"at least {self.limit * 100:.0f}% of the order must be certified",
            "max_share": f"no single origin may exceed {self.limit * 100:.0f}% of the order",
        }[self.kind]

    def to_dict(self) -> dict:
        return {"owner": self.owner, "kind": self.kind, "limit": self.limit}


TOL = 2e-3  # tolerance for share rounding


def hard_violation(m: Market, p: Plan, constraints) -> float:
    return sum(c.violation(m, p) for c in constraints)


@dataclass
class StakeholderAgent:
    id: str
    role: str  # key into SCORERS
    title: str
    floor: float  # private walk-away score
    concession: float  # private: how much the bar drops per round
    constraint: HardConstraint
    # state learned from the bus
    listens: bool = True  # ablation switch: False = ignore the bars others announce
    patience: float = 0.0  # effective rounds conceded so far
    known_constraints: dict[str, HardConstraint] = field(default_factory=dict)
    others_bars: dict[str, float] = field(default_factory=dict)

    # ---- private reasoning ----
    def score(self, m: Market, p: Plan) -> float:
        return SCORERS[self.role](m, p)

    def bar(self) -> float:
        return max(self.floor, 1.0 - self.concession * self.patience)

    def at_floor(self) -> bool:
        return self.bar() <= self.floor + 1e-9

    def constraints_i_respect(self) -> list[HardConstraint]:
        cs = dict(self.known_constraints)
        cs[self.id] = self.constraint
        return list(cs.values())

    def propose(self, m: Market, agents_by_id: dict[str, "StakeholderAgent"]) -> Plan:
        """Best plan for me that also clears every bar and constraint others have announced."""
        constraints = self.constraints_i_respect()
        others = [(a, self.others_bars[a]) for a in self.others_bars] if self.listens else []
        # Early on an agent mostly argues for itself; the longer talks run, the more weight it
        # puts on closing the gaps others have announced. Hard constraints are never traded.
        # Normalised by the number of others so a crowd of demands doesn't swamp the agent's own voice.
        accommodation = (0.5 + 0.5 * self.patience) / max(1, len(others))

        def objective(p: Plan) -> float:
            shortfall = sum(
                max(0.0, bar - SCORERS[agents_by_id[a].role](m, p)) for a, bar in others
            )
            return self.score(m, p) - accommodation * shortfall - PENALTY * hard_violation(m, p, constraints)

        return maximize(m, objective)

    def vote(self, m: Market, p: Plan) -> dict:
        v = self.constraint.violation(m, p)
        if v > TOL:
            return {"vote": "VETO", "constraint": self.constraint.to_dict(), "reason": self.constraint.describe()}
        s = self.score(m, p)
        if s + 1e-9 >= self.bar():
            return {"vote": "APPROVE", "score": round(s, 3)}
        return {"vote": "REJECT", "score": round(s, 3), "needs": round(self.bar(), 3)}

    # ---- reacting to the public record ----
    def observe(self, msg: Message) -> None:
        if msg.sender == self.id:
            return
        if msg.kind == "PROPOSAL":
            self.others_bars[msg.sender] = msg.payload["bar"]
        elif msg.kind == "VOTE" and msg.payload["vote"] == "VETO":
            c = msg.payload["constraint"]
            self.known_constraints[c["owner"]] = HardConstraint(c["owner"], c["kind"], c["limit"])
        elif msg.kind == "CONSTRAINT_UPDATE":
            c = msg.payload["constraint"]
            self.known_constraints[c["owner"]] = HardConstraint(c["owner"], c["kind"], c["limit"])

    def on_event(self, briefing: dict | None) -> Message | None:
        """New information reopens negotiation: concede less ground, apply any private briefing."""
        self.patience = self.patience / 2
        if not briefing:
            return None
        if "floor" in briefing:
            self.floor = briefing["floor"]
        if "constraint_limit" in briefing:
            self.constraint.limit = briefing["constraint_limit"]
            # If others already rely on my old constraint, tell them it changed.
            if self.id in self.known_constraints or briefing.get("announce", False):
                return Message(
                    -1,
                    self.id,
                    "CONSTRAINT_UPDATE",
                    {"constraint": self.constraint.to_dict()},
                    f"Update: {self.constraint.describe()}.",
                )
        return None

    def explain_proposal(self, m: Market, p: Plan) -> str:
        s = self.score(m, p)
        bits = [f"{self.title} proposes {describe_plan(m, p)}.", f"My score {s:.2f}, my bar now {self.bar():.2f}."]
        if self.others_bars:
            bits.append(
                "Accommodating announced bars: "
                + ", ".join(f"{a} {b:.2f}" for a, b in sorted(self.others_bars.items()))
                + "."
            )
        if self.known_constraints:
            bits.append(f"Respecting {len(self.known_constraints)} revealed hard constraint(s).")
        return " ".join(bits)


def lungo_council() -> list[StakeholderAgent]:
    return [
        StakeholderAgent("cost", "cost", "Procurement (Cost)", floor=0.45, concession=0.06,
                         constraint=HardConstraint("cost", "max_price", 3.70)),
        StakeholderAgent("quality", "quality", "Quality (Sommelier)", floor=0.40, concession=0.05,
                         constraint=HardConstraint("quality", "min_quality", 83.0)),
        StakeholderAgent("green", "green", "Sustainability", floor=0.45, concession=0.07,
                         constraint=HardConstraint("green", "min_certified", 0.35)),
        StakeholderAgent("risk", "risk", "Supply Risk", floor=0.50, concession=0.05,
                         constraint=HardConstraint("risk", "max_share", 0.50)),
    ]
