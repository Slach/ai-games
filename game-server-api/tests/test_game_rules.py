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


if __name__ == "__main__":
    unittest.main()
