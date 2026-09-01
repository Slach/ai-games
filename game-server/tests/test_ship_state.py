"""Ship status as persistent code-owned state.

The LLM reports only per-turn deltas (ship_hull_change / ship_shields_change /
systems_taken_offline / systems_restored); game_state holds the authoritative
hull/shields/systems_offline. These tests cover the pure rules functions and
the DB roundtrip (update_game_state / get_game_state) on a temporary sqlite DB.
"""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_rules import (  # noqa: E402
    HULL_MAX,
    SHIELDS_MAX,
    apply_ship_status,
    apply_systems_offline,
)
import database as db  # noqa: E402
from prompts import build_combined_outcome_prompts  # noqa: E402
from language import LANGUAGE_RU  # noqa: E402

logger = logging.getLogger(__name__)


class TestApplyShipStatus(unittest.TestCase):
    def test_damage_reduces_hull(self):
        self.assertEqual(apply_ship_status(100, 100, -25, -30), (75, 70))

    def test_damage_accumulates_across_turns(self):
        hull, shields = apply_ship_status(35, 20, -10, 0)
        self.assertEqual((hull, shields), (25, 20))
        # next turn the LLM "heals" nothing — state must persist, not reset
        hull, shields = apply_ship_status(hull, shields, 0, 0)
        self.assertEqual((hull, shields), (25, 20))

    def test_repair_adds_up(self):
        self.assertEqual(apply_ship_status(40, 10, +10, +15), (50, 25))

    def test_clamped_to_zero_and_max(self):
        self.assertEqual(apply_ship_status(10, 5, -25, -30), (0, 0))
        self.assertEqual(apply_ship_status(95, 95, +50, +50), (HULL_MAX, SHIELDS_MAX))

    def test_hull_reaching_zero_is_destruction_signal(self):
        hull, _ = apply_ship_status(10, 50, -25, 0)
        self.assertEqual(hull, 0)  # main.py: hull <= 0 -> end_game("ship_destroyed")

    def test_invalid_delta_counts_as_no_change(self):
        self.assertEqual(apply_ship_status(42, 17, None, "bad"), (42, 17))

    def test_result_is_int(self):
        hull, shields = apply_ship_status("30", "60", -5, -5)
        self.assertEqual((hull, shields), (25, 55))
        self.assertIsInstance(hull, int)
        self.assertIsInstance(shields, int)


class TestApplySystemsOffline(unittest.TestCase):
    def test_new_systems_appended_in_order(self):
        self.assertEqual(
            apply_systems_offline(["warp drive"], ["weapons", "life support"], []),
            ["warp drive", "weapons", "life support"],
        )

    def test_restore_removes_system(self):
        self.assertEqual(
            apply_systems_offline(["warp drive", "weapons"], [], ["warp drive"]),
            ["weapons"],
        )

    def test_restore_unknown_system_is_noop(self):
        self.assertEqual(apply_systems_offline(["warp drive"], [], ["transporter"]), ["warp drive"])

    def test_duplicates_not_added(self):
        self.assertEqual(
            apply_systems_offline(["warp drive"], ["warp drive", "weapons"], []),
            ["warp drive", "weapons"],
        )

    def test_stable_order_preserved(self):
        current = ["b", "a", "c"]
        self.assertEqual(apply_systems_offline(current, ["d"], ["a"]), ["b", "c", "d"])

    def test_does_not_mutate_input(self):
        current = ["warp drive"]
        apply_systems_offline(current, ["weapons"], ["warp drive"])
        self.assertEqual(current, ["warp drive"])

    def test_repaired_then_rebroken_ends_offline(self):
        self.assertEqual(
            apply_systems_offline(["warp drive"], ["warp drive"], ["warp drive"]),
            ["warp drive"],
        )


