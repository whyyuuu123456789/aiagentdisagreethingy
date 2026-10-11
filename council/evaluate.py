"""Randomized evaluation: python -m council.evaluate --trials 300 [--json results.json]

Each trial draws random farm attributes, private floors, hard constraints and concession
rates; a third of trials also inject a surprise event mid-negotiation. Outcomes are judged
against ground truth computed with full information (which no single agent has).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as st

from .agents import HardConstraint, StakeholderAgent
from .market import ETHIOPIA, Farm, Market, lungo_market
from .protocol import Event
from .run import run_scenario
from .scenarios import Scenario


def random_world(seed: int, listens: bool = True) -> Scenario:
    rng = random.Random(seed)

    def build():
        r = random.Random(seed)  # same draws every build
        base = lungo_market()
        m = Market()
        for f in base.farms.values():
            m.register(Farm(f.id, round(f.price * r.uniform(0.85, 1.15), 2), round(f.quality + r.uniform(-2, 2), 1),
                            round(f.carbon * r.uniform(0.85, 1.15), 2), f.certified,
                            round(min(0.5, f.delay_risk * r.uniform(0.7, 1.4)), 3), f.capacity))
        agents = [
            StakeholderAgent("cost", "cost", "Procurement (Cost)", r.uniform(0.15, 0.55), r.uniform(0.03, 0.10),
                             HardConstraint("cost", "max_price", round(r.uniform(3.2, 4.0), 2)), listens=listens),
            StakeholderAgent("quality", "quality", "Quality (Sommelier)", r.uniform(0.15, 0.55), r.uniform(0.03, 0.10),
                             HardConstraint("quality", "min_quality", round(r.uniform(81.5, 84.5), 1)), listens=listens),
            StakeholderAgent("green", "green", "Sustainability", r.uniform(0.15, 0.55), r.uniform(0.03, 0.10),
                             HardConstraint("green", "min_certified", round(r.uniform(0.2, 0.6), 2)), listens=listens),
            StakeholderAgent("risk", "risk", "Supply Risk", r.uniform(0.15, 0.55), r.uniform(0.03, 0.10),
                             HardConstraint("risk", "max_share", round(r.uniform(0.35, 0.6), 2)), listens=listens),
        ]
        return m, agents

    events = []
    if rng.random() < 1 / 3:
        hit = rng.choice(["brazil", "vietnam", "colombia"])
        events.append(Event(
            round=rng.randint(2, 4),
            title=f"Shipment failure at {hit}; Ethiopia joins the directory",
            farm_updates={hit: {"capacity": round(rng.uniform(0.0, 0.2), 2), "delay_risk": 0.35}},
            new_farms=[Farm(**{**ETHIOPIA.__dict__})],
            briefings={"risk": {"constraint_limit": 0.4, "announce": True}},
        ))
    return Scenario(f"trial-{seed}", "", build, events)


def summarize(results: list[dict]) -> dict:
    n = len(results)
    zopa = [r for r in results if r["zopa_exists"]]
    no_zopa = [r for r in results if not r["zopa_exists"]]
    infeasible = [r for r in results if not r["hard_feasible"]]
    out = {
        "trials": n,
        "with_event": sum(1 for r in results if "stale_plan" in r),
        "ground_truth": {"zopa": len(zopa), "no_zopa_but_feasible": len(no_zopa) - len(infeasible),
                         "hard_infeasible": len(infeasible)},
        "council_outcomes": {s: sum(1 for r in results if r["status"] == s)
                             for s in ("CONSENSUS", "PROCEED_WITH_DISSENT", "ESCALATE")},
        "consensus_when_zopa": round(sum(r["status"] == "CONSENSUS" for r in zopa) / max(1, len(zopa)), 3),
        "escalate_when_infeasible": round(sum(r["status"] == "ESCALATE" for r in infeasible) / max(1, len(infeasible)), 3),
        "mean_rounds": round(st.mean(r["rounds"] for r in results), 1),
        "mean_messages": round(st.mean(r["messages"] for r in results), 1),
        "methods": {},
    }
    for method in ("council", "weighted_sum", "majority_vote", "oracle"):
        cards = [r["comparison"][method] for r in results]
        decided = [c for c in cards if c.get("plan")]
        out["methods"][method] = {
            "decided": len(decided),
            "all_satisfied": sum(len(c["satisfied"]) == 4 for c in decided),
            "plans_violating_a_hard_constraint": sum(bool(c["violated_constraints"]) for c in decided),
            "mean_min_score": round(st.mean(c["min_score"] for c in decided), 3) if decided else None,
            "mean_satisfied_agents": round(st.mean(len(c["satisfied"]) for c in decided), 2) if decided else None,
        }
    events = [r for r in results if "stale_plan" in r and r["stale_plan"]["scorecard_after_event"].get("plan")]
    if events:
        out["event_trials"] = {
            "n": len(events),
            "stale_plan_mean_undeliverable": round(st.mean(r["stale_plan"]["scorecard_after_event"]["undeliverable_share"] for r in events), 3),
            "stale_plan_violating": sum(bool(r["stale_plan"]["scorecard_after_event"]["violated_constraints"]) for r in events),
            "council_violating": sum(bool(r["comparison"]["council"].get("violated_constraints")) for r in events),
            "council_undeliverable": round(st.mean(r["comparison"]["council"].get("undeliverable_share", 0) for r in events), 3),
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    seeds = [args.seed * 1000 + i for i in range(args.trials)]
    full = summarize([run_scenario(random_world(s)) for s in seeds])
    ablation = summarize([run_scenario(random_world(s, listens=False)) for s in seeds])
    report = {"council": full, "ablation_no_listening": {
        k: ablation[k] for k in ("council_outcomes", "consensus_when_zopa", "mean_rounds", "mean_messages")
    } | {"council_method": ablation["methods"]["council"]}}
    print(json.dumps(report, indent=2))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
