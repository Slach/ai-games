"""Doom clock: code-owned threat level 0-100 that ticks every turn.

compute_threat_tick grows threat by CODE (never by the LLM): base tick per
turn, accelerated by auto-selected actions (hesitation), a critically damaged
hull and mission stagnation. Reaching THREAT_MAX ends the game with
end_game("overwhelmed"). These tests cover the pure function, the threat_level
DB roundtrip, the auto-action counter feeding auto_ratio, and the
threat=100 → game over path (test_end_game pattern).
"""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_rules import (  # noqa: E402
    THREAT_AUTO_PENALTY,
    THREAT_BASE_TICK,
    THREAT_HULL_CRITICAL_RATIO,
    THREAT_HULL_PENALTY,
    THREAT_MAX,
    THREAT_STAGNATION_PENALTY,
    compute_threat_tick,
)
import database as db  # noqa: E402
from prompts import format_threat_status  # noqa: E402
from language import LANGUAGE_EN, LANGUAGE_RU  # noqa: E402

logger = logging.getLogger(__name__)


class TestComputeThreatTick(unittest.TestCase):
    def test_base_tick(self):
        self.assertEqual(compute_threat_tick(0), THREAT_BASE_TICK)
        self.assertEqual(compute_threat_tick(20), 20 + THREAT_BASE_TICK)

    def test_auto_acceleration(self):
        self.assertEqual(
            compute_threat_tick(0, auto_ratio=0.5),
            THREAT_BASE_TICK + int(THREAT_AUTO_PENALTY * 0.5),
        )
        self.assertEqual(
            compute_threat_tick(0, auto_ratio=1.0),
            THREAT_BASE_TICK + THREAT_AUTO_PENALTY,
        )
        # manual choices only — no acceleration
        self.assertEqual(compute_threat_tick(0, auto_ratio=0.0), THREAT_BASE_TICK)

    def test_hull_acceleration(self):
        self.assertEqual(
            compute_threat_tick(0, hull_ratio=0.0),
            THREAT_BASE_TICK + THREAT_HULL_PENALTY,
        )
        self.assertEqual(
            compute_threat_tick(0, hull_ratio=THREAT_HULL_CRITICAL_RATIO - 0.01),
            THREAT_BASE_TICK + THREAT_HULL_PENALTY,
        )
        # at or above the critical ratio there is no penalty
        self.assertEqual(
            compute_threat_tick(0, hull_ratio=THREAT_HULL_CRITICAL_RATIO),
            THREAT_BASE_TICK,
        )

    def test_stagnation_penalty(self):
        self.assertEqual(
            compute_threat_tick(0, mission_stagnant=True),
            THREAT_BASE_TICK + THREAT_STAGNATION_PENALTY,
        )
        self.assertEqual(compute_threat_tick(0, mission_stagnant=False), THREAT_BASE_TICK)

    def test_all_penalties_stack(self):
        self.assertEqual(
            compute_threat_tick(0, auto_ratio=1.0, hull_ratio=0.2, mission_stagnant=True),
            THREAT_BASE_TICK + THREAT_AUTO_PENALTY + THREAT_HULL_PENALTY + THREAT_STAGNATION_PENALTY,
        )

    def test_clamped_at_max(self):
        self.assertEqual(compute_threat_tick(THREAT_MAX - 5), THREAT_MAX)
        self.assertEqual(
            compute_threat_tick(THREAT_MAX - 1, auto_ratio=1.0, hull_ratio=0.0, mission_stagnant=True),
            THREAT_MAX,
        )
        self.assertEqual(compute_threat_tick(THREAT_MAX), THREAT_MAX)

    def test_invalid_inputs_fall_back_to_safe_defaults(self):
        # invalid current threat counts as 0
        self.assertEqual(compute_threat_tick(None), THREAT_BASE_TICK)
        self.assertEqual(compute_threat_tick("garbage"), THREAT_BASE_TICK)
        # out-of-range threat is clamped first
        self.assertEqual(compute_threat_tick(-50), THREAT_BASE_TICK)
        self.assertEqual(compute_threat_tick(500), THREAT_MAX)
        # invalid ratios → 0 auto penalty, intact hull
        self.assertEqual(compute_threat_tick(50, auto_ratio="bad", hull_ratio=None), 50 + THREAT_BASE_TICK)
        # NaN / infinite ratios are invalid, not catastrophic
        self.assertEqual(compute_threat_tick(50, auto_ratio=float("nan")), 50 + THREAT_BASE_TICK)
        self.assertEqual(
            compute_threat_tick(50, auto_ratio=float("inf"), hull_ratio=float("inf")),
            50 + THREAT_BASE_TICK,
        )

    def test_result_is_int(self):
        result = compute_threat_tick("30", auto_ratio="0.4", hull_ratio="0.1", mission_stagnant=1)
        self.assertIsInstance(result, int)
        self.assertEqual(result, 30 + THREAT_BASE_TICK + int(THREAT_AUTO_PENALTY * 0.4) + THREAT_HULL_PENALTY + THREAT_STAGNATION_PENALTY)


