"""Tests for push_server.py: onboarding-ready delivery, helpers, HTTP handler,
and the push-queue startup retry of terminal content (database.py).

The delivery function under test (_deliver_onboarding_ready) talks to three
async boundaries — game-server HTTP (splash lookup + image downloads), the
Telegram Bot API, and player_store. All three are replaced with fakes; the
character-card text is validated against the real language templates.
"""

import asyncio
import json
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup  # noqa: E402
from aiohttp import web  # noqa: E402

import push_server  # noqa: E402
from database import (  # noqa: E402
    DB_PATH,
    get_pending_push_messages,
    init_db,
    insert_push_message,
    mark_push_failed,
    reset_failed_terminal_push_messages,
)
from language import LANGUAGE_EN, get_onboarding  # noqa: E402

PLAYER_ID = 123
GAME_ID = "g1"
SESSION_ID = "s1"
SPLASH_URL = "http://fake/splash.png"
AVATAR_URL = "http://fake/avatar.png"

PROPOSAL = {
    "role": "Xenobiologist",
    "species": "Betazoid",
    "gender": "female",
    "role_description": "Studies newly discovered lifeforms.",
    "personality_traits": ["curious", "calm"],
    "avatar_url": AVATAR_URL,
}


def _payload(**overrides) -> dict:
    payload = {
        "player_id": PLAYER_ID,
        "game_id": GAME_ID,
        "session_id": SESSION_ID,
        "language": LANGUAGE_EN,
        "proposal": dict(PROPOSAL),
        "game_title": "",
        "welcome_message": "",
        "final": False,
    }
    payload.update(overrides)
    return payload


class _FakeResponse:
    """Stands in for an aiohttp response context manager."""

    def __init__(self, status: int = 200, json_data=None, body: bytes = b""):
        self.status = status
        self._json_data = json_data
        self._body = body

    async def json(self):
        return self._json_data

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeHttpSession:
    """Stands in for aiohttp.ClientSession: routes GETs by URL substring."""

    def __init__(self, routes: list[tuple[str, _FakeResponse]] | None = None):
        self._routes = routes or []
        self.requests: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs) -> _FakeResponse:
        self.requests.append((url, kwargs))
        for fragment, response in self._routes:
            if fragment in url:
                return response
        return _FakeResponse(status=404)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeBot:
    """Records send_photo / send_message calls; can raise from either."""

    def __init__(self):
        self.photos: list[dict] = []
        self.messages: list[dict] = []
        self.photo_error: Exception | None = None
        self.message_error: Exception | None = None

    async def send_photo(self, chat_id=None, photo=None, caption=None, parse_mode=None, reply_markup=None):
        if self.photo_error is not None:
            raise self.photo_error
        self.photos.append(
            {
                "chat_id": chat_id,
                "photo": photo,
                "caption": caption,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )

    async def send_message(self, chat_id=None, text=None, parse_mode=None, reply_markup=None):
        if self.message_error is not None:
            raise self.message_error
        self.messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )


class DeliverOnboardingReadyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.bot = _FakeBot()
        push_server._blocked_players.clear()
        patcher_state = mock.patch("player_store.update_player_state")
        self.update_state = patcher_state.start()
        self.addCleanup(patcher_state.stop)
        patcher_completion = mock.patch.object(push_server, "_onboarding_completion_fn", None)
        patcher_completion.start()
        self.addCleanup(patcher_completion.stop)

    async def _deliver(self, payload: dict, session: _FakeHttpSession) -> bool:
        with mock.patch.object(push_server.aiohttp, "ClientSession", lambda: session):
            return await push_server._deliver_onboarding_ready(payload, self.bot)

    def _expected_card(self, final: bool = False, **proposal_overrides) -> str:
        proposal = {**PROPOSAL, **proposal_overrides}
        key = "character_card_forced" if final else "character_card"
        return get_onboarding(LANGUAGE_EN)[key].format(
            role=proposal["role"],
            species=proposal["species"],
            gender=proposal["gender"],
            role_description=proposal["role_description"],
            traits="\n- ".join(proposal["personality_traits"]),
        )

    def _assert_card_keyboard(self, reply_markup) -> None:
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        buttons = reply_markup.inline_keyboard[0]
        self.assertEqual(
            [b.callback_data for b in buttons],
            [f"onb_dec:{SESSION_ID}:yes", f"onb_dec:{SESSION_ID}:no"],
        )

    async def test_first_proposal_sends_splash_with_welcome_then_card(self):
        session = _FakeHttpSession(
            [
                ("splash-image", _FakeResponse(json_data={"image_url": SPLASH_URL})),
                (SPLASH_URL, _FakeResponse(body=b"SPLASH")),
                ("avatar.png", _FakeResponse(body=b"AVATAR")),
            ]
        )
        ok = await self._deliver(
            _payload(game_title="Terra-7", welcome_message="Welcome aboard!"), session
        )
        self.assertTrue(ok)

        # Splash fetched once, scoped to the game
        splash_requests = [u for u, _ in session.requests if "splash-image" in u]
        self.assertEqual(splash_requests, [f"{push_server.GAME_SERVER_URL}/content/splash-image"])
        self.assertEqual(session.requests[0][1]["params"], {"game_id": GAME_ID})

        # Photo 1: splash with the welcome caption; Photo 2: character card
        self.assertEqual(len(self.bot.photos), 2)
        self.assertEqual(self.bot.photos[0]["photo"].filename, "splash.png")
        self.assertEqual(self.bot.photos[0]["caption"], "*Terra-7*\n\nWelcome aboard!")
        self.assertEqual(self.bot.photos[1]["photo"].filename, f"character_{SESSION_ID}.png")
        self.assertEqual(self.bot.photos[1]["caption"], self._expected_card())
        self._assert_card_keyboard(self.bot.photos[1]["reply_markup"])

        self.update_state.assert_called_once_with(
            PLAYER_ID, onboarding_session_id=SESSION_ID, game_id=GAME_ID, language=LANGUAGE_EN
        )

    async def test_reonboarding_without_welcome_sends_card_only(self):
        # Regression: re-onboarding (server sends no game_title/welcome_message)
        # must not pull a random splash from the pool — the card goes alone.
        session = _FakeHttpSession([("avatar.png", _FakeResponse(body=b"AVATAR"))])
        ok = await self._deliver(_payload(), session)
        self.assertTrue(ok)
        self.assertFalse([u for u, _ in session.requests if "splash-image" in u])
        self.assertEqual(len(self.bot.messages), 0)
        self.assertEqual(len(self.bot.photos), 1)
        self.assertEqual(self.bot.photos[0]["photo"].filename, f"character_{SESSION_ID}.png")

    async def test_missing_player_id_is_ignored(self):
        payload = _payload()
        payload.pop("player_id")
        session = _FakeHttpSession()
        ok = await self._deliver(payload, session)
        self.assertTrue(ok)
        self.assertEqual(session.requests, [])
        self.assertEqual(self.bot.photos, [])
        self.assertEqual(self.bot.messages, [])

    async def test_splash_endpoint_error_falls_back_to_welcome_text(self):
        session = _FakeHttpSession(
            [
                ("splash-image", _FakeResponse(status=500)),
                ("avatar.png", _FakeResponse(body=b"AVATAR")),
            ]
        )
        ok = await self._deliver(_payload(welcome_message="Welcome aboard!"), session)
        self.assertTrue(ok)
        self.assertEqual(len(self.bot.messages), 1)
        self.assertEqual(self.bot.messages[0]["text"], "Welcome aboard!")
        self.assertEqual(len(self.bot.photos), 1)  # card only

    async def test_splash_download_error_falls_back_to_welcome_text(self):
        session = _FakeHttpSession(
            [
                ("splash-image", _FakeResponse(json_data={"image_url": SPLASH_URL})),
                (SPLASH_URL, _FakeResponse(status=500)),
                ("avatar.png", _FakeResponse(body=b"AVATAR")),
            ]
        )
        ok = await self._deliver(_payload(welcome_message="Welcome aboard!"), session)
        self.assertTrue(ok)
        self.assertEqual(len(self.bot.messages), 1)
        self.assertEqual(self.bot.messages[0]["text"], "Welcome aboard!")
        self.assertEqual(len(self.bot.photos), 1)  # card only

    async def test_avatar_download_error_sends_card_as_text(self):
        session = _FakeHttpSession(
            [
                ("splash-image", _FakeResponse(status=500)),
                ("avatar.png", _FakeResponse(status=500)),
            ]
        )
        ok = await self._deliver(_payload(welcome_message="Welcome aboard!"), session)
        self.assertTrue(ok)
        self.assertEqual(self.bot.photos, [])
        self.assertEqual(len(self.bot.messages), 2)
        self.assertEqual(self.bot.messages[0]["text"], "Welcome aboard!")
        self.assertEqual(self.bot.messages[1]["text"], self._expected_card())
        self._assert_card_keyboard(self.bot.messages[1]["reply_markup"])

    async def test_overlong_card_caption_split_into_photo_plus_message(self):
        long_description = "Studies newly discovered lifeforms. " * 40
        session = _FakeHttpSession(
            [("avatar.png", _FakeResponse(body=b"AVATAR"))]
        )
        ok = await self._deliver(
            _payload(proposal={**PROPOSAL, "role_description": long_description}), session
        )
        self.assertTrue(ok)
        self.assertEqual(len(self.bot.photos), 1)
        self.assertIsNone(self.bot.photos[0]["caption"])
        self.assertEqual(len(self.bot.messages), 1)
        self.assertEqual(self.bot.messages[0]["text"], self._expected_card(role_description=long_description))
        self._assert_card_keyboard(self.bot.messages[0]["reply_markup"])

    async def test_forced_card_has_no_buttons_and_calls_completion(self):
        completion = {"game_id": GAME_ID, "role": PROPOSAL["role"]}
        session = _FakeHttpSession([("avatar.png", _FakeResponse(body=b"AVATAR"))])
        completion_mock = mock.AsyncMock()
        with mock.patch.object(push_server, "_onboarding_completion_fn", completion_mock):
            ok = await self._deliver(_payload(final=True, completion=completion), session)
        self.assertTrue(ok)
        self.assertEqual(len(self.bot.photos), 1)
        self.assertIsNone(self.bot.photos[0]["reply_markup"])
        self.assertIn("3 rejections used up", self.bot.photos[0]["caption"])
        completion_mock.assert_awaited_once_with(self.bot, PLAYER_ID, completion)

    async def test_blocked_player_returns_true_and_schedules_autokick(self):
        # A blocked user fails both photo and text sends. The photo failures
        # are swallowed by the per-block except handlers; the Telegram error
        # surfaces from send_message and must schedule the auto-kick.
        blocked = TelegramBadRequest(
            method="sendMessage", message="Bot was blocked by the user: USER_IS_BLOCKED"
        )
        self.bot.photo_error = blocked
        self.bot.message_error = blocked
        session = _FakeHttpSession(
            [
                ("splash-image", _FakeResponse(json_data={"image_url": SPLASH_URL})),
                (SPLASH_URL, _FakeResponse(body=b"SPLASH")),
                ("avatar.png", _FakeResponse(body=b"AVATAR")),
            ]
        )
        with mock.patch.object(push_server, "_auto_kick_blocked_player", new_callable=mock.AsyncMock) as kick:
            ok = await self._deliver(_payload(welcome_message="Welcome aboard!"), session)
            self.assertTrue(ok)
            for _ in range(3):
                await asyncio.sleep(0)
            kick.assert_awaited_once_with(PLAYER_ID)
        self.assertEqual(self.bot.photos, [])
        self.assertEqual(self.bot.messages, [])


