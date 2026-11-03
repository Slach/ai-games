"""Regression tests: onboarding must not join a player into an ended game.

History (2026-11-03, game khfan2): a player was invited on Oct 31, got a
character proposal card, and abandoned onboarding. The game ended Nov 3
at 07:46; two hours later the player returned, /start restored the stale
proposal, and the "accept" press created a profile in the finished game
and broadcast "Новый член экипажа" to its players. The fix: refuse to
start or complete onboarding for a game whose games.status is 'ended'
(unless the profile already exists — that player joined while it lived).
"""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# main.py hardcodes a daily /app/logs file handler at import time — that
# directory only exists inside the container. Redirect file logging to a
# temp file for the duration of the import so the module loads on the host.
_orig_file_handler = logging.FileHandler


class _TempFileHandler(logging.FileHandler):
    def __init__(self, filename, *args, **kwargs):
        if str(filename).startswith("/app/logs/"):
            filename = os.path.join(tempfile.mkdtemp(), os.path.basename(filename))
        super().__init__(filename, *args, **kwargs)


logging.FileHandler = _TempFileHandler
import database as db  # noqa: E402
import main  # noqa: E402

logging.FileHandler = _orig_file_handler

logger = logging.getLogger(__name__)

PLAYER_ID = 703779807
ENDED_GAME = "khfan2"

PROPOSAL = {
    "role_key": "pilot",
    "past_roles": ["pilot"],
    "past_species": ["human"],
}


def _make_game(game_id: str, *, ended: bool) -> None:
    db.create_game(
        {
            "game_id": game_id,
            "name": "Test Game",
            "setting": "starship",
            "language": "ru",
        }
    )
    if ended:
        db.end_game("mission_complete", game_id=game_id)


def _make_session(game_id: str) -> str:
    session = db.create_onboarding_session(player_id=PLAYER_ID, language="ru")
    db.update_onboarding_session(
        session["session_id"],
        0,
        {-1: game_id, -2: "Jimmy Pop"},
        False,
        "ru",
        PROPOSAL,
    )
    return session["session_id"]


class OnboardingEndedGameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()
        _make_game(ENDED_GAME, ended=True)

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB: %s", self._tmp.name, exc_info=True)

    async def test_start_onboarding_rejects_ended_game(self):
        # The reported-bug entry point: a deep link to an ended game must not
        # even open an onboarding session (no LLM/ComfyUI generations).
        req = main.StartOnboardingRequest(
            player_id=PLAYER_ID,
            game_id=ENDED_GAME,
            player_name="Jimmy Pop",
            language="ru",
        )
        with self.assertRaises(main.HTTPException) as ctx:
            await main.start_onboarding(req)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "Game ended")
        self.assertIsNone(db.get_player_profile(PLAYER_ID))

    async def test_complete_rejects_ended_game_without_profile(self):
        # The reported-bug scenario: a stale session + old card accepted
        # long after the game ended must not create a profile.
        session_id = _make_session(ENDED_GAME)
        with self.assertRaises(main.HTTPException) as ctx:
            await main.complete_onboarding(session_id)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "Game ended")
        self.assertIsNone(db.get_player_profile(PLAYER_ID))

    async def test_get_onboarding_reports_game_status(self):
        # The bot's /start restore branch drops sessions whose game ended;
        # it relies on game_status in this payload.
        session_id = _make_session(ENDED_GAME)
        status = await main.get_onboarding_status(session_id)
        self.assertEqual(status["game_status"], "ended")

    async def test_complete_allows_existing_profile_after_end(self):
        # The player joined while the game was alive; a later re-finalize
        # (restore path) must keep working after the game ended.
        db.create_player_profile(
            {
                "player_id": PLAYER_ID,
                "player_name": "Jimmy Pop",
                "role": "Пилот",
                "game_id": ENDED_GAME,
            }
        )
        session_id = _make_session(ENDED_GAME)
        result = await main.complete_onboarding(session_id)
        self.assertEqual(result["status"], "completed")


if __name__ == "__main__":
    unittest.main()
