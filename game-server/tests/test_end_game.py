"""Regression test: end_game() must mark the game as ended in the `games` table.

Historically end_game() only updated game_state.status, leaving games.status at its
creation default 'active' forever. Since /admin/list-games (and thus Telegram /gm_list)
reads games.status, finished games kept showing up as active. game_state.status is the
live per-turn state; games.status is the lifecycle flag read by list/filter queries, so
end_game() must keep both in sync.
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


class TestEndGameMarksGamesTable(unittest.TestCase):
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
        db.create_game(
            {
                "game_id": game_id,
                "name": "Test Game",
                "setting": "starship",
                "language": "ru",
            }
        )

    def test_end_game_marks_games_status_ended(self):
        self._make_game("g1")
        before = db.get_game("g1")
        assert before is not None
        self.assertEqual(before["status"], "active")

        db.end_game("mission_complete", "g1")

        after = db.get_game("g1")
        assert after is not None
        self.assertEqual(after["status"], "ended")

    def test_end_game_sets_game_state_reason(self):
        self._make_game("g2")
        db.end_game("ship_destroyed", "g2")

        state = db.get_game_state("g2")
        self.assertEqual(state["status"], "ship_destroyed")
        self.assertFalse(state["ship_alive"])


if __name__ == "__main__":
    unittest.main()