class TestThreatDb(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB: %s", self._tmp.name, exc_info=True)

    def _make_game(self, game_id: str) -> None:
        db.create_game({"game_id": game_id, "name": "T", "setting": "starship", "language": "ru"})

    def test_new_game_threat_starts_at_zero(self):
        self._make_game("g1")
        state = db.get_game_state("g1")
        self.assertEqual(state["threat_level"], 0)

    def test_update_roundtrips_threat(self):
        self._make_game("g2")
        db.update_game_state(2, "active", True, threat_level=34, game_id="g2")
        state = db.get_game_state("g2")
        self.assertEqual(state["threat_level"], 34)

    def test_turn_advance_does_not_clobber_threat(self):
        self._make_game("g3")
        db.update_game_state(1, "active", True, threat_level=57, game_id="g3")
        db.update_game_state(2, "active", True, game_id="g3")
        self.assertEqual(db.get_game_state("g3")["threat_level"], 57)

    def test_reset_game_state_to_turn1_clears_threat(self):
        self._make_game("g4")
        db.update_game_state(5, "active", True, threat_level=95, game_id="g4")
        db.reset_game_state_to_turn1("g4")
        state = db.get_game_state("g4")
        self.assertEqual(state["turn"], 1)
        self.assertEqual(state["threat_level"], 0)

    def test_count_turn_action_autos(self):
        self._make_game("g5")
        db.create_player_profile({"player_id": 1, "role": "Инженер", "game_id": "g5"})
        db.create_player_profile({"player_id": 2, "role": "Пилот", "game_id": "g5"})
        db.save_player_action(1, 3, "action_1", "selected", None)
        db.save_player_action(2, 3, "action_2", "auto_selected", None)
        total, auto = db.count_turn_action_autos(3, game_id="g5")
        self.assertEqual((total, auto), (2, 1))

    def test_count_turn_action_autos_empty_turn(self):
        self._make_game("g6")
        self.assertEqual(db.count_turn_action_autos(9, game_id="g6"), (0, 0))

    def test_threat_reaching_max_ends_game(self):
        """Doom clock integration: tick from 95 reaches 100 → game over (overwhelmed)."""
        self._make_game("g7")
        db.update_game_state(4, "active", True, threat_level=95, game_id="g7")
        before = db.get_game_state("g7")
        self.assertEqual(before["threat_level"], 95)
        self.assertTrue(db.is_game_active("g7"))

        new_threat = compute_threat_tick(before["threat_level"], auto_ratio=1.0, mission_stagnant=True)
        self.assertEqual(new_threat, THREAT_MAX)
        db.update_game_state(4, "active", True, threat_level=new_threat, game_id="g7")
        if new_threat >= THREAT_MAX:
            db.end_game("overwhelmed", game_id="g7")

        after = db.get_game_state("g7")
        self.assertEqual(after["threat_level"], THREAT_MAX)
        self.assertEqual(after["status"], "overwhelmed")
        self.assertFalse(after["ship_alive"])
        self.assertFalse(db.is_game_active("g7"))
        self.assertEqual(db.get_game("g7")["status"], "ended")


class TestThreatStatusLine(unittest.TestCase):
    def test_ru_line_has_bar_and_level(self):
        line = format_threat_status(LANGUAGE_RU, 34)
        self.assertIn("Угроза миссии", line)
        self.assertIn("▓▓▓", line)
        self.assertIn("░" * 7, line)
        self.assertIn("34/100", line)
        self.assertIn("враждебное окружение сжимает кольцо", line)

    def test_en_line(self):
        line = format_threat_status(LANGUAGE_EN, 75)
        self.assertIn("Mission threat", line)
        self.assertIn("▓" * 7, line)
        self.assertIn("75/100", line)
        self.assertIn("time is running out", line)

    def test_max_level_bar_full(self):
        line = format_threat_status(LANGUAGE_RU, THREAT_MAX)
        self.assertIn("▓" * 10, line)
        self.assertNotIn("░", line)

    def test_level_clamped(self):
        self.assertIn("0/100", format_threat_status(LANGUAGE_RU, -5))
        self.assertIn("100/100", format_threat_status(LANGUAGE_RU, 250))


if __name__ == "__main__":
    unittest.main()
