"""DB-level tests for mission persistence and read-time normalization."""

import os
import sys
import tempfile
import logging
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

logger = logging.getLogger(__name__)


class TestMissionPersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB: %s", self._tmp.name)

    def _raw_mission(self):
        return {
            "name": "Test",
            "description": "d",
            "objectives": [
                {"stage": 1, "name": "A", "description": "a", "success_threshold": 3},
                {"stage": 2, "name": "B", "description": "b", "success_threshold": 3},
            ],
        }

    def test_create_derives_total_stages_from_objectives(self):
        result = db.create_mission(self._raw_mission(), "g1")
        assert result is not None
        self.assertEqual(result["total_stages"], 2)
        self.assertEqual(result["current_stage"], 1)
        self.assertFalse(result["completed"])

    def test_get_mission_normalizes_stale_row(self):
        # Simulate the legacy bug: write a row with stale current/total,
        # then confirm get_mission repairs it via normalize_mission.
        raw = self._raw_mission()
        raw["current_stage"] = 0
        raw["total_stages"] = 1
        raw["stage_progress"] = {"1": 5, "2": 5}
        db.create_mission(raw, "g2")
        got = db.get_mission(None, "g2")
        assert got is not None
        self.assertEqual(got["total_stages"], 2)
        self.assertEqual(got["current_stage"], 3)  # both stages >= threshold
        self.assertTrue(got["completed"])

    def test_archetype_and_seeds_round_trip(self):
        raw = self._raw_mission()
        raw["archetype"] = "first_contact"
        raw["seeds"] = {"setting": "orbital station", "complication": "pirates"}
        db.create_mission(raw, "g3")
        got = db.get_mission(None, "g3")
        assert got is not None
        self.assertEqual(got["archetype"], "first_contact")
        self.assertEqual(got["seeds"]["complication"], "pirates")


class TestLastDeathDay(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB: %s", self._tmp.name)

    def test_get_returns_zero_default_and_set_persists(self):
        state = db.get_game_state("gd1")
        self.assertEqual(state["last_death_day"], 0)
        db.set_last_death_day("gd1", 7)
        self.assertEqual(db.get_game_state("gd1")["last_death_day"], 7)


if __name__ == "__main__":
    unittest.main()
