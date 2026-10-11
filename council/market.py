"""Coffee market model: farms, a capability directory, and the public scoring functions.

Scores are *public* formulas (any agent can compute any other agent's score for a plan).
What is *private* to each agent is how much it needs (its bar, its walk-away floor) and its
hard constraint. That split is what makes negotiation necessary.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

Plan = dict[str, float]  # farm_id -> share of the order (shares sum to 1.0)


@dataclass
class Farm:
    id: str
    price: float  # $/lb
    quality: float  # SCA cupping score
    carbon: float  # kg CO2e per lb delivered
    certified: bool  # Rainforest Alliance / Fair Trade
    delay_risk: float  # probability the shipment is late
    capacity: float  # max share of the order this farm can supply (0..1)
    capabilities: tuple[str, ...] = ("green_coffee",)


@dataclass
class Market:
    """The shared world state. Farms register in a directory; agents discover them by capability."""

    demand_lb: float = 1000.0
    farms: dict[str, Farm] = field(default_factory=dict)
    version: int = 0
    _bounds_cache: dict = field(default_factory=dict, repr=False)

    # ---- directory (AGNTCY-style capability discovery) ----
    def register(self, farm: Farm) -> None:
        self.farms[farm.id] = farm
        self.version += 1

    def discover(self, capability: str = "green_coffee") -> list[Farm]:
        return [
            f
            for f in self.farms.values()
            if capability in f.capabilities and f.capacity > 1e-9
        ]

    def update_farm(self, farm_id: str, **changes) -> None:
        for k, v in changes.items():
            setattr(self.farms[farm_id], k, v)
        self.version += 1

    def clone(self) -> "Market":
        m = copy.deepcopy(self)
        m._bounds_cache = {}
        return m

    # ---- plan helpers ----
    def available_ids(self) -> list[str]:
        return [f.id for f in self.discover()]

    def is_supplyable(self) -> bool:
        return sum(f.capacity for f in self.discover()) >= 1.0 - 1e-9

    def _greedy_extreme(self, key, reverse: bool) -> float:
        """Best/worst achievable average of a linear attribute, filling farms in order."""
        farms = sorted(self.discover(), key=key, reverse=reverse)
        left, total = 1.0, 0.0
        for f in farms:
            take = min(left, f.capacity)
            total += take * key(f)
            left -= take
        return total

    def bounds(self, name: str) -> tuple[float, float]:
        ck = (self.version, name)
        if ck not in self._bounds_cache:
            key = {
                "price": lambda f: f.price,
                "quality": lambda f: f.quality,
                "carbon": lambda f: f.carbon,
                "certified": lambda f: 1.0 if f.certified else 0.0,
                "ontime": lambda f: 1.0 - f.delay_risk,
            }[name]
            self._bounds_cache[ck] = (
                self._greedy_extreme(key, reverse=False),
                self._greedy_extreme(key, reverse=True),
            )
        return self._bounds_cache[ck]

    def avg(self, plan: Plan, attr: str) -> float:
        total = 0.0
        for fid, share in plan.items():
            f = self.farms[fid]
            v = {
                "price": f.price,
                "quality": f.quality,
                "carbon": f.carbon,
                "certified": 1.0 if f.certified else 0.0,
                "ontime": 1.0 - f.delay_risk,
            }[attr]
            total += share * v
        return total

    def _norm(self, plan: Plan, attr: str, higher_is_better: bool) -> float:
        lo, hi = self.bounds(attr)
        if hi - lo < 1e-12:
            return 1.0
        z = (self.avg(plan, attr) - lo) / (hi - lo)
        z = z if higher_is_better else 1.0 - z
        return min(1.0, max(0.0, z))


# ---- public objective functions, one per stakeholder ----


def cost_score(m: Market, p: Plan) -> float:
    return m._norm(p, "price", higher_is_better=False)


def quality_score(m: Market, p: Plan) -> float:
    return m._norm(p, "quality", higher_is_better=True)


def green_score(m: Market, p: Plan) -> float:
    return 0.5 * m._norm(p, "carbon", higher_is_better=False) + 0.5 * m._norm(
        p, "certified", higher_is_better=True
    )


def risk_score(m: Market, p: Plan) -> float:
    n = max(1, len(m.discover()))
    hhi = sum(s * s for s in p.values())
    min_hhi = max(1.0 / n, 1e-9)
    diversification = 1.0 if n == 1 else min(1.0, max(0.0, (1 - hhi) / (1 - min_hhi)))
    return 0.6 * m._norm(p, "ontime", higher_is_better=True) + 0.4 * diversification


SCORERS = {
    "cost": cost_score,
    "quality": quality_score,
    "green": green_score,
    "risk": risk_score,
}


def describe_plan(m: Market, p: Plan) -> str:
    parts = [f"{fid} {s * 100:.0f}%" for fid, s in sorted(p.items(), key=lambda kv: -kv[1]) if s >= 0.005]
    return (
        ", ".join(parts)
        + f"  | ${m.avg(p, 'price'):.2f}/lb, cup {m.avg(p, 'quality'):.1f}, "
        f"{m.avg(p, 'carbon'):.2f} kgCO2e/lb, {m.avg(p, 'certified') * 100:.0f}% certified, "
        f"max share {max(p.values()) * 100:.0f}%"
    )


def lungo_market() -> Market:
    """Lungo's three farms plus two more that can join mid-negotiation."""
    m = Market()
    m.register(Farm("brazil", 3.10, 82.0, 1.10, False, 0.08, 0.60))
    m.register(Farm("colombia", 4.20, 86.0, 0.90, True, 0.10, 0.40))
    m.register(Farm("vietnam", 2.40, 79.0, 1.40, False, 0.15, 0.60))
    m.register(Farm("honduras", 3.60, 84.0, 1.00, True, 0.12, 0.30))
    return m


ETHIOPIA = Farm("ethiopia", 4.60, 89.0, 0.80, True, 0.18, 0.30)
