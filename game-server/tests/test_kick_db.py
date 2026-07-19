"""DB-level tests for game-scoped player kicks."""

import os
import sys
import tempfile
import logging
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

logger = logging.getLogger(__name__)


class TestGameScopedKicks(unittest.TestCase):
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

    def test_kick_is_scoped_per_game(self):
        """A kick recorded in one game must not affect another game."""
        db.record_kick(111, "npc_captain_game_a", "Player reset", game_id="game_a")

        self.assertTrue(db.is_player_kicked(111, "game_a"))
        # Same player, different game → not kicked
        self.assertFalse(db.is_player_kicked(111, "game_b"))

    def test_player_with_legacy_kick_not_kicked_in_new_game(self):
        """Reproduces the original bug: a player reset from previous games must
        still receive briefings in a brand-new game."""
        db.record_kick(222, "npc_science_officer_default_game", "Player reset", game_id="default_game")
        db.record_kick(222, "npc_captain_epl2yq", "Player reset", game_id="epl2yq")

        self.assertFalse(db.is_player_kicked(222, "c39q8a"))

    def test_kick_round_trips_game_id(self):
        result = db.record_kick(333, "npc_x_g1", "bot was blocked", game_id="g1")
        self.assertEqual(result["game_id"], "g1")
        kicked = db.get_kicked_players()
        matching = [k for k in kicked if k["kicked_player_id"] == 333]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["game_id"], "g1")

    def test_clear_kicks_when_player_returns_to_same_game(self):
        """Reproduces the player 281412419 bug: after /reset (recorded in
        player_kicks) the player re-onboards into the SAME game. Stale kick rows
        would otherwise keep is_player_kicked() True and exclude them from
        briefing pushes (turn 2 never delivered)."""
        db.record_kick(281412419, "npc_security_chief_default_game", "Player reset", game_id="default_game")
        db.record_kick(281412419, "npc_chief_engineer_default_game", "Player reset", game_id="default_game")

        self.assertTrue(db.is_player_kicked(281412419, "default_game"))

        deleted = db.clear_kicks_for_returning_player(281412419, "default_game")
        self.assertEqual(deleted, 2)
        self.assertFalse(db.is_player_kicked(281412419, "default_game"))

    def test_clear_kicks_scoped_to_game(self):
        """Clearing kicks in one game must leave the player kicked in another."""
        db.record_kick(444, "npc_pilot_g1", "reset", game_id="g1")
        db.record_kick(444, "npc_pilot_g2", "reset", game_id="g2")

        deleted = db.clear_kicks_for_returning_player(444, "g1")
        self.assertEqual(deleted, 1)
        self.assertFalse(db.is_player_kicked(444, "g1"))
        self.assertTrue(db.is_player_kicked(444, "g2"))


if __name__ == "__main__":
    unittest.main()
