"""Tests for the post-finale mission summary: stats reader + text formatter."""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
from language import LANGUAGE_EN, LANGUAGE_RU, format_game_summary  # noqa: E402

logger = logging.getLogger(__name__)


class TestGetGameActionStats(unittest.TestCase):
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

    def _save(self, game_id: str, player_id: int, turn: int, kind: str) -> None:
        db.save_player_action_stats(
            game_id=game_id,
            player_id=player_id,
            turn=turn,
            action_id=f"action_{turn}",
            action_text="a",
            consequence_kind=kind,
            hull_integrity=90,
        )

    def test_empty_game_returns_zeroes(self):
        stats = db.get_game_action_stats(game_id="g_empty")
        self.assertEqual(stats, {"players": [], "total_actions": 0, "auto_actions": 0})

    def test_roundtrip_two_players_delay_counts_as_auto(self):
        # player 111: two manual actions + one delay (auto hesitation)
        self._save("g1", 111, 1, "progress")
        self._save("g1", 111, 2, "delay")
        self._save("g1", 111, 3, "injury")
        # player 222: one manual action
        self._save("g1", 222, 1, "fatal")
        stats = db.get_game_action_stats(game_id="g1")
        self.assertEqual(
            stats,
            {
                "players": [
                    {"player_id": 111, "actions": 3, "auto_actions": 1},
                    {"player_id": 222, "actions": 1, "auto_actions": 0},
                ],
                "total_actions": 4,
                "auto_actions": 1,
            },
        )

    def test_other_game_rows_not_leaked(self):
        self._save("g1", 111, 1, "delay")
        self._save("g2", 111, 1, "delay")
        stats = db.get_game_action_stats(game_id="g1")
        self.assertEqual(stats["total_actions"], 1)
        self.assertEqual(stats["auto_actions"], 1)


class TestFormatGameSummary(unittest.TestCase):
    def _args(self) -> dict:
        return dict(
            outcome_label="🏆 МИССИЯ ВЫПОЛНЕНА — ПОБЕДА!",
            end_status="mission_complete",
            turns=7,
            hull=45,
            shields=30,
            threat=60,
            dead_names=["Иван Петров", "Мария"],
            alive_crew=4,
            total_crew=6,
            player_stats=[
                {"name": "Алексей", "actions": 7, "auto_actions": 2},
                {"name": "Иван Петров", "actions": 5, "auto_actions": 1},
            ],
        )

    def test_ru_summary_contains_all_blocks(self):
        text = format_game_summary(LANGUAGE_RU, **self._args())
        self.assertIn("📊 Итоги миссии", text)
        self.assertIn(
            "Исход: 🏆 МИССИЯ ВЫПОЛНЕНА — ПОБЕДА! · Причина: миссия выполнена",
            text,
        )
        self.assertIn(
            "Ходов: 7 · Корпус: 45/100 · Щиты: 30/100 · Угроза: 60/100",
            text,
        )
        self.assertIn("Погибли: Иван Петров, Мария · Выжили: 4 из 6", text)
        self.assertIn("Действия:", text)
        self.assertIn("Алексей — 7 (промедления 2)", text)
        self.assertIn("Иван Петров — 5 (промедления 1)", text)

    def test_en_summary_contains_all_blocks(self):
        text = format_game_summary(LANGUAGE_EN, **self._args())
        self.assertIn("📊 Mission Summary", text)
        self.assertIn(
            "Outcome: 🏆 МИССИЯ ВЫПОЛНЕНА — ПОБЕДА! · Reason: mission accomplished",
            text,
        )
        self.assertIn(
            "Turns: 7 · Hull: 45/100 · Shields: 30/100 · Threat: 60/100",
            text,
        )
        self.assertIn("Lost: Иван Петров, Мария · Survived: 4 of 6", text)
        self.assertIn("Actions:", text)
        self.assertIn("Алексей — 7 (delays 2)", text)
        self.assertIn("Иван Петров — 5 (delays 1)", text)

    def test_no_dead_shows_placeholder(self):
        args = self._args()
        args["dead_names"] = []
        self.assertIn("Погибли: нет", format_game_summary(LANGUAGE_RU, **args))
        self.assertIn("Lost: none", format_game_summary(LANGUAGE_EN, **args))

    def test_markdown_specials_in_names_are_escaped(self):
        args = self._args()
        args["dead_names"] = ["A_B [x]"]
        text = format_game_summary(LANGUAGE_RU, **args)
        # Only the link-opening '[' is special in legacy Telegram Markdown
        # (same rule as push_server._escape_md); ']' stays literal.
        self.assertIn("A\\_B \\[x]", text)


if __name__ == "__main__":
    unittest.main()
