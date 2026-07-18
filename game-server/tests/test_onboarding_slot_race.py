"""Regression test for the duplicate onboarding answer race.

Bug: a player tapping the inline buttons repeatedly (while the species/gender
question for the previous answer was still being generated, 30-60s) spawned N
parallel generations of the next question — e.g. "Ситуация 7" was delivered
four times. The server advanced current_question and built the next question
unconditionally on every /answer, so concurrent submissions all "won".

The fix is reserve_onboarding_slot(): a compare-and-set UPDATE that only
advances current_question if it still matches the expected value. SQLite
serialises the UPDATE, so at most one concurrent caller wins.

These tests exercise the DB primitive directly (deterministic, no LLM/HTTP).
The FastAPI handler in main.py additionally rejects stale question_ids with
409 before reaching the CAS; that guard is covered by the question_id ==
current_question+1 invariant the handler asserts.
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


class TestOnboardingSlotRace(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()
        self.session = db.create_onboarding_session(
            player_id=535628479,
            language="ru",
            shuffle_seed=1,
            questions=[{"id": 1, "text": "q1", "options": []}],
        )
        self.session_id = self.session["session_id"]

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB: %s", self._tmp.name, exc_info=True)

    def test_first_reservation_advances_counter(self):
        self.assertEqual(self.session["current_question"], 0)
        won = db.reserve_onboarding_slot(self.session_id, expected_current_question=0)
        self.assertTrue(won)
        after = db.get_onboarding_session(self.session_id)
        self.assertIsNotNone(after)
        self.assertEqual(after["current_question"], 1)

    def test_concurrent_reservation_loses(self):
        # The reported-bug scenario: two submissions for the same question both
        # saw current_question == 0 in their in-memory snapshot. Only the CAS
        # winner advances the counter; the loser must be told it lost.
        self.assertTrue(db.reserve_onboarding_slot(self.session_id, expected_current_question=0))
        self.assertFalse(db.reserve_onboarding_slot(self.session_id, expected_current_question=0))
        after = db.get_onboarding_session(self.session_id)
        self.assertIsNotNone(after)
        self.assertEqual(after["current_question"], 1)

    def test_stale_expected_value_loses(self):
        # A late/duplicate answer arriving after the counter already advanced
        # must not advance it again.
        self.assertTrue(db.reserve_onboarding_slot(self.session_id, expected_current_question=0))
        self.assertFalse(db.reserve_onboarding_slot(self.session_id, expected_current_question=0))

    def test_unknown_session_loses(self):
        self.assertFalse(db.reserve_onboarding_slot("no_such_session", expected_current_question=0))


if __name__ == "__main__":
    unittest.main()
