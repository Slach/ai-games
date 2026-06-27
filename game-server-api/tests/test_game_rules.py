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
    objectives = [
        {"stage": i + 1, "name": n, "description": "", "success_threshold": t}
        for i, (n, t) in enumerate(stages)
    ]
    return normalize_mission(
        {"objectives": objectives, "stage_progress": progress or {}}
    )


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
        with patch.object(
            GameMasterAgent, "_call_llm", return_value=self._fake_llm_result()
        ):
            result = agent.generate_mission([{"role": "Pilot", "type": "player"}])
        self.assertEqual([o["stage"] for o in result["objectives"]], [1, 2, 3])
        self.assertEqual([o["name"] for o in result["objectives"]], ["A", "B", "C"])
        for o in result["objectives"]:
            self.assertGreaterEqual(o["success_threshold"], MIN_THRESHOLD)
            self.assertLessEqual(o["success_threshold"], MAX_THRESHOLD)
        self.assertEqual(result["current_stage"], 1)
        self.assertEqual(result["total_stages"], 3)
        self.assertFalse(result["completed"])


if __name__ == "__main__":
    unittest.main()
