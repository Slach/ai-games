"""Regression tests for the re-onboarding policy (should_reset_profile_for_reonboarding).

Bug: a returning player who already had a profile in a finished/other game was
shown "welcome back" into that old game when clicking a deep link to a NEW game,
because the bot ignored the deep-link game_id whenever any profile existed.

The server-side /onboarding/start must allow re-onboarding (deleting the old
profile) when:
  - the player joins a DIFFERENT game than their current profile's game, OR
  - their previous game has ended, OR
  - they are dead / a spectator.
It must block only when re-onboarding into the SAME still-active game while alive.
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


def _profile(game_id: str, *, is_dead: bool = False, is_spectator: bool = False) -> dict:
    return {
        "player_id": 1,
        "game_id": game_id,
        "is_dead": is_dead,
        "is_spectator": is_spectator,
    }


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
        # The reported-bug scenario: player in one game, deep-linked into another.
        allow, reason = db.should_reset_profile_for_reonboarding(_profile("gameA"), "gameB")
        self.assertTrue(allow)
        self.assertEqual(reason, "different_game")

    def test_allows_same_game_after_it_ended(self):
        # default_game was finished -> player must be able to re-onboard into it.
        db.end_game("mission_complete", game_id="gameA")
        allow, reason = db.should_reset_profile_for_reonboarding(_profile("gameA"), "gameA")
        self.assertTrue(allow)
        self.assertEqual(reason, "ended")

    def test_allows_dead_player_same_active_game(self):
        allow, reason = db.should_reset_profile_for_reonboarding(_profile("gameA", is_dead=True), "gameA")
        self.assertTrue(allow)
        self.assertEqual(reason, "dead_spectator")

    def test_different_game_takes_priority_over_ended(self):
        db.end_game("mission_complete", game_id="gameA")
        allow, reason = db.should_reset_profile_for_reonboarding(_profile("gameA"), "gameB")
        self.assertTrue(allow)
        self.assertEqual(reason, "different_game")


if __name__ == "__main__":
    unittest.main()
