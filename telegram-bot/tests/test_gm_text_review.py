"""Tests for the GM text-review push delivery (push_server._deliver_gm_text_review).

The review carries generated briefings with action choices plus the previous
turn's outcome narrative, rendered with per-action 👍/👎/🤔 verdict rows whose
callback stages (btx_*_a<n>, btx_*_brf, otext) select the text reason set in
bot.gm_feedback_callback.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402
import push_server  # noqa: E402
from language import get_gm_text_review  # noqa: E402

GM_ID = 99
SUBSCRIBER_ID = 777
GAME_ID = "g1"
TURN = 12


def _payload(**overrides) -> dict:
    payload = {
        "game_id": GAME_ID,
        "turn": TURN,
        "language": "ru",
        "entries": [
            {
                "slug": "p100",
                "player_id": 100,
                "npc_key": None,
                "is_npc": False,
                "name": "Ivan",
                "role": "captain",
                "personal_title": "Ivan — Капитан — Решения",
                "briefing": "Мостик в огне.",
                "actions": [
                    {"id": "action_1", "kind": "progress", "text": "Перегнать мощность в щиты"},
                    {"id": "action_2", "kind": "injury", "text": "Прыгнуть в спасательную капсулу"},
                ],
                "selected_action_id": None,
            },
            {
                "slug": "science_officer",
                "player_id": None,
                "npc_key": "npc_science_officer_g1",
                "is_npc": True,
                "name": "Spock",
                "role": "science officer",
                "personal_title": "Spock — Научный офицер — Логика",
                "briefing": "Сенсоры молчат.",
                "actions": [
                    {"id": "scan", "kind": "progress", "text": "Сканировать аномалию"},
                ],
                "selected_action_id": "scan",
            },
        ],
        "outcome_narrative": "Экипаж прошёл сквозь поле обломков.",
    }
    payload.update(overrides)
    return payload


class _FakeBot:
    """Records send_message calls; can fail per chat_id."""

    def __init__(self, fail_for: set[int] | None = None):
        self.messages: list[dict] = []
        self.fail_for = fail_for or set()

    async def send_message(self, chat_id=None, text=None, parse_mode=None, reply_markup=None):
        if chat_id in self.fail_for:
            raise ConnectionError("telegram down")
        self.messages.append({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
        })


def _row_callback_data(markup, row: int) -> list[str]:
    return [b.callback_data for b in markup.inline_keyboard[row]]


class DeliverGmTextReviewTests(unittest.IsolatedAsyncioTestCase):
    async def _deliver(self, payload: dict, bot: _FakeBot) -> bool:
        with (
            mock.patch.object(push_server, "GAME_MASTER_ID", GM_ID),
            mock.patch.object(push_server, "FEEDBACK_IDS", {SUBSCRIBER_ID}),
            mock.patch("asyncio.sleep", mock.AsyncMock()),
        ):
            return await push_server._deliver_gm_text_review(payload, bot)

    async def test_delivers_outcome_and_crew_to_gm_and_subscribers(self):
        bot_fake = _FakeBot()
        ok = await self._deliver(_payload(), bot_fake)
        self.assertTrue(ok)
        chats = [m["chat_id"] for m in bot_fake.messages]
        # 1 outcome + 2 crew messages per recipient
        self.assertEqual(
            chats, [GM_ID, GM_ID, GM_ID, SUBSCRIBER_ID, SUBSCRIBER_ID, SUBSCRIBER_ID]
        )
        outcome_msg = bot_fake.messages[0]
        self.assertIn("Итог хода 11", outcome_msg["text"])
        self.assertIn("поле обломков", outcome_msg["text"])
        self.assertEqual(
            _row_callback_data(outcome_msg["reply_markup"], 0),
            [
                f"gmfb:n:{GAME_ID}:{TURN}:otext:0",
                f"gmfb:v:{GAME_ID}:{TURN}:otext:up",
                f"gmfb:v:{GAME_ID}:{TURN}:otext:down",
                f"gmfb:v:{GAME_ID}:{TURN}:otext:meh",
            ],
        )

    async def test_crew_message_layout_and_verdict_rows(self):
        bot_fake = _FakeBot()
        await self._deliver(_payload(), bot_fake)
        crew_msg = bot_fake.messages[1]
        text = crew_msg["text"]
        self.assertIn("*Ivan*", text)
        self.assertIn("🎯 *Ivan — Капитан — Решения*", text)
        self.assertIn("Мостик в огне.", text)
        self.assertIn("Действия:", text)
        self.assertIn("1️⃣ [прогресс] Перегнать мощность в щиты", text)
        self.assertIn("2️⃣ [ранение] Прыгнуть в спасательную капсулу", text)
        self.assertNotIn("Шилды держатся", text)  # hidden consequence stays out

        rows = crew_msg["reply_markup"].inline_keyboard
        self.assertEqual(
            _row_callback_data(crew_msg["reply_markup"], 0),
            [
                f"gmfb:n:{GAME_ID}:{TURN}:btx_p100_a1:0",
                f"gmfb:v:{GAME_ID}:{TURN}:btx_p100_a1:up",
                f"gmfb:v:{GAME_ID}:{TURN}:btx_p100_a1:down",
                f"gmfb:v:{GAME_ID}:{TURN}:btx_p100_a1:meh",
            ],
        )
        self.assertEqual(
            _row_callback_data(crew_msg["reply_markup"], 2)[-1],
            f"gmfb:v:{GAME_ID}:{TURN}:btx_p100_brf:meh",
        )

    async def test_npc_selected_action_marked(self):
        bot_fake = _FakeBot()
        await self._deliver(_payload(), bot_fake)
        npc_msg = bot_fake.messages[2]
        self.assertIn("🤖", npc_msg["text"])
        self.assertIn("✅", npc_msg["text"])

    async def test_long_entry_splits_briefing_from_actions(self):
        payload = _payload()
        payload["entries"][0]["briefing"] = "Длинно " * 700
        bot_fake = _FakeBot()
        await self._deliver(payload, bot_fake)
        gm_msgs = [m for m in bot_fake.messages if m["chat_id"] == GM_ID]
        # outcome + briefing part + actions part + npc entry = 4
        self.assertEqual(len(gm_msgs), 4)
        self.assertIn("Длинно", gm_msgs[1]["text"])
        self.assertIn("Действия:", gm_msgs[2]["text"])
        self.assertIn("Перегнать мощность в щиты", gm_msgs[2]["text"])
        self.assertIsNone(gm_msgs[1]["reply_markup"])
        self.assertIsNotNone(gm_msgs[2]["reply_markup"])

    async def test_recipient_failure_does_not_block_others(self):
        bot_fake = _FakeBot(fail_for={GM_ID})
        ok = await self._deliver(_payload(), bot_fake)
        self.assertFalse(ok)
        self.assertEqual(
            [m["chat_id"] for m in bot_fake.messages],
            [SUBSCRIBER_ID, SUBSCRIBER_ID, SUBSCRIBER_ID],
        )

    async def test_no_recipients_returns_true(self):
        bot_fake = _FakeBot()
        with (
            mock.patch.object(push_server, "GAME_MASTER_ID", 0),
            mock.patch.object(push_server, "FEEDBACK_IDS", set()),
        ):
            ok = await push_server._deliver_gm_text_review(_payload(), bot_fake)
        self.assertTrue(ok)
        self.assertEqual(bot_fake.messages, [])


class TextReasonSelectionTests(unittest.TestCase):
    def test_text_stages_get_text_reasons(self):
        for stage in ("btx_p100_a1", "btx_science_officer_brf", "otext"):
            self.assertIs(bot._feedback_reasons(stage), bot.GM_TEXT_FEEDBACK_REASONS)

    def test_image_stages_keep_scene_reasons(self):
        for stage in ("bridge", "scene", "char", "action", "death", "outcome", "finale"):
            self.assertIs(bot._feedback_reasons(stage), bot.GM_FEEDBACK_REASONS)

    def test_reason_codes_translate(self):
        labels = set(bot.GM_TEXT_FEEDBACK_REASONS.values())
        self.assertEqual(
            labels, {"шаблон/повтор", "не по персонажу", "скучно", "сюжет/логика", "спойлер последствия", "другое"}
        )
        self.assertEqual(
            set(get_gm_text_review("ru")["kind_labels"].values()),
            {"прогресс", "ранение", "смерть", "пауза"},
        )
        self.assertEqual(
            set(get_gm_text_review("en")["kind_labels"].values()),
            {"progress", "injury", "fatal", "delay"},
        )


if __name__ == "__main__":
    unittest.main()
