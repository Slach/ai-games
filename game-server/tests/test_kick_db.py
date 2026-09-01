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
        db.record_kick(111, "Player reset", game_id="game_a")

        self.assertTrue(db.is_player_kicked(111, "game_a"))
        # Same player, different game → not kicked
        self.assertFalse(db.is_player_kicked(111, "game_b"))

    def test_player_with_legacy_kick_not_kicked_in_new_game(self):
        """Reproduces the original bug: a player reset from previous games must
        still receive briefings in a brand-new game."""
        db.record_kick(222, "Player reset", game_id="default_game")
        db.record_kick(222, "Player reset", game_id="epl2yq")

        self.assertFalse(db.is_player_kicked(222, "c39q8a"))

    def test_kick_round_trips_game_id(self):
        result = db.record_kick(333, "bot was blocked", game_id="g1")
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
        db.record_kick(281412419, "Player reset", game_id="default_game")
        db.record_kick(281412419, "Player reset", game_id="default_game")

        self.assertTrue(db.is_player_kicked(281412419, "default_game"))

        deleted = db.clear_kicks_for_returning_player(281412419, "default_game")
        self.assertEqual(deleted, 2)
        self.assertFalse(db.is_player_kicked(281412419, "default_game"))

    def test_clear_kicks_scoped_to_game(self):
        """Clearing kicks in one game must leave the player kicked in another."""
        db.record_kick(444, "reset", game_id="g1")
        db.record_kick(444, "reset", game_id="g2")

        deleted = db.clear_kicks_for_returning_player(444, "g1")
        self.assertEqual(deleted, 1)
        self.assertFalse(db.is_player_kicked(444, "g1"))
        self.assertTrue(db.is_player_kicked(444, "g2"))


class TestKickDoesNotCreateNpc(unittest.TestCase):
    """Kicking a player must leave their seat EMPTY — no NPC replacement.

    Mirrors /admin/kick-player's new behavior (main.py): release_role +
    record_kick + leave_game. Previously _replace_player_with_npc created an
    active NPC on the vacated role, so the crew could never shrink and
    crew_wiped was unreachable.
    """

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

    def test_kick_leaves_seat_empty_and_no_npc(self):
        game_id = "g1"
        db.create_game({"game_id": game_id, "name": "T", "setting": "starship", "language": "ru"})
        db.create_player_profile(
            {"player_id": 555, "role": "Пилот", "game_id": game_id, "player_name": "Kicked"}
        )
        db.take_role("pilot", 555, game_id)
        active_before = db.get_all_active_npcs(game_id)
        self.assertEqual(active_before, [])

        # What /admin/kick-player does now (no _replace_player_with_npc).
        db.release_role("pilot", game_id)
        db.record_kick(555, "Inactive", game_id=game_id)
        db.leave_game(555)

        # No NPC replacement appeared for the vacated seat.
        self.assertEqual(db.get_all_active_npcs(game_id), [])
        self.assertEqual(db.get_all_npcs(game_id), [])
        # The seat is empty, not held by the kicked player.
        roles = {r["role_key"]: r for r in db.get_all_roles(game_id, language="ru")}
        self.assertNotIn("taken_by", roles["pilot"])
        # The player is out of the game and recorded as kicked.
        self.assertIsNone(db.get_player_profile(555)["game_id"])
        self.assertTrue(db.is_player_kicked(555, game_id))

    def test_dead_player_still_marks_dead_without_npc(self):
        """Player death (mark_player_dead) is unrelated to kicking: the player
        stays registered as a dead spectator and no NPC takes the seat."""
        game_id = "g2"
        db.create_game({"game_id": game_id, "name": "T", "setting": "starship", "language": "ru"})
        db.create_player_profile(
            {"player_id": 666, "role": "Пилот", "game_id": game_id, "player_name": "Doomed"}
        )
        db.take_role("pilot", 666, game_id)

        db.mark_player_dead(666, game_id)

        self.assertEqual(db.get_all_active_npcs(game_id), [])
        self.assertIn(666, db.get_dead_players(game_id))
        self.assertNotIn(666, db.get_live_players(game_id))


if __name__ == "__main__":
    unittest.main()
