"""Tests for wound severity persistence and wound-driven action counts."""

import os
import sys
import tempfile
import logging
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
from game_server import _actions_for_wound  # noqa: E402

logger = logging.getLogger(__name__)


def _make_player(player_id, game_id, role="Pilot", name="Test"):
    db.create_player_profile(
        {
            "player_id": player_id,
            "role": role,
            "game_id": game_id,
            "player_name": name,
        }
    )


def _make_npc(npc_key, game_id, role="Engineer", name="NPC Eng"):
    db.create_npc_profile(
        {
            "npc_key": npc_key,
            "role_key": role,
            "npc_name": name,
            "role": role,
            "game_id": game_id,
        }
    )


class TestWoundPersistence(unittest.TestCase):
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

    def test_migration_adds_wound_columns(self):
        # Columns exist after init_db.
        conn = db.get_db_connection()
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(player_profiles)").fetchall()}
        cols_npc = {row["name"] for row in conn.execute("PRAGMA table_info(npc_profiles)").fetchall()}
        conn.close()
        self.assertIn("wound_severity", cols)
        self.assertIn("wound_severity", cols_npc)

    def test_player_wound_default_null(self):
        _make_player(1, "g1")
        p = db.get_player_profile(1)
        self.assertIsNone(p["wound_severity"])

    def test_set_player_wound_severity_roundtrip(self):
        _make_player(2, "g1", role="Medic", name="Doc")
        self.assertTrue(db.set_player_wound_severity(2, "g1", "critical"))
        self.assertEqual(db.get_player_profile(2)["wound_severity"], "critical")
        # None heals fully.
        self.assertTrue(db.set_player_wound_severity(2, "g1", None))
        self.assertIsNone(db.get_player_profile(2)["wound_severity"])

    def test_set_npc_wound_severity_roundtrip(self):
        _make_npc("npc_x", "g1")
        self.assertTrue(db.set_npc_wound_severity("npc_x", "moderate"))
        self.assertEqual(db.get_npc_profile("npc_x")["wound_severity"], "moderate")
        self.assertTrue(db.set_npc_wound_severity("npc_x", None))
        self.assertIsNone(db.get_npc_profile("npc_x")["wound_severity"])

    def test_wound_does_not_affect_alive_count(self):
        """A wounded member is still alive (not dead)."""
        _make_player(3, "g1")
        _make_player(4, "g1")
        db.set_player_wound_severity(3, "g1", "critical")
        # Both players count as alive.
        self.assertEqual(len(db.get_live_players("g1")), 2)
        self.assertEqual(len(db.get_dead_players("g1")), 0)


class TestActionsForWound(unittest.TestCase):
    def test_healthy_full_set(self):
        # default env: 2 progress, 2 injury, 1 fatal = 5
        total, p, i, f = _actions_for_wound(None, 2, 2, 1)
        self.assertEqual((total, p, i, f), (5, 2, 2, 1))

    def test_minor_removes_one(self):
        total, p, i, f = _actions_for_wound("minor", 2, 2, 1)
        self.assertEqual(total, 4)
        # fatal trimmed first.
        self.assertEqual(f, 0)
        self.assertEqual(p, 2)
        self.assertEqual(i, 2)

    def test_moderate_removes_two(self):
        total, p, i, f = _actions_for_wound("moderate", 2, 2, 1)
        self.assertEqual(total, 3)
        # fatal (1) then one injury trimmed; progress preserved.
        self.assertEqual(f, 0)
        self.assertEqual(i, 1)
        self.assertEqual(p, 2)

    def test_critical_never_below_one(self):
        total, p, i, f = _actions_for_wound("critical", 2, 2, 1)
        # fatal (1) + both injury (2) = 3 trimmed; progress preserved at 2.
        self.assertEqual(total, 2)
        self.assertEqual(p, 2)
        self.assertEqual(i, 0)
        self.assertEqual(f, 0)
        self.assertEqual(total, p + i + f)

    def test_unknown_severity_treated_as_healthy(self):
        total, p, i, f = _actions_for_wound("garbage", 2, 2, 1)
        self.assertEqual((total, p, i, f), (5, 2, 2, 1))


if __name__ == "__main__":
    unittest.main()
