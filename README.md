# Lungo Council: four coffee buyers who don't agree

**Level 3 · Collective intelligence** extension of [coffeeAGNTCY](https://github.com/agntcy/coffeeAgntcy).

Lungo's supervisor *asks* farms for yields and decides on its own. The Council removes that
supervisor. Four autonomous stakeholder agents must split a green-coffee order across
Lungo's farms (Brazil, Colombia, Vietnam, plus Honduras, and Ethiopia once it appears in the
directory). Each agent wants something different and knows things the others don't:

| Agent | Argues for | Private hard limit (vetoes over it) | Private floor |
|---|---|---|---|
| Procurement (Cost) | lowest $/lb | max average price | min cost score it will accept |
| Quality (Sommelier) | cup score | min average cup score | ″ |
| Sustainability | low carbon, certified share | min certified share | ″ |
| Supply Risk | on-time delivery, diversification | max share from any one origin | ″ |

Scoring formulas are public, so anyone can evaluate a plan for anyone. **How much each
agent needs** (its floor, how fast it concedes) and **what it will never accept** (its hard
limit) are private. The only way to learn them is to negotiate.

```
python3 -m council.run frost           # one scenario, condensed transcript + baseline comparison
python3 -m council.run all --verbose   # all seven scenarios, every proposal and vote
python3 -m council.evaluate --trials 300 --json results/evaluation.json
python3 -m unittest discover -s tests -t .
python3 -m council.cards               # writes A2A AgentCards to cards/
```

Pure standard-library Python 3.12+: no API key, Docker or network needed, and every run is deterministic.
`--llm` optionally voices each agent through litellm using the repo's `LLM_MODEL` convention.

## Architecture

```
                 lungo/council/procurement   (one SLIM/NATS group topic; InProcessBus locally)
   ┌──────────────┬──────────────┬──────────────┬──────────────┐
   │ Cost agent   │ Quality agent│ Green agent  │ Risk agent   │   each: own objective, private floor,
   │ propose/vote │ propose/vote │ propose/vote │ propose/vote │   private hard limit, concession rate,
   │ decide/commit│ decide/commit│ decide/commit│ decide/commit│   its own solver, its own decision
   └──────▲───────┴──────▲───────┴──────▲───────┴──────▲───────┘
          │ discover(capability="green_coffee")        │
   ┌──────┴────────────────────────────────────────────┴──────┐
   │ Market directory: farms register / fail / change           │ ◄── world events (frost, shipment failure,
   └──────────────────────────────────────────────────────────┘      new supplier) arrive as EVENT messages
```

* `market.py`: farms, the capability directory, and the four public scoring functions.
* `agents.py`: `StakeholderAgent`: private state, `propose`, `vote`, `observe`, `on_event`.
* `protocol.py`: the deliberation rounds and stopping rules. **No component chooses the plan.**
  The round clock only advances time and injects events.
* `solver.py`: deterministic pairwise-transfer local search each agent uses to plan (no LP dependency).
* `baselines.py`: weighted-sum optimizer, one-shot majority vote, full-information oracle, and a common scorecard.
* `scenarios.py`, `run.py`, `evaluate.py`: repeatable demos and the randomized study.
* `cards.py`: A2A AgentCards in Lungo's format (`slim://…/lungo/agents/council_<id>`, a `deliberate` skill
  tagged `objective:<role>`), so the agents can be served and discovered like Lungo's farms.

### The deliberation mechanism

Every round, every agent:

1. **Proposes** the plan that is best *for itself* while closing the gaps others announced.
   Its objective is `own_score − w·Σ shortfall(others' announced bars) − big·hard_violations(known limits)`.
   `w` starts small and grows with time, so agents open selfishly and accommodate as talks run on.
2. **Announces its bar**, the score it currently needs. The bar starts at 1.0 and drops by the agent's
   private concession rate each round, but never below its private floor.
3. **Votes** on every proposal: `APPROVE`, `REJECT` with "I need ≥ x", or `VETO`.
   A veto **reveals the agent's hard limit** to everyone, and from then on all agents plan around it.
4. **Decides locally.** Each agent applies the same public rule to the shared log: among plans
   everyone approved, take the one with the largest minimum margin over announced bars. It then
   broadcasts a `COMMIT` hash. Consensus means every agent independently derived the same plan.

### When to stop, keep disagreeing, or ask a human

