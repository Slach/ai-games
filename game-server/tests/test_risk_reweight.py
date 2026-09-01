"""Unit tests for the VS risk reweighting (game_rules) and the
delay-action fallback for LLM auto-choice failures."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_rules import (  # noqa: E402
    compute_risk_factor,
    option_badness,
    reweight_probabilities,
)
from game_server import GameServer  # noqa: E402


class TestOptionBadness(unittest.TestCase):
    def test_empty_and_missing_fields_are_harmless(self):
        self.assertEqual(option_badness({}), 0.0)
        self.assertEqual(option_badness(None), 0.0)
        self.assertEqual(option_badness("not a dict"), 0.0)

    def test_hull_delta(self):
        self.assertAlmostEqual(option_badness({"ship_hull_change": -25}), 0.5)
        self.assertEqual(option_badness({"ship_hull_change": 10}), 0.0)

    def test_shields_delta(self):
        self.assertAlmostEqual(option_badness({"ship_shields_change": -50}), 1.0)
        self.assertEqual(option_badness({"ship_shields_change": 15}), 0.0)

    def test_deaths_and_injuries(self):
        option = {
            "dead_crew_members": [{"entity_id": "p1"}, {"entity_id": "p2"}],
            "crew_injured": [{"entity_id": "p3"}],
        }
        self.assertAlmostEqual(option_badness(option), 2 * 0.34 + 0.17)

    def test_negative_mission_points_takes_max_entry(self):
        option = {
            "mission_progress": [
                {"stage": 1, "points": -2},
                {"stage": 2, "points": -4},
                {"stage": 3, "points": 3},
            ]
        }
        self.assertAlmostEqual(option_badness(option), 4 / 4)
        self.assertEqual(option_badness({"mission_progress": [{"stage": 1, "points": 5}]}), 0.0)

    def test_clamped_to_one(self):
        option = {
            "ship_hull_change": -50,
            "ship_shields_change": -50,
            "dead_crew_members": [{"entity_id": "p1"}],
        }
        self.assertEqual(option_badness(option), 1.0)

    def test_invalid_values_count_as_no_harm(self):
        self.assertEqual(option_badness({"ship_hull_change": "oops", "crew_injured": "meh"}), 0.0)


class TestReweightProbabilities(unittest.TestCase):
    def test_identity_when_risk_zero(self):
        probs = [0.5, 0.2, 0.2, 0.1]
        self.assertEqual(reweight_probabilities(probs, [0.0, 0.5, 0.9, 1.0], 0.0), probs)

    def test_identity_when_no_badness(self):
        probs = [0.4, 0.3, 0.3]
        self.assertEqual(reweight_probabilities(probs, [0.0, 0.0, 0.0], 1.0), probs)

    def test_catastrophic_options_grow_at_full_risk(self):
        probs = [0.6, 0.25, 0.10, 0.05]
        badness = [0.0, 0.2, 0.8, 1.0]
        result = reweight_probabilities(probs, badness, 1.0)
        # p * (1 + 3*b): [0.6, 0.4, 0.34, 0.2] -> normalized [0.395, 0.263, 0.224, 0.132]
        self.assertGreater(result[3], probs[3])
        self.assertGreater(result[2], probs[2])
        self.assertLess(result[0], probs[0])
        self.assertAlmostEqual(sum(result), 1.0)

    def test_no_option_above_cap(self):
        probs = [0.95, 0.05]
        result = reweight_probabilities(probs, [0.0, 1.0], 1.0)
        self.assertLessEqual(max(result), 0.6 + 1e-9)
        self.assertAlmostEqual(sum(result), 1.0)

    def test_cap_redistributes_to_others(self):
        probs = [0.95, 0.05]
        result = reweight_probabilities(probs, [0.0, 1.0], 1.0)
        self.assertAlmostEqual(result[0], 0.6)
        self.assertAlmostEqual(result[1], 0.4)

    def test_renormalizes_non_unit_sum(self):
        probs = [0.4, 0.3]  # sums to 0.7, as sloppy LLMs emit; both stay under the cap
        result = reweight_probabilities(probs, [0.0, 0.0], 0.0)
        self.assertAlmostEqual(sum(result), 1.0)
        self.assertAlmostEqual(result[0], 0.4 / 0.7)

    def test_cap_applies_even_without_badness(self):
        probs = [0.8, 0.2]
        result = reweight_probabilities(probs, [0.0, 0.0], 0.0)
        self.assertAlmostEqual(result[0], 0.6)
        self.assertAlmostEqual(result[1], 0.4)

    def test_single_option_is_one(self):
        self.assertEqual(reweight_probabilities([0.7], [1.0], 1.0), [1.0])

    def test_invalid_inputs_return_copy_of_input(self):
        for probs, badness, risk in [
            ([], [], 0.5),
            ([0.5, 0.5], [1.0], 0.5),  # length mismatch
            ([0.5, "x"], [0.0, 0.0], 0.5),
            ([-0.5, 1.5], [0.0, 0.0], 0.5),  # negative probability
            ([0.0, 0.0], [0.0, 0.0], 0.5),  # zero sum
            ([0.5, 0.5], [0.0, 0.0], 1.5),  # risk out of range
            ([0.5, 0.5], [0.0, 0.0], -0.1),
        ]:
            with self.subTest(probs=probs, badness=badness, risk=risk):
                result = reweight_probabilities(probs, badness, risk)
                self.assertEqual(result, probs)
                self.assertIsNot(result, probs)

    def test_does_not_mutate_input(self):
        probs = [0.5, 0.5]
        badness = [0.0, 1.0]
        reweight_probabilities(probs, badness, 1.0)
        self.assertEqual(probs, [0.5, 0.5])


class TestComputeRiskFactor(unittest.TestCase):
    def test_safe_state_is_zero(self):
        self.assertEqual(compute_risk_factor(hull_ratio=1.0, threat_level=0, reckless_ratio=0.0), 0.0)

    def test_dire_state_clamps_to_one(self):
        self.assertEqual(compute_risk_factor(hull_ratio=0.0, threat_level=100, reckless_ratio=1.0), 1.0)

    def test_formula_midpoint(self):
        # 0.5*(1-0.5) + 0.4*(50/100) + 0.6*0.5 = 0.25 + 0.2 + 0.3 = 0.75
        self.assertAlmostEqual(
            compute_risk_factor(hull_ratio=0.5, threat_level=50, reckless_ratio=0.5),
            0.75,
        )

    def test_inputs_clamped(self):
        self.assertEqual(
            compute_risk_factor(hull_ratio=-3.0, threat_level=250, reckless_ratio=7.0),
            1.0,
        )
        self.assertEqual(
            compute_risk_factor(hull_ratio=4.0, threat_level=-40, reckless_ratio=-1.0),
            0.0,
        )

    def test_invalid_inputs_fall_back_to_safe(self):
        self.assertEqual(compute_risk_factor(hull_ratio="bad", threat_level=None, reckless_ratio="x"), 0.0)


_CHOICES = [
    {"id": "action_1", "text": "Scan the anomaly", "consequence_kind": "progress"},
    {"id": "action_2", "text": "Reroute power", "consequence_kind": "injury"},
    {"id": "action_3", "text": "Vent the deck", "consequence_kind": "fatal"},
]


class TestDelayFallback(unittest.IsolatedAsyncioTestCase):
    async def test_player_auto_choice_llm_failure_returns_delay(self):
        agent = GameServer(language="ru")
        agent.vs_enabled = False
        with patch.object(GameServer, "_call_llm", new=AsyncMock(side_effect=RuntimeError("boom"))):
            decision = await agent.generate_player_auto_choice(
                _CHOICES,
                {"role": "Pilot", "personality_traits": [], "species": ""},
                "briefing",
                None,
                "Аня",
                game_id=None,
                player_id="1",
                turn=1,
                kind="player_auto_choice",
            )
        self.assertEqual(decision["action_id"], "delay")
        self.assertEqual(decision["choice"]["id"], "delay")
        self.assertEqual(decision["choice"]["consequence_kind"], "delay")
        self.assertIn("Аня", decision["choice"]["text"])
        self.assertIn("Промедление", decision["choice"]["text"])

    async def test_player_auto_choice_invalid_id_returns_delay(self):
        agent = GameServer(language="en")
        agent.vs_enabled = False
        with patch.object(GameServer, "_call_llm", new=AsyncMock(return_value={"action_id": "bogus", "rationale": "?"})):
            decision = await agent.generate_player_auto_choice(
                _CHOICES,
                {"role": "Pilot", "personality_traits": [], "species": ""},
                "briefing",
                None,
                "Anya",
                game_id=None,
                player_id="1",
                turn=1,
                kind="player_auto_choice",
            )
        self.assertEqual(decision["action_id"], "delay")
        self.assertIn("Hesitation", decision["choice"]["text"])
        self.assertIn("Anya", decision["choice"]["text"])

    async def test_npc_choice_llm_failure_returns_delay(self):
        agent = GameServer(language="en")
        agent.vs_enabled = False
        with patch.object(GameServer, "_call_llm", new=AsyncMock(side_effect=RuntimeError("boom"))):
            decision = await agent.generate_npc_choice(
                _CHOICES,
                {"npc_name": "Ripley", "role": "Engineer", "personality_traits": []},
                game_id=None,
                player_id=None,
                turn=1,
                kind="npc_choice",
            )
        self.assertEqual(decision["action_id"], "delay")
        self.assertEqual(decision["choice"]["consequence_kind"], "delay")
        self.assertIn("Ripley", decision["choice"]["text"])

    async def test_npc_choice_invalid_id_returns_delay_ru(self):
        agent = GameServer(language="ru")
        agent.vs_enabled = False
        with patch.object(GameServer, "_call_llm", new=AsyncMock(return_value={"action_id": "nope", "rationale": "?"})):
            decision = await agent.generate_npc_choice(
                _CHOICES,
                {"npc_name": "Рипли", "role": "Инженер", "personality_traits": []},
                game_id=None,
                player_id=None,
                turn=1,
                kind="npc_choice",
            )
        self.assertEqual(decision["action_id"], "delay")
        self.assertIn("Промедление", decision["choice"]["text"])

    async def test_valid_choice_still_passes_through(self):
        agent = GameServer(language="en")
        agent.vs_enabled = False
        with patch.object(GameServer, "_call_llm", new=AsyncMock(return_value={"action_id": "action_2", "rationale": "in character"})):
            decision = await agent.generate_player_auto_choice(
                _CHOICES,
                {"role": "Pilot", "personality_traits": [], "species": ""},
                "briefing",
                None,
                "Anya",
                game_id=None,
                player_id="1",
                turn=1,
                kind="player_auto_choice",
            )
        self.assertEqual(decision["action_id"], "action_2")
        self.assertNotIn("choice", decision)


if __name__ == "__main__":
    unittest.main()
