"""Regression tests for the /gm_list Markdown message.

/gm_list renders API data — LLM-generated titles, snake_case archetypes,
scheduler times — into a parse_mode='Markdown' message. An unescaped '_'
in an archetype (`last_stand`) opened an unclosed italic entity and
Telegram rejected the whole message with "can't parse entities: Can't
find end of the entity starting at byte offset 314" (2026-09-09).

These tests build the message through the same code path as the handler
(_build_gm_list_message) and validate it against Telegram's legacy
Markdown entity rules: unescaped _ * ` [ outside code spans must pair
up, escaped ones are skipped.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402
from language import LANGUAGE_EN, LANGUAGE_RU  # noqa: E402

# Mirrors the shapes returned by /admin/list-games and /scheduler/status.
GAMES = [
    {
        "game_id": "default_game",
        "name": "Линкор «Цитадель»: Последний отбой",
        "player_count": 2,
        "onboarding_count": 1,
        "status": "active",
        "started": True,
        "language": "ru",
        "current_turn": 3,
        "archetype": "last_stand",
    },
    {
        "game_id": "beta02",
        "title": "Meridian Reach: *Bold* and _italics_ and `code`",
        "player_count": 0,
        "onboarding_count": 1,
        "status": "active",
        "started": False,
        "language": "en",
        "current_turn": 0,
        "archetype": "intrigue",
    },
    {
        "game_id": "gamma03",
        "player_count": 4,
        "onboarding_count": 0,
        "status": "ended",
        "started": True,
        "language": "ru",
        "current_turn": 8,
        "archetype": "defense",
        "finale_outcome_type": "victory",
    },
]

SCHED_BY_GAME = {
    "default_game": {
        "mode": "scheduled",
        "schedule_type": "daily",
        "schedule_value": "06:00,18:00",
        "next_run_at": "2026-09-09T06:00:00+00:00",
    },
    "beta02": {
        "mode": "scheduled",
        "schedule_type": "interval",
        "schedule_value": "10800",
        "next_run_at": "2026-09-10T12:30:00+00:00",
    },
    "gamma03": {
        "mode": "paused",
        "schedule_type": "daily",
        "schedule_value": "09:00",
    },
}


class BuildGmListMessageTests(unittest.TestCase):
    def _assert_markdown_balanced(self, text: str) -> None:
        """Fail if text has unbalanced legacy-Markdown entities."""
        in_code = False
        italic = bold = bracket = 0
        i = 0
        while i < len(text):
            c = text[i]
            if c == "\\" and i + 1 < len(text) and text[i + 1] in "_*`[":
                i += 2
                continue
            if c == "`":
                in_code = not in_code
            elif not in_code:
                if c == "_":
                    italic += 1
                elif c == "*":
                    bold += 1
                elif c == "[":
                    bracket += 1
            i += 1
        self.assertFalse(in_code, "unclosed code span")
        self.assertEqual(italic % 2, 0, "unbalanced _ entities")
        self.assertEqual(bold % 2, 0, "unbalanced * entities")
        self.assertEqual(bracket, 0, "unclosed [ entity")

    def _build(self, language: str) -> str:
        gm_msgs = bot.lang.get_gm_commands(language)
        return bot._build_gm_list_message(GAMES, SCHED_BY_GAME, gm_msgs)

    def test_validator_catches_unescaped_underscore(self):
        # Sanity check: the validator must reject the pre-fix message shape.
        with self.assertRaises(AssertionError):
            self._assert_markdown_balanced("1. `beta02` — game 🎭 last_stand 🇷🇺")

    def test_archetype_underscore_is_escaped(self):
        for language in (LANGUAGE_RU, LANGUAGE_EN):
            with self.subTest(language=language):
                message = self._build(language)
                self.assertIn("last\\_stand", message)
                self._assert_markdown_balanced(message)

    def test_title_with_markdown_entities_is_escaped(self):
        message = self._build(LANGUAGE_EN)
        self.assertIn("\\*Bold\\*", message)
        self.assertIn("\\_italics\\_", message)
        self.assertIn("\\`code\\`", message)
        self._assert_markdown_balanced(message)

    def test_ended_games_section(self):
        for language in (LANGUAGE_RU, LANGUAGE_EN):
            with self.subTest(language=language):
                gm_msgs = bot.lang.get_gm_commands(language)
                message = self._build(language)
                self.assertIn(f"*{gm_msgs['game_ended_label']}:*", message)
                self.assertIn(gm_msgs["ended_victory_label"], message)
                self._assert_markdown_balanced(message)

    def test_missing_title_falls_back_to_default(self):
        gm_msgs = bot.lang.get_gm_commands(LANGUAGE_EN)
        message = bot._build_gm_list_message(
            [{"game_id": "x1", "status": "active", "started": False, "language": "en"}],
            {},
            gm_msgs,
        )
        self.assertIn(gm_msgs["default_game_title"], message)
        self._assert_markdown_balanced(message)


class SchedulerFormatTests(unittest.TestCase):
    def test_next_run_time_rendered_in_utc(self):
        self.assertEqual(
            bot._format_scheduler_time("2026-09-09T06:00:00+00:00"),
            "2026-09-09 06:00 UTC",
        )

    def test_naive_time_assumed_utc(self):
        self.assertEqual(
            bot._format_scheduler_time("2026-09-09T06:00:00"),
            "2026-09-09 06:00 UTC",
        )

    def test_unparseable_time_returned_as_is(self):
        self.assertEqual(bot._format_scheduler_time("not-a-date"), "not-a-date")

    def test_schedule_labels(self):
        self.assertEqual(bot._format_schedule_label("interval", "3600"), "1h")
        self.assertEqual(bot._format_schedule_label("interval", "5400"), "90m")
        self.assertEqual(bot._format_schedule_label("interval", "45"), "45s")
        self.assertEqual(bot._format_schedule_label("daily", "06:00,18:00"), "06:00,18:00")
        self.assertEqual(bot._format_schedule_label("multi_daily", "08:00"), "08:00")


if __name__ == "__main__":
    unittest.main()