| Situation (detected by every agent from public info) | Outcome | Why |
|---|---|---|
| A proposal everyone approves | **CONSENSUS** | Every true floor and hard limit is met. |
| Revealed hard limits are jointly infeasible | **ESCALATE** at once, naming the minimal colliding limits | No amount of concession fixes it. Trading budget against quality is a value judgement for a human. |
| Everyone is at their floor, no unanimous plan, a supermajority (3/4) backs a veto-free plan, and the order is ≤ $5k | **PROCEED_WITH_DISSENT** | Small and reversible, so act, but **record the dissent** rather than paper over it. |
| Same stalemate, but the order is large (or there's no supermajority) | **ESCALATE** with the top options and who objects to each | Overriding a stakeholder on a $34k commitment is not an agent's call. |
| Supply can no longer cover the order | **ESCALATE** | The problem itself must be re-scoped. |

## Demonstration: seven repeatable scenarios, from easy to impossible

`python3 -m council.run all` checks each scenario against its expected outcome and exits non-zero on a mismatch.

| Scenario | Difficulty | What happens | Outcome |
|---|---|---|---|
| `quick_deal` | Easy | Harvest's limits, but every stakeholder lowers its bar by 0.35 a round instead of 0.05–0.07. Round 1's vetoes reveal the hard limits, and the next proposals respect them. | CONSENSUS in 3 rounds, on the same plan as `harvest` |
| `harvest` | In between | Cost opens with 60% Vietnam ($2.68/lb). Quality, Sustainability and Risk veto it, revealing cup ≥ 83, ≥ 35% certified, and ≤ 50% per origin. Bars fall until all four approve. | CONSENSUS in 13 rounds: Brazil 42 / Colombia 23 / Honduras 22 / Vietnam 13 at $3.38/lb |
| `frost` | In between, with new information | At round 3 a frost cuts Brazil to 20% and raises its risk. Ethiopia registers in the directory. Risk privately tightens to ≤ 40% and announces it. Cost gets budget headroom. Agents half-reset their concessions and renegotiate around the new farm. | CONSENSUS: Vietnam 35 / Ethiopia 30 / Honduras 27 / Colombia 8 |
| `hard_line` | Long | Procurement caps price at $3.50, Sustainability demands ≥ 50% certified, Risk caps any origin at 40%, and bars drop only 0.02 a round. In round 1 every agent vetoes at least one plan. | CONSENSUS in 37 rounds: Brazil 30 / Colombia 30 / Vietnam 20 / Honduras 20 at $3.39/lb. The weighted-sum optimizer and the majority vote both break the $3.50 limit. |
| `budget_clash` | Impossible | Finance caps price at $3.25 while Quality demands cup ≥ 84. | ESCALATE after **1 round**, naming exactly those two limits |
| `stubborn_small` | Impossible | Procurement's floor is unreachable given everyone else's limits. | PROCEED_WITH_DISSENT, logging "cost: 0.307 < needs 0.45" |
| `stubborn_large` | Impossible | Same, but 10,000 lb (~$34k). | ESCALATE with three options and the objections to each |

Effect of new information (frost): the deal the council would have signed *without* the frost,
judged after it, leaves **22% of the order undeliverable** and breaks Supply Risk's new limit.
The renegotiated plan satisfies all four agents.

## Results: 300 randomized worlds

Each trial randomizes farm prices, quality, carbon and risk (±15%), every agent's floor, hard
limit and concession rate. A third of the trials also inject a shipment failure plus a new
supplier mid-negotiation. Plans are judged against every agent's **true** private preferences.
Ground truth (computed with full information) finds 181 worlds where an agreement zone exists,
50 where the hard limits are compatible but the floors are not, and 69 where the hard limits collide.

| Method | Decides | All 4 truly satisfied | Plans breaking a hard limit | Mean min score |
|---|---|---|---|---|
| **Council** | 209 / 300 (defers the rest to a human) | **171** | **0** | **0.40** |
| Weighted-sum optimizer (central, public info only) | 300 | 2 | 244 | 0.06 |
| One-shot majority vote | 300 | 21 | 219 | 0.05 |
| Full-information oracle (upper bound, not realistic) | 300 | 181 | 74 | 0.37 |
| Ablation: agents ignore announced bars | 209 | 133 | 0 | 0.36 |

* **Consensus recall:** the council reached consensus in **93.9%** of worlds where one existed (170/181).
* **Escalation precision:** it escalated in **100%** of worlds with colliding hard limits. It never
  "agreed" on a plan that silently broke someone's limit; every non-escalated plan is veto-free.
* **New information:** across 61 event trials, stale pre-event deals left 13.3% of the order
  undeliverable on average and broke a hard limit in 26 of them. The council's renegotiated plans had 0% undeliverable and 0 violations.
* **Listening matters:** with announced bars ignored, consensus recall drops from 93.9% to 73.5%
  and talks run longer (10.9 vs 9.4 rounds). Announcing "what I need" is the cheapest useful
  information an agent can share.

Raw numbers: [`results/evaluation.json`](results/evaluation.json); scenario transcripts: [`results/scenarios.json`](results/scenarios.json).

## Lessons learned

1. **Central optimizers fail because of what they can't see, not how they optimize.** With only
   public objectives, the weighted-sum planner broke a hidden hard limit in 81% of worlds. The
   oracle, given everyone's private limits, does about as well as the council. The council's value is
   that it *surfaces* private constraints through vetoes without anyone handing over their full preferences.
2. **A veto that explains itself is the key move.** A bare "no" leads to endless counter-offers.
   "No, because cup score must be ≥ 83" turns a rejection into a constraint every agent can plan around.
3. **Early accommodation backfires.** In the first version, agents weighted *every* announced bar
   fully from round one. Faced with three bars at 1.0, Procurement proposed a plan scoring 0.00 for
   itself. Scaling accommodation by time and by the number of other agents restored real advocacy.
4. **Disagreement has types, and each needs a different exit.** A collision of hard limits is
   detectable immediately and should go straight to a human. A clash of floors only shows up after
   concessions run out, and can be acted on with recorded dissent when the stakes are reversible.
5. **Determinism made the system debuggable.** Every run replays byte-for-byte (a test checks this).
   That is how we traced an apparent "false consensus" to a rounding bug in the scorecard, not the agents.

### Limitations and next steps

* About 6% of agreement zones are missed. Every agent's planner is a local search, and announced bars
  lag one round behind. Better planners (an LP, or proposing the oracle-style max-min plan near the end)
  should close part of the gap.
* Agents report their bars honestly. A strategic agent could inflate its bar. A natural extension is
  to cap how much each agent can claim beyond its credible alternative.
* The bus here is in-process. The agents use a publish/subscribe interface on one topic, matching
  AGNTCY's SLIM/NATS group messaging, and `cards/` holds their A2A cards. Running them as separate
  `AgntcyFactory` services on Lungo's docker-compose stack is the remaining integration step; it was
  not exercised in this build because Docker was unavailable.
