"""Regression tests for the re-onboarding policy (should_reset_profile_for_reonboarding).

The server-side /onboarding/start must allow re-onboarding (deleting the old
profile) when:
  - the player joins a DIFFERENT game than their current profile's game, OR
  - their previous game has ended.
It must block re-onboarding into the SAME still-active game — both while alive
(prevents accidental loss of an in-progress character) and when dead / spectator
/ already replaced by an NPC. A dead player must not be revived into the same
active game: the current turn's briefings were generated without them, so they
end up stuck as "waiting" forever.
"""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

logger = logging.getLogger(__name__)

PLAYER_ID = 1


def _profile(game_id: str, *, is_dead: bool = False, is_spectator: bool = False) -> dict:
    return {
        "player_id": PLAYER_ID,
        "game_id": game_id,
        "is_dead": is_dead,
        "is_spectator": is_spectator,
    }


def _make_replacement_npc(game_id: str, *, is_active: bool = True) -> None:
    """Insert an NPC row marking PLAYER_ID as having played/died in ``game_id``."""
    db.create_npc_profile(
        {
            "npc_key": f"npc_test_{game_id}",
            "role_key": "captain",
            "npc_name": "Ghost",
            "role": "Captain",
            "game_id": game_id,
            "is_active": is_active,
            "replaces_player_id": PLAYER_ID,
        }
    )


class TestReonboardingPolicy(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()
        for gid in ("gameA", "gameB"):
            db.create_game(
                {
                    "game_id": gid,
                    "name": "Test " + gid,
                    "setting": "starship",
                    "language": "ru",
                }
            )

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB: %s", self._tmp.name, exc_info=True)

    def test_blocks_same_active_game_alive(self):
        allow, reason = db.should_reset_profile_for_reonboarding(_profile("gameA"), "gameA")
        self.assertFalse(allow)
        self.assertEqual(reason, "active_same_game")

    def test_allows_different_active_game(self):
        # Player in one game, joining another — re-onboarding allowed.
        allow, reason = db.should_reset_profile_for_reonboarding(_profile("gameA"), "gameB")
        self.assertTrue(allow)
        self.assertEqual(reason, "different_game")

    def test_allows_same_game_after_it_ended(self):
        db.end_game("mission_complete", game_id="gameA")
        allow, reason = db.should_reset_profile_for_reonboarding(_profile("gameA"), "gameA")
        self.assertTrue(allow)
        self.assertEqual(reason, "ended")

    def test_blocks_dead_player_same_active_game(self):
        # A dead player must NOT be revived into the same still-active game.
        allow, reason = db.should_reset_profile_for_reonboarding(_profile("gameA", is_dead=True), "gameA")
        self.assertFalse(allow)
        self.assertEqual(reason, "already_played_same_game")

    def test_blocks_spectator_same_active_game(self):
        allow, reason = db.should_reset_profile_for_reonboarding(
            _profile("gameA", is_spectator=True), "gameA"
        )
        self.assertFalse(allow)
        self.assertEqual(reason, "already_played_same_game")

    def test_blocks_replaced_player_same_active_game(self):
        # Player profile is clean (not dead), but an NPC was created from them
        # in this game — they already played and must not re-onboard.
        _make_replacement_npc("gameA")
        allow, reason = db.should_reset_profile_for_reonboarding(_profile("gameA"), "gameA")
        self.assertFalse(allow)
        self.assertEqual(reason, "already_played_same_game")

    def test_allows_dead_player_different_active_game(self):
        # Dead in gameA, joining gameB — fine, different game.
        allow, reason = db.should_reset_profile_for_reonboarding(
            _profile("gameA", is_dead=True), "gameB"
        )
        self.assertTrue(allow)
        self.assertEqual(reason, "different_game")

    def test_allows_replaced_player_after_game_ended(self):
        _make_replacement_npc("gameA")
        db.end_game("mission_complete", game_id="gameA")
        allow, reason = db.should_reset_profile_for_reonboarding(_profile("gameA"), "gameA")
        self.assertTrue(allow)
        self.assertEqual(reason, "ended")

    def test_different_game_takes_priority_over_ended(self):
        db.end_game("mission_complete", game_id="gameA")
        allow, reason = db.should_reset_profile_for_reonboarding(_profile("gameA"), "gameB")
        self.assertTrue(allow)
        self.assertEqual(reason, "different_game")


class TestPlayerHasPlayedInGame(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()
        db.create_game(
            {"game_id": "gameA", "name": "Test", "setting": "starship", "language": "ru"}
        )

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB: %s", self._tmp.name, exc_info=True)

    def test_false_when_no_npc(self):
        self.assertFalse(db.player_has_played_in_game(PLAYER_ID, "gameA"))

    def test_true_when_active_replacement_npc(self):
        _make_replacement_npc("gameA", is_active=True)
        self.assertTrue(db.player_has_played_in_game(PLAYER_ID, "gameA"))

    def test_true_when_inactive_replacement_npc(self):
        # Deactivation only flips is_active; the row remains as a history marker.
        _make_replacement_npc("gameA", is_active=False)
        self.assertTrue(db.player_has_played_in_game(PLAYER_ID, "gameA"))

    def test_false_for_different_game(self):
        _make_replacement_npc("gameA")
        self.assertFalse(db.player_has_played_in_game(PLAYER_ID, "gameB"))


if __name__ == "__main__":
    unittest.main()
