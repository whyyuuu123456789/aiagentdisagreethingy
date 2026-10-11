"""Run a scenario: python -m council.run frost [--verbose] [--llm] [--json out.json]"""

from __future__ import annotations

import argparse
import json
import sys

from . import baselines
from .market import describe_plan
from .protocol import Deliberation
from .scenarios import SCENARIOS, Scenario


def run_scenario(sc: Scenario, narrator=None) -> dict:
    m, agents = sc.build()
    kwargs = dict(sc.kwargs)
    if "demand_lb" in kwargs:
        m.demand_lb = kwargs.pop("demand_lb")
    outcome = Deliberation(m, agents, events=list(sc.events), narrator=narrator, **kwargs).run()

    # Baselines judged on the same final market, against everyone's true private preferences.
    fresh_m, fresh_agents = sc.build()
    for ev in sc.events:  # bring the baseline world and preferences to the post-event state
        for fid, ch in ev.farm_updates.items():
            fresh_m.update_farm(fid, **ch)
        for f in ev.new_farms:
            fresh_m.register(f)
        for a in fresh_agents:
            b = ev.briefings.get(a.id, {})
            a.floor = b.get("floor", a.floor)
            if "constraint_limit" in b:
                a.constraint.limit = b["constraint_limit"]
    fresh_m.demand_lb = m.demand_lb

    result = {
        "scenario": sc.name,
        "story": sc.story,
        "expected": sc.expect,
        "status": outcome.status,
        "reason": outcome.reason,
        "rounds": outcome.rounds,
        "messages": outcome.message_count,
        "plan_summary": describe_plan(m, outcome.plan) if outcome.plan else None,
        "dissent": outcome.dissent,
        "conflicting_constraints": outcome.conflicting_constraints,
        "options": [{k: v for k, v in o.items() if k != "plan"} for o in outcome.options[:3]],
        "zopa_exists": baselines.zopa_exists(fresh_m, fresh_agents),
        "hard_feasible": baselines.hard_feasible(fresh_m, fresh_agents),
        "comparison": {
            "council": baselines.scorecard(fresh_m, fresh_agents, outcome.plan),
            "weighted_sum": baselines.scorecard(fresh_m, fresh_agents, baselines.weighted_sum(fresh_m, fresh_agents)),
            "majority_vote": baselines.scorecard(fresh_m, fresh_agents, baselines.majority_vote(fresh_m, fresh_agents)),
            "oracle": baselines.scorecard(fresh_m, fresh_agents, baselines.oracle(fresh_m, fresh_agents)),
        },
        "transcript": [msg.to_dict() for msg in outcome.messages],
    }
    if sc.events:
        # What if the council had locked in its pre-event deal? Judge that stale plan on the new world.
        no_event = Scenario(sc.name + "_no_event", "", sc.build, [], sc.kwargs)
        m0, a0 = no_event.build()
        stale = Deliberation(m0, a0).run()
        result["stale_plan"] = {
            "summary": describe_plan(m0, stale.plan) if stale.plan else None,
            "scorecard_after_event": baselines.scorecard(fresh_m, fresh_agents, stale.plan),
        }
    for name, card in result["comparison"].items():
        if card.get("plan"):
            card["summary"] = describe_plan(fresh_m, card["plan"])
    return result


def print_result(r: dict, verbose: bool) -> None:
    print(f"\n=== {r['scenario']} ===\n{r['story']}\n")
    for msg in r["transcript"]:
        kind = msg["kind"]
        if verbose or kind in ("EVENT", "CONSTRAINT_UPDATE", "DECISION", "STALEMATE") or (
            kind == "VOTE" and msg["payload"].get("vote") == "VETO"
        ) or (kind == "PROPOSAL" and msg["round"] in (0, 1)):
            print(f"  [r{msg['round']:>2}] {kind:<10} {msg['text']}")
    print(f"\nOUTCOME: {r['status']} after {r['rounds']} rounds, {r['messages']} messages (expected {r['expected']})")
    print(f"  {r['reason']}")
    if r["plan_summary"]:
        print(f"  plan: {r['plan_summary']}")
    for d in r["dissent"]:
        print(f"  dissent recorded: {d}")
    for c in r["conflicting_constraints"]:
        print(f"  conflicting constraint ({c['owner']}): {c['text']}")
    if r["status"] == "ESCALATE" and r["options"]:
        print("  options handed to the human:")
        for o in r["options"]:
            objections = ", ".join(f"{k}:{v['vote']}" for k, v in o["objections"].items()) or "none"
            print(f"    - {o['proposer']}'s plan: {o['summary']}\n      approved by {o['approvers']}; objections {objections}")
    print(f"\n  ZOPA exists (true preferences): {r['zopa_exists']}   hard constraints jointly feasible: {r['hard_feasible']}")
    print(f"  {'method':<14}{'satisfied':>10}{'violations':>12}{'min':>7}{'mean':>7}")
    for name, card in r["comparison"].items():
        if not card.get("plan"):
            print(f"  {name:<14}{'(no plan: deferred to human)':>36}")
            continue
        print(f"  {name:<14}{len(card['satisfied']):>8}/4{len(card['violated_constraints']):>12}"
              f"{card['min_score']:>7.2f}{card['mean_score']:>7.2f}")
    if "stale_plan" in r:
        s = r["stale_plan"]["scorecard_after_event"]
        if s.get("plan"):
            print(f"  stale pre-event deal judged after the event: {len(s['satisfied'])}/4 satisfied, "
                  f"{s['violated_constraints']} violated, {s['undeliverable_share'] * 100:.0f}% of order undeliverable")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenario", choices=[*SCENARIOS, "all"])
    ap.add_argument("--verbose", action="store_true", help="print every proposal and vote")
    ap.add_argument("--llm", action="store_true", help="have an LLM voice each agent (decisions unchanged)")
    ap.add_argument("--json", help="write full results (with transcript) to this file")
    args = ap.parse_args(argv)

    narrator = None
    if args.llm:
        from .narrate import llm_narrator

        narrator = llm_narrator()
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results = [run_scenario(SCENARIOS[n], narrator) for n in names]
    for r in results:
        print_result(r, args.verbose)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results if len(results) > 1 else results[0], f, indent=2, default=str)
    return 0 if all(r["status"] == r["expected"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
