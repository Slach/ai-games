"""Tests for the /gm_simulate command helpers and turn loop.

Covers argument parsing, turn-outcome summary extraction, GM message key
completeness in both languages, and the background turn loop driven by a
fake game-server API (no network, no LLM): happy path, game ending
mid-simulation, failure stopping the loop, and per-turn timeout.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
from language import GM_COMMANDS, LANGUAGE_EN, LANGUAGE_RU

SIMULATE_KEYS = [
    "simulate_usage",
    "simulate_already_running",
    "simulate_started",
    "simulate_turn_done",
    "simulate_game_ended",
    "simulate_done",
    "simulate_error",
    "simulate_not_started",
]


class ParseArgsTests(unittest.TestCase):
    def test_no_arguments(self):
        self.assertEqual(bot.parse_gm_simulate_args("/gm_simulate"), (None, None))
        self.assertEqual(bot.parse_gm_simulate_args("/gm_simulate  "), (None, None))
        self.assertEqual(bot.parse_gm_simulate_args(None), (None, None))

    def test_default_turns(self):
        self.assertEqual(bot.parse_gm_simulate_args("/gm_simulate abc123"), ("abc123", 3))

    def test_explicit_turns(self):
        self.assertEqual(bot.parse_gm_simulate_args("/gm_simulate abc123 5"), ("abc123", 5))
        self.assertEqual(bot.parse_gm_simulate_args("/gm_simulate abc123 1"), ("abc123", 1))

    def test_turns_capped(self):
        self.assertEqual(bot.parse_gm_simulate_args("/gm_simulate abc123 99"), ("abc123", bot.SIMULATE_MAX_TURNS))

    def test_invalid_turns_show_usage(self):
        self.assertEqual(bot.parse_gm_simulate_args("/gm_simulate abc123 abc"), (None, None))
        self.assertEqual(bot.parse_gm_simulate_args("/gm_simulate abc123 0"), (None, None))
        self.assertEqual(bot.parse_gm_simulate_args("/gm_simulate abc123 -2"), (None, None))


class SummarizeTurnOutcomeTests(unittest.TestCase):
    def test_empty_inputs(self):
        self.assertEqual(bot.summarize_turn_outcome(None), "")
        self.assertEqual(bot.summarize_turn_outcome({}), "")
        self.assertEqual(bot.summarize_turn_outcome({"combined_outcome": ""}), "")

    def test_narrative_extracted_and_whitespace_collapsed(self):
        turn = {
            "combined_outcome": json.dumps(
                {"outcome_narrative": "First line.\n  Second  line.", "mission_progress": "50%"}
            )
        }
        self.assertEqual(bot.summarize_turn_outcome(turn), "First line. Second line.")

    def test_long_narrative_truncated_to_200_chars(self):
        turn = {"combined_outcome": json.dumps({"outcome_narrative": "x" * 500})}
        self.assertEqual(bot.summarize_turn_outcome(turn), "x" * 200)

    def test_non_json_outcome_falls_back_to_raw_text(self):
        turn = {"combined_outcome": "plain  text\nsummary"}
        self.assertEqual(bot.summarize_turn_outcome(turn), "plain text summary")

    def test_dict_shaped_outcome(self):
        turn = {"combined_outcome": {"outcome_narrative": "Dict narrative"}}
        self.assertEqual(bot.summarize_turn_outcome(turn), "Dict narrative")

    def test_non_dict_json_outcome_yields_empty(self):
        turn = {"combined_outcome": json.dumps(["not", "a", "dict"])}
        self.assertEqual(bot.summarize_turn_outcome(turn), "")


class EndedReasonLabelTests(unittest.TestCase):
    def test_known_status_localized(self):
        gm_msgs = bot.lang.get_gm_commands(LANGUAGE_EN)
        self.assertEqual(bot._ended_reason_label("ship_destroyed", gm_msgs), gm_msgs["ended_reason_ship_destroyed"])
        self.assertEqual(bot._ended_reason_label("mutiny", gm_msgs), gm_msgs["ended_reason_mutiny"])

    def test_unknown_status_passthrough(self):
        gm_msgs = bot.lang.get_gm_commands(LANGUAGE_EN)
        self.assertEqual(bot._ended_reason_label("weird_status", gm_msgs), "weird_status")


class MessageKeysTests(unittest.TestCase):
    def test_simulate_keys_present_in_both_languages(self):
        for key in SIMULATE_KEYS:
            for language in (LANGUAGE_RU, LANGUAGE_EN):
                with self.subTest(key=key, language=language):
                    self.assertIn(key, GM_COMMANDS[language])
                    self.assertTrue(GM_COMMANDS[language][key].strip())


class FakeMessage:
    """Records answers like an aiogram Message."""

    def __init__(self):
        self.answers: list[str] = []

    async def answer(self, text, parse_mode=None, **_kwargs):
        self.answers.append(text)


def outcome_turn(narrative: str) -> dict:
    return {"combined_outcome": json.dumps({"outcome_narrative": narrative})}


class TurnLoopTests(unittest.IsolatedAsyncioTestCase):
    """Drive _run_gm_simulation against a fake game-server API."""

    def setUp(self):
        self.gm_msgs = bot.lang.get_gm_commands(LANGUAGE_EN)

    async def asyncTearDown(self):
        bot._simulate_running_games.clear()

    async def _run(self, message, game_id, turns):
        # Mimic the handler: the game is registered as running for the loop.
        bot._simulate_running_games.add(game_id)
        await bot._run_gm_simulation(message, game_id, turns, LANGUAGE_EN, self.gm_msgs)

    async def test_happy_path_two_turns(self):
        game_id = "simhappy"
        calls: list[tuple[str, str]] = []
        world = {"turn": 3, "continue_issued": False}

        async def fake_api(method, endpoint, data=None, params=None, timeout_total=0, ignore_codes=()):
            calls.append((method, endpoint))
            if endpoint == "/game/state":
                if world["continue_issued"]:
                    # First poll after continue: generation still in flight.
                    world["continue_issued"] = False
                    return {"turn": world["turn"] - 1, "status": "active", "ship_alive": True}
                return {"turn": world["turn"], "status": "active", "ship_alive": True}
            if endpoint == "/game/status":
                return {
                    "players": [
                        {"player_id": 1, "is_dead": False, "is_spectator": False, "has_chosen": True},
                        {"player_id": 2, "is_dead": False, "is_spectator": False, "has_chosen": False},
                        {"player_id": 3, "is_dead": True, "is_spectator": False, "has_chosen": False},
                    ]
                }
            if endpoint.startswith("/game/auto-action/"):
                return {"status": "selected"}
            if endpoint == "/admin/continue-game":
                world["continue_issued"] = True
                world["turn"] += 1
                return {"status": "accepted", "turn": world["turn"] - 1}
            if endpoint == "/game/turn/2":
                return outcome_turn("The crew patched the hull under fire.")
            if endpoint == "/game/turn/3":
                return outcome_turn("Scouts reached the relay.")
            raise AssertionError(f"unexpected endpoint {endpoint}")

        message = FakeMessage()
        with (
            patch.object(bot, "api_request", fake_api),
            patch.object(bot, "SIMULATE_POLL_INTERVAL_SECONDS", 0),
        ):
            await self._run(message, game_id, 2)

        self.assertEqual(len(message.answers), 3)  # 2 per-turn + final done
        self.assertIn("Turn 2 done", message.answers[0])
        self.assertIn("The crew patched the hull under fire.", message.answers[0])
        self.assertIn("Next turn: 4", message.answers[0])
        self.assertIn("Turn 3 done", message.answers[1])
        self.assertIn("2 turns played", message.answers[2])
        # One auto-action per iteration, only for the pending live player on
        # the playable turn (dead player 1 and already-chosen player skipped).
        auto_calls = [c for c in calls if c[1].startswith("/game/auto-action/")]
        self.assertEqual(
            auto_calls,
            [("POST", "/game/auto-action/2/2"), ("POST", "/game/auto-action/2/3")],
        )
        self.assertEqual(
            [c for c in calls if c[1] == "/admin/continue-game"],
            [("POST", "/admin/continue-game"), ("POST", "/admin/continue-game")],
        )
        self.assertNotIn(game_id, bot._simulate_running_games)

    async def test_game_ended_mid_simulation(self):
        game_id = "simended"
        world = {"state_calls": 0}

        async def fake_api(method, endpoint, data=None, params=None, timeout_total=0, ignore_codes=()):
            if endpoint == "/game/state":
                world["state_calls"] += 1
                if world["state_calls"] == 1:
                    return {"turn": 3, "status": "active", "ship_alive": True}
                # Playable turn 2 was closed; its outcome destroyed the ship,
                # so the game state intentionally stops advancing.
                return {"turn": 3, "status": "ship_destroyed", "ship_alive": False}
            if endpoint == "/game/status":
                return {"players": []}
            if endpoint == "/admin/continue-game":
                return {"status": "accepted", "turn": 3}
            if endpoint == "/game/turn/2":
                return outcome_turn("The reactor went critical.")
            raise AssertionError(f"unexpected endpoint {endpoint}")

        message = FakeMessage()
        with (
            patch.object(bot, "api_request", fake_api),
            patch.object(bot, "SIMULATE_POLL_INTERVAL_SECONDS", 0),
        ):
            await self._run(message, game_id, 3)

        self.assertEqual(len(message.answers), 1)
        self.assertIn("ended at turn 2", message.answers[0])
        self.assertIn(self.gm_msgs["ended_reason_ship_destroyed"], message.answers[0])
        self.assertIn("The reactor went critical.", message.answers[0])
        self.assertNotIn(game_id, bot._simulate_running_games)

    async def test_auto_action_failure_stops_loop(self):
        game_id = "simfail"

        async def fake_api(method, endpoint, data=None, params=None, timeout_total=0, ignore_codes=()):
            if endpoint == "/game/state":
                return {"turn": 2, "status": "active", "ship_alive": True}
            if endpoint == "/game/status":
                return {
                    "players": [
                        {"player_id": 7, "is_dead": False, "is_spectator": False, "has_chosen": False},
                    ]
                }
            if endpoint.startswith("/game/auto-action/"):
                raise RuntimeError("LLM exploded")
            raise AssertionError(f"unexpected endpoint {endpoint}")

        message = FakeMessage()
        with patch.object(bot, "api_request", fake_api):
            await self._run(message, game_id, 2)

        self.assertEqual(len(message.answers), 1)
        self.assertIn("stopped at turn 1", message.answers[0])
        self.assertIn("LLM exploded", message.answers[0])
        self.assertNotIn(game_id, bot._simulate_running_games)

    async def test_turn_timeout_stops_loop(self):
        game_id = "simtimeout"

        async def fake_api(method, endpoint, data=None, params=None, timeout_total=0, ignore_codes=()):
            if endpoint == "/game/state":
                # Generation never finishes: turn stays at 2, game stays active.
                return {"turn": 2, "status": "active", "ship_alive": True}
            if endpoint == "/game/status":
                return {"players": []}
            if endpoint == "/admin/continue-game":
                return {"status": "accepted", "turn": 2}
            raise AssertionError(f"unexpected endpoint {endpoint}")

        message = FakeMessage()
        with (
            patch.object(bot, "api_request", fake_api),
            patch.object(bot, "SIMULATE_POLL_INTERVAL_SECONDS", 0.01),
            patch.object(bot, "SIMULATE_TURN_TIMEOUT_SECONDS", 0.05),
        ):
            await self._run(message, game_id, 1)

        self.assertEqual(len(message.answers), 1)
        self.assertIn("stopped at turn 1", message.answers[0])
        self.assertIn("did not complete within", message.answers[0])
        self.assertNotIn(game_id, bot._simulate_running_games)


class TestTurnFeedbackLoop(unittest.TestCase):
    """The per-turn status message carries the same verdict keyboard as the
    image stages, so the GM can rate a simulated turn and attach a text
    comment through the regular pending-feedback flow."""

    def test_turn_stage_uses_text_reasons(self):
        self.assertIs(bot._feedback_reasons("turn"), bot.GM_TEXT_FEEDBACK_REASONS)
        self.assertIsNot(bot._feedback_reasons("turn"), bot.GM_FEEDBACK_REASONS)

    def test_turn_keyboard_builds_verdict_callback(self):
        from aiogram.types import InlineKeyboardMarkup

        kb = bot._turn_feedback_kb("althef", 7)
        self.assertIsInstance(kb, InlineKeyboardMarkup)
        buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertEqual(
            buttons,
            [
                "gmfb:v:althef:7:turn:up",
                "gmfb:v:althef:7:turn:down",
                "gmfb:v:althef:7:turn:meh",
            ],
        )


class TestVerdictKeepsKeyboard(unittest.IsolatedAsyncioTestCase):
    """Telegram drops the whole inline keyboard when editMessageText omits
    reply_markup, so the ✅ marker edit must send the keyboard back —
    otherwise the first verdict press wipes the remaining action rows
    (GM feedback 2026-11-15: keyboard vanished after rating one action)."""

    def _markup(self):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        rows = [
            [("1️⃣ 👍", "gmfb:v:althef:11:btx_p281412419_a1:up"),
             ("1️⃣ 👎", "gmfb:v:althef:11:btx_p281412419_a1:down")],
            [("2️⃣ 👍", "gmfb:v:althef:11:btx_p281412419_a2:up"),
             ("2️⃣ 👎", "gmfb:v:althef:11:btx_p281412419_a2:down")],
            [("📝 👍", "gmfb:v:althef:11:btx_p281412419_brf:up")],
        ]
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=c) for t, c in row]
            for row in rows
        ])

    def test_mark_stage_voted_prefixes_only_voted_stage(self):
        marked = bot._mark_stage_voted(self._markup(), "althef", 11, "btx_p281412419_a1")
        self.assertEqual(marked.inline_keyboard[0][0].text, "✅ 1️⃣ 👍")
        self.assertEqual(marked.inline_keyboard[0][1].text, "✅ 1️⃣ 👎")
        # The other action rows stay unmarked and pressable.
        self.assertEqual(marked.inline_keyboard[1][0].text, "2️⃣ 👍")
        self.assertEqual(marked.inline_keyboard[2][0].text, "📝 👍")
        self.assertEqual(
            marked.inline_keyboard[1][0].callback_data,
            "gmfb:v:althef:11:btx_p281412419_a2:up",
        )

    def test_mark_stage_voted_second_stage(self):
        marked = bot._mark_stage_voted(self._markup(), "althef", 11, "btx_p281412419_a2")
        self.assertEqual(marked.inline_keyboard[0][0].text, "1️⃣ 👍")
        self.assertEqual(marked.inline_keyboard[1][0].text, "✅ 2️⃣ 👍")

    def test_mark_stage_voted_without_markup(self):
        self.assertIsNone(bot._mark_stage_voted(None, "althef", 11, "btx_x"))

    async def test_verdict_edit_passes_keyboard_back(self):
        from aiogram import types

        original = self._markup()
        edits = []

        class FakeMessage:
            text = " Crew text review — Slach (Engineer)"
            caption = None
            reply_markup = original

            async def edit_text(self, new_text, **kwargs):
                edits.append((new_text, kwargs))

        class FakeUser:
            id = bot.GAME_MASTER_ID

        class FakeCallback:
            from_user = FakeUser()
            data = "gmfb:v:althef:11:btx_p281412419_a1:up"
            message = FakeMessage()

            async def answer(self, *args, **kwargs):
                pass

        callback = FakeCallback()
        with patch.object(bot, "_record_gm_feedback"):
            await bot.gm_feedback_callback(callback)

        self.assertEqual(len(edits), 1)
        new_text, kwargs = edits[0]
        self.assertTrue(new_text.endswith("✅ Записано: 👍"))
        # Without reply_markup Telegram would strip the whole keyboard here.
        marked = kwargs["reply_markup"]
        self.assertEqual(marked.inline_keyboard[0][0].text, "✅ 1️⃣ 👍")
        self.assertEqual(marked.inline_keyboard[1][0].text, "2️⃣ 👍")


if __name__ == "__main__":

    unittest.main()
