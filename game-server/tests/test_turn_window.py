"""Turn-window tests: deadline storage, reminder targeting, deadline display.

The turn window is the time between scheduled generations. With unit 10 an
auto-timeout is honestly fixed as a "delay" consequence that raises the
threat — so the deadline must be visible in advance (stored on the turn,
rendered in briefings) and players who haven't chosen must get T-2h/T-30m
reminders. These tests cover the pieces testable without HTTP:

- deadline roundtrip in game_turns (create → get, and NULL when unknown)
- get_players_who_need_to_choose = the "whom to remind" definition used by
  /game/remind-turn (same one auto-action uses)
- format_deadline renders UTC via strftime("%Y-%m-%d %H:%M %Z") with no
  manual timezone concatenation
"""

import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
from push_client import format_deadline  # noqa: E402

logger = logging.getLogger(__name__)


class TurnWindowDBTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB %s", self._tmp.name, exc_info=True)

    def _make_game(self, game_id: str) -> None:
        db.create_game(
            {
                "game_id": game_id,
                "name": "Test Game",
                "setting": "starship",
                "language": "ru",
            }
        )

    def _make_turn_data(self, turn: int, deadline: str | None = None) -> dict:
        return {
            "turn": turn,
            "story": "Сигнал бедствия с окраинной станции.",
            "deadline": deadline,
        }


class TestDeadlineRoundtrip(TurnWindowDBTestCase):
    def test_deadline_stored_and_returned(self):
        self._make_game("g1")
        deadline = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
        db.create_game_turn(self._make_turn_data(1, deadline), "g1")

        turn = db.get_game_turn(1, "g1")
        if turn is None:
            self.fail("turn 1 was not persisted")
        self.assertEqual(turn["deadline"], deadline)

    def test_deadline_null_when_unknown(self):
        self._make_game("g2")
        db.create_game_turn(self._make_turn_data(1), "g2")

        turn = db.get_game_turn(1, "g2")
        if turn is None:
            self.fail("turn 1 was not persisted")
        self.assertIsNone(turn["deadline"])

    def test_recreate_keeps_deadline(self):
        """continue-game creates the turn record twice (early placeholder +
        final INSERT OR REPLACE) — both writes must carry the deadline."""
        self._make_game("g3")
        deadline = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
        db.create_game_turn(self._make_turn_data(2, deadline), "g3")
        db.create_game_turn(self._make_turn_data(2, deadline), "g3")

        turn = db.get_game_turn(2, "g3")
        if turn is None:
            self.fail("turn 2 was not persisted")
        self.assertEqual(turn["deadline"], deadline)


class TestWhomToRemind(TurnWindowDBTestCase):
    """/game/remind-turn targets get_players_who_need_to_choose — the same
    "hasn't chosen" definition auto-action uses (briefing without
    selected_action_id, excluding dead/spectator players)."""

    def _make_briefing(self, game_id: str, player_id: int, turn: int, selected: str | None = None) -> None:
        db.save_player_briefing(
            {
                "turn": turn,
                "player_id": player_id,
                "npc_key": None,
                "is_npc": False,
                "briefing": f"Брифинг игрока {player_id}",
                "choices": [{"id": "a1", "text": "Действие"}],
                "selected_action_id": selected,
            },
            game_id,
        )

    def test_only_pending_players_are_reminded(self):
        game_id = "g1"
        self._make_game(game_id)
        for pid, name in ((101, "A"), (102, "B"), (103, "C")):
            db.create_player_profile(
                {"player_id": pid, "role": "Пилот", "game_id": game_id, "player_name": name}
            )

        self._make_briefing(game_id, 101, turn=1, selected="a1")  # already chose
        self._make_briefing(game_id, 102, turn=1)  # pending → remind
        self._make_briefing(game_id, 103, turn=1)  # pending → remind

        pending = db.get_players_who_need_to_choose(1, game_id)
        self.assertEqual({b["player_id"] for b in pending}, {102, 103})

    def test_dead_player_not_reminded(self):
        game_id = "g2"
        self._make_game(game_id)
        db.create_player_profile(
            {"player_id": 201, "role": "Инженер", "game_id": game_id, "player_name": "D"}
        )
        self._make_briefing(game_id, 201, turn=1)  # never chose...

        db.mark_player_dead(201, game_id)  # ...but died before the deadline

        self.assertEqual(db.get_players_who_need_to_choose(1, game_id), [])

    def test_kicked_player_not_reminded(self):
        game_id = "g4"
        self._make_game(game_id)
        db.create_player_profile(
            {"player_id": 401, "role": "Пилот", "game_id": game_id, "player_name": "K"}
        )
        self._make_briefing(game_id, 401, turn=1)  # briefing left open...

        # ...but the GM kicked the player mid-turn: leave_game clears the
        # profile's game_id, so reminders/auto-action must skip them.
        db.release_role("pilot", game_id)
        db.record_kick(401, "bot was blocked", game_id=game_id)
        db.leave_game(401)

        self.assertEqual(db.get_players_who_need_to_choose(1, game_id), [])

    def test_reminder_targets_follow_new_turn(self):
        game_id = "g3"
        self._make_game(game_id)
        db.create_player_profile(
            {"player_id": 301, "role": "Медик", "game_id": game_id, "player_name": "E"}
        )
        self._make_briefing(game_id, 301, turn=1)
        self._make_briefing(game_id, 301, turn=2, selected="a1")

        # Turn 1 stays pending, turn 2 is closed out — reminders for turn 2
        # must not fire for a player who already chose there.
        self.assertEqual(db.get_players_who_need_to_choose(2, game_id), [])
        self.assertEqual(
            [b["player_id"] for b in db.get_players_who_need_to_choose(1, game_id)], [301]
        )


class TestFormatDeadline(unittest.TestCase):
    def test_utc_iso_renders_with_z(self):
        self.assertEqual(format_deadline("2026-08-30T12:00:00+00:00"), "2026-08-30 12:00 UTC")

    def test_naive_iso_treated_as_utc(self):
        self.assertEqual(format_deadline("2026-08-30T12:00:00"), "2026-08-30 12:00 UTC")

    def test_non_utc_offset_converted_to_utc(self):
        self.assertEqual(format_deadline("2026-08-30T15:00:00+03:00"), "2026-08-30 12:00 UTC")


if __name__ == "__main__":
    unittest.main()
