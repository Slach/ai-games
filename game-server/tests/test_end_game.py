"""Regression test: end_game() must mark the game as ended in the `games` table.

Historically end_game() only updated game_state.status, leaving games.status at its
creation default 'active' forever. Since /admin/list-games (and thus Telegram /gm_list)
reads games.status, finished games kept showing up as active. game_state.status is the
live per-turn state; games.status is the lifecycle flag read by list/filter queries, so
end_game() must keep both in sync.
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


class TestEndGameMarksGamesTable(unittest.TestCase):
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

    def _make_game(self, game_id: str) -> None:
        db.create_game(
            {
                "game_id": game_id,
                "name": "Test Game",
                "setting": "starship",
                "language": "ru",
            }
        )

    def test_end_game_marks_games_status_ended(self):
        self._make_game("g1")
        before = db.get_game("g1")
        if before is None:
            self.fail("game g1 was not created")
        self.assertEqual(before["status"], "active")

        db.end_game("mission_complete", game_id="g1")

        after = db.get_game("g1")
        if after is None:
            self.fail("game g1 was not persisted")
        self.assertEqual(after["status"], "ended")

    def test_end_game_sets_game_state_reason(self):
        self._make_game("g2")
        db.end_game("ship_destroyed", game_id="g2")

        state = db.get_game_state("g2")
        self.assertEqual(state["status"], "ship_destroyed")
        self.assertFalse(state["ship_alive"])


class TestCrewWipedEndsGame(unittest.TestCase):
    """crew_wiped must be reachable: when every player is dead and every NPC
    deactivated, _analyze_turn_outcome's check (main.py) fires and end_game
    marks the game ended in both tables. With the NPC pool capped at
    NPC_COUNT and no NPC replacement for departing players, the crew can
    actually die out — previously an NPC refilled every vacated seat."""

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

    def _make_game(self, game_id: str) -> None:
        db.create_game(
            {
                "game_id": game_id,
                "name": "Test Game",
                "setting": "starship",
                "language": "ru",
            }
        )

    def _crew_wiped(self, game_id: str) -> bool:
        """Mirror of the crew_wiped check in _analyze_turn_outcome (main.py)."""
        live_players = db.get_live_players(game_id)
        active_npcs = db.get_all_active_npcs(game_id)
        return len(live_players) == 0 and len(active_npcs) == 0

    def test_crew_wiped_when_all_players_dead_and_npcs_deactivated(self):
        game_id = "g1"
        self._make_game(game_id)
        db.create_player_profile(
            {"player_id": 1, "role": "Пилот", "game_id": game_id, "player_name": "A"}
        )
        db.create_player_profile(
            {"player_id": 2, "role": "Медик", "game_id": game_id, "player_name": "B"}
        )
        db.create_npc_profile({"npc_key": "npc_pilot_g1", "role": "Пилот", "game_id": game_id, "npc_name": "N1"})
        db.create_npc_profile({"npc_key": "npc_eng_g1", "role": "Инженер", "game_id": game_id, "npc_name": "N2"})

        # Everyone dies: players via mark_player_dead, NPCs via deactivate_npc
        # (exactly what _analyze_turn_outcome does for dead_crew_members).
        db.mark_player_dead(1, game_id)
        db.mark_player_dead(2, game_id)
        db.deactivate_npc("npc_pilot_g1")
        db.deactivate_npc("npc_eng_g1")

        self.assertTrue(self._crew_wiped(game_id))
        db.end_game("crew_wiped", game_id=game_id)

        self.assertEqual(db.get_game_state(game_id)["status"], "crew_wiped")
        self.assertEqual(db.get_game(game_id)["status"], "ended")

    def test_not_wiped_while_an_npc_still_active(self):
        game_id = "g2"
        self._make_game(game_id)
        db.create_player_profile(
            {"player_id": 1, "role": "Пилот", "game_id": game_id, "player_name": "A"}
        )
        db.create_npc_profile({"npc_key": "npc_eng_g2", "role": "Инженер", "game_id": game_id, "npc_name": "N1"})

        db.mark_player_dead(1, game_id)
        db.deactivate_npc("npc_eng_g2")
        # One NPC survives (story spared it) → no wipe.
        db.create_npc_profile({"npc_key": "npc_med_g2", "role": "Медик", "game_id": game_id, "npc_name": "N2"})

        self.assertFalse(self._crew_wiped(game_id))
        self.assertEqual(db.get_game_state(game_id)["status"], "active")

    def test_not_wiped_while_a_player_alive(self):
        game_id = "g3"
        self._make_game(game_id)
        db.create_player_profile(
            {"player_id": 1, "role": "Пилот", "game_id": game_id, "player_name": "A"}
        )
        db.create_npc_profile({"npc_key": "npc_eng_g3", "role": "Инженер", "game_id": game_id, "npc_name": "N1"})

        db.deactivate_npc("npc_eng_g3")
        # Player still alive → no wipe even with zero NPCs.
        self.assertFalse(self._crew_wiped(game_id))
        self.assertEqual(db.get_game_state(game_id)["status"], "active")


if __name__ == "__main__":
    unittest.main()
