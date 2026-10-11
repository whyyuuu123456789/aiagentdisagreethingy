"""Decentralized deliberation protocol.

Each round, every agent:
  1. PROPOSES a plan and announces its current bar (how good the plan must be for it),
  2. VOTES on every proposal: APPROVE, REJECT (with what it needs), or VETO (revealing
     its private hard constraint),
  3. independently applies the same public selection rule to the shared log and
     broadcasts a COMMIT hash. Agreement means every agent derived the same plan.

There is no coordinator. `RoundClock` only advances time and injects world events,
the way a market calendar would. It never reads votes or chooses plans.

Stopping rules (each agent evaluates them locally over the same public log):
  * CONSENSUS: some proposal is approved by everyone -> commit it.
  * ESCALATE (infeasible): the revealed hard constraints cannot all hold at once. That is a
    values conflict only a human can trade off, so agents stop and report which
    constraints collide.
  * STALEMATE: every agent has hit its walk-away floor and still no plan clears all bars.
      - if a supermajority approves a constraint-respecting plan and the order is small
        enough to be reversible, PROCEED_WITH_DISSENT (record the minority objection);
      - otherwise ESCALATE to a human with the best options and who objects to each.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .agents import TOL, HardConstraint, StakeholderAgent, hard_violation
from .bus import TOPIC, InProcessBus, Message
from .market import SCORERS, Farm, Market, Plan, describe_plan
from .solver import maximize


@dataclass
class Event:
    round: int
    title: str
    farm_updates: dict[str, dict] = field(default_factory=dict)
    new_farms: list[Farm] = field(default_factory=list)
    briefings: dict[str, dict] = field(default_factory=dict)  # private, per agent


@dataclass
class Outcome:
    status: str  # CONSENSUS | PROCEED_WITH_DISSENT | ESCALATE
    reason: str
    plan: Plan | None
    rounds: int
    messages: list[Message]
    options: list[dict] = field(default_factory=list)
    dissent: list[str] = field(default_factory=list)
    conflicting_constraints: list[dict] = field(default_factory=list)

    @property
    def message_count(self) -> int:
        return len(self.messages)


def plan_hash(p: Plan) -> str:
    return hashlib.sha256(json.dumps({k: round(v, 3) for k, v in sorted(p.items())}).encode()).hexdigest()[:10]


def constraints_feasible(m: Market, constraints: list[HardConstraint]) -> tuple[bool, Plan]:
    if not m.is_supplyable():
        return False, {}
    p = maximize(m, lambda q: -hard_violation(m, q, constraints))
    return hard_violation(m, p, constraints) <= TOL * len(constraints), p


def minimal_conflict(m: Market, constraints: list[HardConstraint]) -> list[HardConstraint]:
    """Constraints whose removal alone would restore feasibility (the ones a human must weigh)."""
    culprits = [c for c in constraints if constraints_feasible(m, [d for d in constraints if d is not c])[0]]
    return culprits or constraints


class Deliberation:
    def __init__(
        self,
        market: Market,
        agents: list[StakeholderAgent],
        events: list[Event] | None = None,
        max_rounds: int = 25,
        supermajority: int = 3,
        auto_approve_limit_usd: float = 5000.0,
        narrator=None,
    ) -> None:
        self.m = market
        self.agents = agents
        self.by_id = {a.id: a for a in agents}
        self.events = {e.round: e for e in (events or [])}
        self.max_rounds = max_rounds
        self.supermajority = supermajority
        self.auto_limit = auto_approve_limit_usd
        self.narrator = narrator
        self.bus = InProcessBus()
        for a in agents:
            self.bus.subscribe(TOPIC, a.observe)
        self._feasibility_cache: dict = {}

    # -- helpers --
    def _say(self, r: int, sender: str, kind: str, payload: dict, text: str = "") -> Message:
        if self.narrator and kind in ("PROPOSAL", "VOTE") and text:
            text = self.narrator(sender, kind, payload, text)
        msg = Message(r, sender, kind, payload, text)
        self.bus.publish(TOPIC, msg)
        return msg

    def _public_constraints(self) -> list[HardConstraint]:
        # Every agent holds the same public set (it comes from broadcast vetoes/updates);
        # the union is simply the constraints that have been revealed on the bus.
        public: dict[str, HardConstraint] = {}
        for a in self.agents:
            public.update(a.known_constraints)
        return list(public.values())

    def _apply_event(self, r: int, ev: Event) -> None:
        for fid, changes in ev.farm_updates.items():
            self.m.update_farm(fid, **changes)
        for f in ev.new_farms:
            self.m.register(f)
        self._say(
            r,
            "world",
            "EVENT",
            {"title": ev.title, "farm_updates": ev.farm_updates, "new_farms": [f.id for f in ev.new_farms]},
            f"NEW INFORMATION: {ev.title}",
        )
        for a in self.agents:
            update = a.on_event(ev.briefings.get(a.id))
            if update:
                update.round = r
                self.bus.publish(TOPIC, update)

    def _local_decision(self, agent: StakeholderAgent, proposals, votes) -> Plan | None:
        """The selection rule every agent applies on its own to the public record."""
        # Use the bars as announced (rounded) so every agent computes identical margins.
        bars = {a.id: (round(a.bar(), 3) if a is agent else agent.others_bars.get(a.id, 0.0)) for a in self.agents}
        best, best_key = None, None
        for pid, plan in proposals.items():
            if all(votes[(voter, pid)]["vote"] == "APPROVE" for voter in self.by_id):
                margin = min(SCORERS[self.by_id[v].role](self.m, plan) - bars[v] for v in self.by_id)
                key = (round(margin, 6), pid)
                if best_key is None or key > best_key:
                    best, best_key = plan, key
        return best

    # -- main loop --
    def run(self) -> Outcome:
        floor_rounds = 0
        last_proposals: dict[str, Plan] = {}
        last_votes: dict = {}
        r = 0
        for r in range(self.max_rounds):
            if r in self.events:
                self._apply_event(r, self.events[r])
                floor_rounds = 0

            if not self.m.is_supplyable():
                return self._finish("ESCALATE", "Remaining farms cannot cover the order; buying must be re-scoped by a human.", None, r)

            # 1. proposals
            proposals: dict[str, Plan] = {}
            for a in self.agents:
                p = a.propose(self.m, self.by_id)
                proposals[a.id] = p
                self._say(r, a.id, "PROPOSAL", {"plan": p, "bar": round(a.bar(), 3), "at_floor": a.at_floor()},
                          a.explain_proposal(self.m, p))

            # 2. votes
            votes: dict = {}
            for voter in self.agents:
                for pid, p in proposals.items():
                    v = voter.vote(self.m, p)
                    votes[(voter.id, pid)] = v
                    text = {
                        "APPROVE": f"{voter.title} approves {pid}'s plan (score {v.get('score')}).",
                        "REJECT": f"{voter.title} rejects {pid}'s plan: score {v.get('score')} < needs {v.get('needs')}.",
                        "VETO": f"{voter.title} VETOES {pid}'s plan: {v.get('reason')}.",
                    }[v["vote"]]
                    self._say(r, voter.id, "VOTE", {"proposal": pid, **v}, text)

            # 3. each agent decides locally, then commits
            commits = {a.id: self._local_decision(a, proposals, votes) for a in self.agents}
            hashes = {aid: (plan_hash(p) if p else None) for aid, p in commits.items()}
            if all(h is not None for h in hashes.values()):
                for aid, h in hashes.items():
                    self._say(r, aid, "COMMIT", {"plan_hash": h}, f"{aid} commits to plan {h}.")
                if len(set(hashes.values())) == 1:
                    plan = next(iter(commits.values()))
                    return self._finish("CONSENSUS", "Every agent approved and committed to the same plan.", plan, r)

            # 4. stopping checks over public knowledge
            public = self._public_constraints()
            key = (self.m.version, tuple(sorted((c.owner, c.kind, c.limit) for c in public)))
            if key not in self._feasibility_cache:
                self._feasibility_cache[key] = constraints_feasible(self.m, public)[0]
            if not self._feasibility_cache[key]:
                culprits = minimal_conflict(self.m, public)
                out = self._finish(
                    "ESCALATE",
                    "Revealed hard constraints cannot all be satisfied; relaxing one is a value judgement for a human.",
                    None, r,
                )
                out.conflicting_constraints = [c.to_dict() | {"text": c.describe()} for c in culprits]
                out.options = self._options(proposals, votes)
                return out

            floor_rounds = floor_rounds + 1 if all(a.at_floor() for a in self.agents) else 0
            last_proposals, last_votes = proposals, votes
            if floor_rounds >= 2:
                return self._stalemate(r, proposals, votes)

            for a in self.agents:
                a.patience += 1

        return self._stalemate(r, last_proposals, last_votes, timed_out=True)

    def _options(self, proposals, votes) -> list[dict]:
        opts = []
        for pid, p in proposals.items():
            approvers = [v for v in self.by_id if votes[(v, pid)]["vote"] == "APPROVE"]
            objections = {v: votes[(v, pid)] for v in self.by_id if votes[(v, pid)]["vote"] != "APPROVE"}
            opts.append({"proposer": pid, "plan": p, "summary": describe_plan(self.m, p),
                         "approvers": approvers, "objections": objections})
        opts.sort(key=lambda o: (-len(o["approvers"]), o["proposer"]))
        return opts

    def _stalemate(self, r, proposals, votes, timed_out=False) -> Outcome:
        opts = self._options(proposals, votes)
        self._say(r, "all", "STALEMATE", {"timed_out": timed_out},
                  "Every agent is at its walk-away floor and no plan clears all bars.")
        viable = [o for o in opts if not any(x["vote"] == "VETO" for x in o["objections"].values())]
        order_value = None
        if viable:
            best = viable[0]
            order_value = self.m.avg(best["plan"], "price") * self.m.demand_lb
            if len(best["approvers"]) >= self.supermajority and order_value <= self.auto_limit:
                out = self._finish(
                    "PROCEED_WITH_DISSENT",
                    f"Supermajority ({len(best['approvers'])}/{len(self.agents)}) backs a plan that breaks no hard "
                    f"constraint, and the ${order_value:,.0f} order is under the ${self.auto_limit:,.0f} reversible limit.",
                    best["plan"], r,
                )
                out.dissent = [f"{v}: {o.get('score')} < needs {o.get('needs')}" for v, o in best["objections"].items()]
                out.options = opts
                return out
        reason = "No supermajority plan" if not viable or len(viable[0]["approvers"]) < self.supermajority else (
            f"${order_value:,.0f} order exceeds the ${self.auto_limit:,.0f} autonomous limit")
        out = self._finish("ESCALATE", f"Stalemate: {reason}. Disagreement is preserved for a human.", None, r)
        out.options = opts
        return out

    def _finish(self, status: str, reason: str, plan: Plan | None, r: int) -> Outcome:
        self._say(r, "all", "DECISION", {"status": status, "plan": plan}, reason)
        return Outcome(status, reason, plan, r + 1, self.bus.log)
