"""DB-level tests for player_briefings persistence, incl. personal_title resume support."""

import os
import sys
import tempfile
import logging
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

logger = logging.getLogger(__name__)


class TestBriefingPersistence(unittest.TestCase):
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

    def _player_briefing(self, turn=1, player_id=100, personal_title="T1 — Captain's log"):
        return {
            "turn": turn,
            "player_id": player_id,
            "npc_key": None,
            "is_npc": False,
            "briefing": "Situation critical",
            "choices": [{"id": "action_1", "text": "Fire"}],
            "selected_action_id": None,
            "choice_rationale": "",
            "consequence_result": {},
            "personal_title": personal_title,
        }

    def test_personal_title_round_trips(self):
        db.save_player_briefing(self._player_briefing(), "g1")
        got = db.get_player_briefing(1, 100, "g1")
        if got is None:
            self.fail("expected saved briefing")
        self.assertEqual(got["personal_title"], "T1 — Captain's log")

    def test_personal_title_defaults_empty_when_omitted(self):
        raw = self._player_briefing()
        del raw["personal_title"]
        db.save_player_briefing(raw, "g1")
        got = db.get_player_briefing(1, 100, "g1")
        if got is None:
            self.fail("expected saved briefing")
        self.assertEqual(got["personal_title"], "")

    def test_briefing_upsert_replaces_not_duplicates(self):
        """INSERT OR REPLACE must overwrite the same (turn, player, game) briefing.

        Resume relies on this: re-saving a briefing for an existing participant
        updates it instead of creating a duplicate row.
        """
        db.save_player_briefing(self._player_briefing(personal_title="first"), "g1")
        db.save_player_briefing(self._player_briefing(personal_title="second"), "g1")
        all_for_turn = db.get_all_briefings_for_turn(1, "g1")
        player_rows = [b for b in all_for_turn if b.get("player_id") == 100]
        self.assertEqual(len(player_rows), 1)
        self.assertEqual(player_rows[0]["personal_title"], "second")


if __name__ == "__main__":
    unittest.main()
