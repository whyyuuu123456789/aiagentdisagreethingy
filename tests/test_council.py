import unittest

from council.agents import lungo_council
from council.baselines import scorecard
from council.market import lungo_market
from council.protocol import Deliberation, plan_hash
from council.run import run_scenario
from council.scenarios import SCENARIOS


class ScenarioTests(unittest.TestCase):
    def test_every_scenario_reaches_its_expected_outcome(self):
        for name, sc in SCENARIOS.items():
            with self.subTest(name):
                self.assertEqual(run_scenario(sc)["status"], sc.expect)

    def test_runs_are_deterministic(self):
        a = run_scenario(SCENARIOS["frost"])
        b = run_scenario(SCENARIOS["frost"])
        self.assertEqual(a["transcript"], b["transcript"])

    def test_consensus_plan_satisfies_everyone_truly(self):
        r = run_scenario(SCENARIOS["harvest"])
        card = r["comparison"]["council"]
        self.assertEqual(len(card["satisfied"]), 4)
        self.assertEqual(card["violated_constraints"], [])

    def test_frost_plan_uses_discovered_supplier_and_respects_new_capacity(self):
        r = run_scenario(SCENARIOS["frost"])
        card = r["comparison"]["council"]
        self.assertGreater(card["plan"].get("ethiopia", 0), 0)
        self.assertLessEqual(card["plan"]["brazil"], 0.20 + 1e-3)
        self.assertEqual(card["undeliverable_share"], 0)

    def test_infeasible_constraints_are_named_for_the_human(self):
        r = run_scenario(SCENARIOS["budget_clash"])
        owners = {c["owner"] for c in r["conflicting_constraints"]}
        self.assertEqual(owners, {"cost", "quality"})

    def test_dissent_is_recorded(self):
        r = run_scenario(SCENARIOS["stubborn_small"])
        self.assertTrue(r["dissent"])
        self.assertEqual(r["comparison"]["council"]["violated_constraints"], [])


class ProtocolTests(unittest.TestCase):
    def test_private_constraints_start_unknown_and_are_revealed_by_veto(self):
        m, agents = lungo_market(), lungo_council()
        self.assertTrue(all(not a.known_constraints for a in agents))
        d = Deliberation(m, agents, max_rounds=1)
        d.run()
        vetoers = {msg.sender for msg in d.bus.log if msg.kind == "VOTE" and msg.payload["vote"] == "VETO"}
        for a in agents:
            self.assertEqual(set(a.known_constraints) , vetoers - {a.id})

    def test_commit_hashes_match_on_consensus(self):
        m, agents = lungo_market(), lungo_council()
        agents[0].floor = 0.25
        out = Deliberation(m, agents).run()
        commits = [msg.payload["plan_hash"] for msg in out.messages if msg.kind == "COMMIT" and msg.round == out.rounds - 1]
        self.assertEqual(len(set(commits)), 1)
        self.assertEqual(commits[0], plan_hash(out.plan))

    def test_scorecard_flags_violations(self):
        m, agents = lungo_market(), lungo_council()
        cheap = {"vietnam": 0.6, "brazil": 0.4, "colombia": 0.0, "honduras": 0.0}
        card = scorecard(m, agents, cheap)
        self.assertEqual(set(card["violated_constraints"]), {"quality", "green", "risk"})


if __name__ == "__main__":
    unittest.main()
