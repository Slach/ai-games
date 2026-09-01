"""Regression test for the duplicate onboarding decision race.

History: a player tapping the inline buttons repeatedly (while the next
question was still being generated, 30-60s) spawned N parallel generations —
e.g. "Ситуация 7" was delivered four times. The fix is
reserve_onboarding_slot(): a compare-and-set UPDATE that only advances the
counter if it still matches the expected value. SQLite serialises the UPDATE,
so at most one concurrent caller wins.

Since onboarding 2.0 the counter tracks character rejections ("no" presses):
each reroll POST advances it once, and reaching ONBOARDING_MAX_REROLLS force-
assigns the next generated character. The same CAS primitive guards against
duplicate reroll presses racing while a proposal is still generating.

These tests exercise the DB primitive directly (deterministic, no LLM/HTTP).
The FastAPI handlers in main.py additionally reject stale requests with 409
before reaching the CAS.
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
        # The reported-bug scenario: two submissions both saw the same counter
        # value in their in-memory snapshot. Only the CAS winner advances the
        # counter; the loser must be told it lost.
        self.assertTrue(db.reserve_onboarding_slot(self.session_id, expected_current_question=0))
        self.assertFalse(db.reserve_onboarding_slot(self.session_id, expected_current_question=0))
        after = db.get_onboarding_session(self.session_id)
        self.assertIsNotNone(after)
        self.assertEqual(after["current_question"], 1)

    def test_stale_expected_value_loses(self):
        # A late/duplicate request arriving after the counter already advanced
        # must not advance it again.
        self.assertTrue(db.reserve_onboarding_slot(self.session_id, expected_current_question=0))
        self.assertFalse(db.reserve_onboarding_slot(self.session_id, expected_current_question=0))

    def test_unknown_session_loses(self):
        self.assertFalse(db.reserve_onboarding_slot("no_such_session", expected_current_question=0))

    def test_proposal_roundtrip(self):
        # The proposal dict (role/species/gender/flavour/avatar) survives a
        # write/read cycle so /reroll and /complete can both load it.
        proposal = {
            "role_key": "pilot",
            "role": "Пилот",
            "species": "Человек",
            "gender": "Женский",
            "avatar_url": None,
            "personality_traits": ["смелый", "осторожный", "ироничный"],
            "past_roles": ["pilot"],
            "past_species": ["human"],
        }
        db.update_onboarding_session(
            self.session_id,
            0,
            {-1: "game1", -2: "Alice"},
            False,
            "ru",
            proposal,
        )
        after = db.get_onboarding_session(self.session_id)
        self.assertIsNotNone(after)
        self.assertEqual(after["proposal"], proposal)

    def test_complete_via_finalize_update(self):
        # Forced finalization: the background task marks the session completed
        # together with the accepted proposal in a single update.
        proposal = {"role_key": "pilot", "past_roles": ["pilot"], "past_species": ["human"]}
        db.update_onboarding_session(
            self.session_id,
            3,
            {-1: "game1"},
            True,
            "ru",
            proposal,
        )
        after = db.get_onboarding_session(self.session_id)
        self.assertIsNotNone(after)
        self.assertTrue(after["completed"])
        self.assertEqual(after["proposal"]["role_key"], "pilot")


if __name__ == "__main__":
    unittest.main()