class TestShipStateDb(unittest.TestCase):
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

    def _game_state_columns(self) -> set[str]:
        conn = db.get_db_connection()
        try:
            return {r["name"] for r in conn.execute("PRAGMA table_info(game_state)").fetchall()}
        finally:
            conn.close()

    def test_new_game_gets_default_ship_status(self):
        db.create_game({"game_id": "g1", "name": "T", "setting": "starship", "language": "ru"})
        state = db.get_game_state("g1")
        self.assertEqual(state["hull_integrity"], 100)
        self.assertEqual(state["shields"], 100)
        self.assertEqual(state["systems_offline"], [])

    def test_crew_health_column_dropped(self):
        cols = self._game_state_columns()
        self.assertNotIn("crew_health", cols)
        self.assertIn("hull_integrity", cols)
        self.assertIn("shields", cols)
        self.assertIn("systems_offline", cols)

    def test_update_roundtrips_ship_status(self):
        db.create_game({"game_id": "g2", "name": "T", "setting": "starship", "language": "ru"})
        db.update_game_state(
            2,
            "active",
            True,
            hull_integrity=35,
            shields=20,
            systems_offline=["warp drive", "life support"],
            game_id="g2",
        )
        state = db.get_game_state("g2")
        self.assertEqual(state["hull_integrity"], 35)
        self.assertEqual(state["shields"], 20)
        self.assertEqual(state["systems_offline"], ["warp drive", "life support"])

    def test_turn_advance_does_not_clobber_ship_status(self):
        db.create_game({"game_id": "g3", "name": "T", "setting": "starship", "language": "ru"})
        db.update_game_state(1, "active", True, hull_integrity=45, shields=30, systems_offline=["weapons"], game_id="g3")
        # what start/continue-game do: advance the turn without ship kwargs
        db.update_game_state(2, "active", True, game_id="g3")
        state = db.get_game_state("g3")
        self.assertEqual(state["turn"], 2)
        self.assertEqual(state["hull_integrity"], 45)
        self.assertEqual(state["shields"], 30)
        self.assertEqual(state["systems_offline"], ["weapons"])

    def test_reset_game_state_to_turn1_restores_pristine_ship(self):
        db.create_game({"game_id": "g4", "name": "T", "setting": "starship", "language": "ru"})
        db.update_game_state(3, "active", True, hull_integrity=10, shields=5, systems_offline=["warp drive"], game_id="g4")
        db.reset_game_state_to_turn1("g4")
        state = db.get_game_state("g4")
        self.assertEqual(state["turn"], 1)
        self.assertEqual(state["hull_integrity"], 100)
        self.assertEqual(state["shields"], 100)
        self.assertEqual(state["systems_offline"], [])

    def test_end_game_marks_ended_and_inactive(self):
        db.create_game({"game_id": "g5", "name": "T", "setting": "starship", "language": "ru"})
        self.assertTrue(db.is_game_active("g5"))
        db.end_game("ship_destroyed", game_id="g5")
        state = db.get_game_state("g5")
        self.assertEqual(state["status"], "ship_destroyed")
        self.assertFalse(state["ship_alive"])
        self.assertFalse(db.is_game_active("g5"))


class TestCombinedOutcomePromptShipStatus(unittest.TestCase):
    def test_prompt_carries_current_ship_status_and_delta_instruction(self):
        _, user = build_combined_outcome_prompts(
            LANGUAGE_RU,
            setting="s",
            conflict="c",
            narrative="n",
            previous_summary="",
            mission_text="m",
            ship_status_text="Hull integrity: 35/100\nShields: 20/100\nSystems offline: warp drive",
            decisions_text="d",
            roster_text="r",
            use_vs=False,
            vs_k=1,
        )
        self.assertIn("Hull integrity: 35/100", user)
        self.assertIn("ship_hull_change", user)
        self.assertIn("ship_shields_change", user)
        self.assertIn("systems_taken_offline", user)
        self.assertIn("systems_restored", user)
        self.assertNotIn("ship_destroyed — true/false", user)


if __name__ == "__main__":
    unittest.main()
