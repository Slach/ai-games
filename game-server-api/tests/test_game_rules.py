"""Unit tests for the game-rules layer (pure functions, no DB/LLM)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_rules import (  # noqa: E402
    MAX_THRESHOLD,
    MIN_THRESHOLD,
    clamp_threshold,
    normalize_mission_objectives,
)


class TestNormalizeObjectives(unittest.TestCase):
    def test_clamp_high_threshold_to_max(self):
        self.assertEqual(clamp_threshold(10), MAX_THRESHOLD)

    def test_clamp_low_threshold_to_min(self):
        self.assertEqual(clamp_threshold(1), MIN_THRESHOLD)

    def test_clamp_keeps_value_in_range(self):
        self.assertEqual(clamp_threshold(4), 4)

    def test_clamp_non_numeric_defaults_to_min(self):
        self.assertEqual(clamp_threshold("oops"), MIN_THRESHOLD)

    def test_normalize_reindexes_strictly_1_based(self):
        objectives = [
            {"stage": 7, "name": "C", "description": "c", "success_threshold": 4},
            {"stage": 2, "name": "A", "description": "a", "success_threshold": 4},
            {"stage": 5, "name": "B", "description": "b", "success_threshold": 4},
        ]
        result = normalize_mission_objectives(objectives)
        self.assertEqual([o["stage"] for o in result], [1, 2, 3])
        self.assertEqual([o["name"] for o in result], ["A", "B", "C"])

    def test_normalize_clamps_thresholds(self):
        objectives = [
            {"name": "A", "success_threshold": 1},
            {"name": "B", "success_threshold": 99},
        ]
        result = normalize_mission_objectives(objectives)
        self.assertEqual(result[0]["success_threshold"], MIN_THRESHOLD)
        self.assertEqual(result[1]["success_threshold"], MAX_THRESHOLD)

    def test_normalize_does_not_mutate_input(self):
        objectives = [{"stage": 1, "name": "A", "success_threshold": 4}]
        normalize_mission_objectives(objectives)
        self.assertEqual(objectives[0]["stage"], 1)


from game_rules import (  # noqa: E402
    apply_mission_progress,
    normalize_mission,
)


def _mission(stages, progress=None):
    """Build a normalized mission with given (name, threshold) stages."""
    objectives = [{"stage": i + 1, "name": n, "description": "", "success_threshold": t} for i, (n, t) in enumerate(stages)]
    return normalize_mission({"objectives": objectives, "stage_progress": progress or {}})


class TestApplyMissionProgress(unittest.TestCase):
    def test_progress_accumulates_to_completion(self):
        m = _mission([("A", 3), ("B", 3)])
        m = apply_mission_progress(m, [{"stage": 1, "points": 2}])
        self.assertFalse(m["completed"])
        self.assertEqual(m["current_stage"], 1)
        m = apply_mission_progress(m, [{"stage": 1, "points": 2}])  # stage1 = 4 >= 3
        self.assertEqual(m["stage_progress"]["1"], 4)
        self.assertFalse(m["completed"])
        self.assertEqual(m["current_stage"], 2)
        m = apply_mission_progress(m, [{"stage": 2, "points": 3}])
        self.assertTrue(m["completed"])

    def test_off_by_one_fixed_current_stage_is_1(self):
        """Spec defect B: current_stage must not stay at 0."""
        m = _mission([("A", 3)])
        self.assertEqual(m["current_stage"], 1)

    def test_no_premature_completion(self):
        """Spec defect C: completing stage N-1 must NOT mark mission complete."""
        m = _mission([("A", 3), ("B", 3), ("C", 3)])
        m = apply_mission_progress(m, [{"stage": 1, "points": 5}])
        m = apply_mission_progress(m, [{"stage": 2, "points": 5}])
        # stage 3 not yet reached -> not complete
        self.assertFalse(m["completed"])
        self.assertEqual(m["current_stage"], 3)

    def test_regression_capped_to_minus_one(self):
        m = _mission([("A", 5)])
        m = apply_mission_progress(m, [{"stage": 1, "points": 4}])
        self.assertEqual(m["stage_progress"]["1"], 4)
        m = apply_mission_progress(m, [{"stage": 1, "points": -9}])
        # cap at -1 -> 4 - 1 = 3 (not 4 - 9 = 0 via floor; regression is bounded)
        self.assertEqual(m["stage_progress"]["1"], 3)

    def test_completed_stage_does_not_rollback(self):
        m = _mission([("A", 3), ("B", 3)])
        m = apply_mission_progress(m, [{"stage": 1, "points": 5}])  # stage1 = 5 >= 3
        self.assertEqual(m["stage_progress"]["1"], 5)
        m = apply_mission_progress(m, [{"stage": 1, "points": -1}])
        # completed stage must not drop below threshold
        self.assertEqual(m["stage_progress"]["1"], 5)

    def test_tempo_floor_advances_current_stage_by_one(self):
        """A turn with no positive progress on the current stage still nudges +1."""
        m = _mission([("A", 5)])
        m = apply_mission_progress(m, [{"stage": 1, "points": 2}])
        self.assertEqual(m["stage_progress"]["1"], 2)
        m = apply_mission_progress(m, [{"stage": 1, "points": 0}])  # no advance proposed
        self.assertEqual(m["stage_progress"]["1"], 3)

    def test_ignores_unknown_stage_and_bad_points(self):
        m = _mission([("A", 3)])
        m = apply_mission_progress(
            m,
            [{"stage": 99, "points": 5}, {"stage": 1, "points": "bad"}, {}],
        )
        # tempo floor still applies to stage 1 -> 1
        self.assertEqual(m["stage_progress"]["1"], 1)


from unittest.mock import patch  # noqa: E402

from game_master import GameMasterAgent  # noqa: E402


class TestGenerateMissionNormalization(unittest.TestCase):
    def _fake_llm_result(self):
        return {
            "name": "Echo Protocol",
            "description": "A test mission.",
            "objectives": [
                {"stage": 3, "name": "C", "description": "c", "success_threshold": 1},
                {"stage": 1, "name": "A", "description": "a", "success_threshold": 99},
                {"stage": 2, "name": "B", "description": "b", "success_threshold": 4},
            ],
        }

    def test_generate_mission_normalizes_objectives_and_stages(self):
        agent = GameMasterAgent(language="en")
        with patch.object(GameMasterAgent, "_call_llm", return_value=self._fake_llm_result()):
            result = agent.generate_mission([{"role": "Pilot", "type": "player"}])
        self.assertEqual([o["stage"] for o in result["objectives"]], [1, 2, 3])
        self.assertEqual([o["name"] for o in result["objectives"]], ["A", "B", "C"])
        for o in result["objectives"]:
            self.assertGreaterEqual(o["success_threshold"], MIN_THRESHOLD)
            self.assertLessEqual(o["success_threshold"], MAX_THRESHOLD)
        self.assertEqual(result["current_stage"], 1)
        self.assertEqual(result["total_stages"], 3)
        self.assertFalse(result["completed"])


import random as _random  # noqa: E402

from game_rules import (  # noqa: E402
    FORBIDDEN_OPENINGS,
    MISSION_ARCHETYPES,
    SEED_TABLES,
    select_mission_seeds,
)


class TestMissionSeeds(unittest.TestCase):
    def test_select_returns_archetype_and_all_seed_tables(self):
        rng = _random.Random(42)
        result = select_mission_seeds(language="en", rng=rng)
        self.assertIn(result["archetype"], MISSION_ARCHETYPES)
        self.assertEqual(set(result["seeds"].keys()), set(SEED_TABLES.keys()))

    def test_select_is_deterministic_with_seed(self):
        r1 = select_mission_seeds(language="en", rng=_random.Random(123))
        r2 = select_mission_seeds(language="en", rng=_random.Random(123))
        self.assertEqual(r1, r2)

    def test_ru_and_en_tables_have_matching_keys(self):
        for table, opts in SEED_TABLES.items():
            self.assertIn("ru", opts)
            self.assertIn("en", opts)
            self.assertGreaterEqual(len(opts["ru"]), 4)
            self.assertEqual(len(opts["ru"]), len(opts["en"]))
        self.assertIn("ru", FORBIDDEN_OPENINGS)
        self.assertIn("en", FORBIDDEN_OPENINGS)

    def test_all_archetypes_have_both_languages(self):
        for key, val in MISSION_ARCHETYPES.items():
            self.assertIn("ru", val)
            self.assertIn("en", val)


from prompts import build_mission_prompts  # noqa: E402


class TestMissionPromptInjection(unittest.TestCase):
    def test_prompt_includes_archetype_and_seeds(self):
        seeds = select_mission_seeds(language="en", rng=_random.Random(7))
        system, user = build_mission_prompts("en", "  - Pilot (player)", archetype=seeds["archetype"], seeds=seeds["seeds"])
        self.assertIn(seeds["archetype"], system + user)
        for value in seeds["seeds"].values():
            self.assertIn(value, system + user)

    def test_prompt_lists_forbidden_openings_and_threshold_range(self):
        _, user = build_mission_prompts("ru", "  - Пилот (игрок)")
        self.assertIn("3-5", user)
        self.assertIn("сигнал", user)  # forbidden list mentions the banned trope


from game_rules import (  # noqa: E402
    DEATH_COOLDOWN_TURNS,
    apply_death_limits,
)


class TestDeathLimits(unittest.TestCase):
    def test_first_death_allowed(self):
        outcome = {"dead_crew_members": [["A", "Pilot"]], "crew_injured": []}
        out, last = apply_death_limits(outcome, day=3, last_death_day=0, alive_count=5)
        self.assertEqual(out["dead_crew_members"], [["A", "Pilot"]])
        self.assertEqual(last, 3)
        self.assertEqual(out["crew_injured"], [])

    def test_second_death_on_cooldown_is_demoted_to_critical(self):
        outcome = {"dead_crew_members": [["B", "Medic"]], "crew_injured": []}
        out, last = apply_death_limits(
            outcome, day=4, last_death_day=3, alive_count=5
        )
        self.assertEqual(out["dead_crew_members"], [])
        self.assertEqual(out["crew_injured"], [["B", "Medic", "critical"]])
        self.assertEqual(last, 3)  # unchanged, no new death accepted

    def test_death_after_cooldown_allowed_again(self):
        outcome = {"dead_crew_members": [["C", "Engineer"]], "crew_injured": []}
        out, last = apply_death_limits(
            outcome, day=3 + DEATH_COOLDOWN_TURNS, last_death_day=3, alive_count=4
        )
        self.assertEqual(out["dead_crew_members"], [["C", "Engineer"]])
        self.assertEqual(last, 3 + DEATH_COOLDOWN_TURNS)

    def test_extra_deaths_in_one_turn_demoted(self):
        outcome = {
            "dead_crew_members": [["A", "Pilot"], ["B", "Medic"], ["C", "Eng"]],
            "crew_injured": [],
        }
        out, _ = apply_death_limits(outcome, day=5, last_death_day=0, alive_count=6)
        self.assertEqual(len(out["dead_crew_members"]), 1)
        self.assertEqual(len(out["crew_injured"]), 2)
        self.assertTrue(all(i[2] == "critical" for i in out["crew_injured"]))

    def test_never_kill_below_min_alive(self):
        outcome = {"dead_crew_members": [["A", "Pilot"]], "crew_injured": []}
        out, last = apply_death_limits(
            outcome, day=2, last_death_day=0, alive_count=1, min_alive=1
        )
        self.assertEqual(out["dead_crew_members"], [])
        self.assertEqual(last, 0)
        self.assertEqual(out["crew_injured"], [["A", "Pilot", "critical"]])

    def test_ship_destruction_not_throttled(self):
        outcome = {
            "ship_destroyed": True,
            "dead_crew_members": [["A", "Pilot"], ["B", "Medic"]],
        }
        out, last = apply_death_limits(outcome, day=4, last_death_day=3, alive_count=5)
        self.assertEqual(len(out["dead_crew_members"]), 2)
        self.assertEqual(last, 3)


if __name__ == "__main__":
    unittest.main()