class ResetFailedTerminalPushMessagesTests(unittest.TestCase):
    """Startup retry of one-shot content must not depend on a current turn.

    Regression for game jkzoi8: outcome/game-over pushes failed while the
    Telegram proxy was down, the game then ended, and
    reset_failed_for_current_turn never retried them because finished
    games have no current turn.
    """

    PLAYER_ID = 987201

    @classmethod
    def setUpClass(cls):
        init_db(DB_PATH)

    def _insert_failed(self, push_type: str, turn: int | None, game_id: str = GAME_ID) -> int:
        row_id = insert_push_message(self.PLAYER_ID, push_type, "{}", turn, game_id, DB_PATH)
        mark_push_failed(row_id, "proxy down", DB_PATH)
        return row_id

    def _row(self, row_id: int) -> tuple[str, str | None]:
        conn = sqlite3.connect(DB_PATH)
        try:
            row = conn.execute(
                "SELECT status, error FROM push_queue WHERE id = ?", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        return row[0], row[1]

    def test_failed_content_reset_even_without_current_turn(self):
        outcome = self._insert_failed("outcome", turn=9, game_id="finished_game")
        game_over = self._insert_failed("game_over", turn=None, game_id="finished_game")
        death = self._insert_failed("player_death", turn=1, game_id="finished_game")

        reset = reset_failed_terminal_push_messages(DB_PATH)

        self.assertGreaterEqual(reset, 3)
        for row_id in (outcome, game_over, death):
            status, error = self._row(row_id)
            self.assertEqual(status, "pending")
            self.assertIsNone(error)

    def test_turn_bound_and_transient_types_stay_failed(self):
        briefing = self._insert_failed("briefing", turn=5)
        action = self._insert_failed("action", turn=5)
        reminder = self._insert_failed("turn_reminder", turn=5)
        language = self._insert_failed("language_changed", turn=None)

        reset_failed_terminal_push_messages(DB_PATH)

        for row_id in (briefing, action, reminder, language):
            status, error = self._row(row_id)
            self.assertEqual(status, "failed")
            self.assertEqual(error, "proxy down")

    def test_reset_rows_show_up_in_pending_queue(self):
        row_id = self._insert_failed("gm_notification", turn=12, game_id="finished_game")

        reset_failed_terminal_push_messages(DB_PATH)

        rows = [r for r in get_pending_push_messages(DB_PATH) if r["id"] == row_id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["push_type"], "gm_notification")


class EscapeMarkdownTests(unittest.TestCase):
    def test_escapes_special_characters(self):
        self.assertEqual(
            push_server._escape_md("last_stand *bold* `code` [link]"),
            "last\\_stand \\*bold\\* \\`code\\` \\[link]",
        )

    def test_plain_text_unchanged(self):
        self.assertEqual(push_server._escape_md("Terra-7: Последний дозор"), "Terra-7: Последний дозор")


class IsStaleTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(push_server._current_turns)
        push_server._current_turns.clear()
        push_server._current_turns.update({"g1": 4})

    def tearDown(self):
        push_server._current_turns.clear()
        push_server._current_turns.update(self._saved)

    def test_none_turn_or_game_is_never_stale(self):
        self.assertFalse(push_server._is_stale(None, "g1"))
        self.assertFalse(push_server._is_stale(3, None))

    def test_unknown_game_is_never_stale(self):
        self.assertFalse(push_server._is_stale(1, "unknown"))

    def test_older_turn_is_stale(self):
        self.assertTrue(push_server._is_stale(3, "g1"))

    def test_current_turn_is_not_stale(self):
        self.assertFalse(push_server._is_stale(4, "g1"))
        self.assertFalse(push_server._is_stale(5, "g1"))


class OnboardingReadyHandlerTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        init_db(DB_PATH)

    async def _post(self, body: bytes, content_type: str = "application/json"):
        app = web.Application()
        app.router.add_post("/push/onboarding-ready", push_server.handle_push_onboarding_ready)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{port}/push/onboarding-ready",
                    data=body,
                    headers={"Content-Type": content_type},
                ) as resp:
                    return resp.status, await resp.json()
        finally:
            await runner.cleanup()

    async def test_valid_payload_queued(self):
        payload = _payload()
        payload["player_id"] = 987101
        status, data = await self._post(json.dumps(payload).encode())
        self.assertEqual(status, 200)
        self.assertEqual(data, {"status": "ok", "player_id": 987101})
        rows = [r for r in get_pending_push_messages(DB_PATH) if r["player_id"] == 987101]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["push_type"], "onboarding")
        self.assertEqual(rows[0]["game_id"], GAME_ID)
        self.assertEqual(json.loads(rows[0]["payload"])["session_id"], SESSION_ID)

    async def test_invalid_json_rejected(self):
        status, data = await self._post(b"not json {")
        self.assertEqual(status, 400)
        self.assertEqual(data, {"error": "Invalid JSON"})

    async def test_missing_player_id_or_game_id_rejected(self):
        status, data = await self._post(json.dumps({"game_id": GAME_ID}).encode())
        self.assertEqual(status, 400)
        self.assertEqual(data, {"error": "Missing player_id or game_id"})

        status, data = await self._post(json.dumps({"player_id": 987102}).encode())
        self.assertEqual(status, 400)
        self.assertEqual(data, {"error": "Missing player_id or game_id"})


if __name__ == "__main__":
    unittest.main()
