"""Tests for the crew-count denominator and death-notice roster in the outcome push.

Crew count: reproduces the epl2yq double-count — an NPC that replaced a player
still registered in the game is the same crew seat as that player, so it must
not be added to the total denominator (otherwise "9 из 11" instead of "9 из 10").

Death notices: reproduces the ephemeral-death bug — death_notices must be a
persistent roster rebuilt from DB state (dead players + deactivated NPCs) every
turn, the same way injuries are, not only the current turn's new deaths.
"""

import os
import sys
import tempfile
import logging
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

logger = logging.getLogger(__name__)


def _make_player(player_id, game_id, role="Pilot", name="Test"):
    db.create_player_profile(
        {
            "player_id": player_id,
            "role": role,
            "game_id": game_id,
            "player_name": name,
        }
    )


def _make_npc(npc_key, game_id, role="Engineer", name="NPC Eng", **extra):
    data = {
        "npc_key": npc_key,
        "role_key": extra.get("role_key", role),
        "npc_name": name,
        "role": role,
        "game_id": game_id,
    }
    data.update(extra)
    db.create_npc_profile(data)


class TestCrewCountDenominator(unittest.TestCase):
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

    def _compute_total(self, game_id):
        """Mirror of main.py's total_crew formula after the dedup fix."""
        all_players_total = db.get_players_in_game(game_id)
        all_npcs_total = db.get_all_npcs(game_id)
        player_ids = set(all_players_total)
        distinct_npc_total = [
            n for n in all_npcs_total if n.get("replaces_player_id") not in player_ids
        ]
        return len(all_players_total) + len(distinct_npc_total)

    def test_replaced_player_npc_excluded_from_total(self):
        # epl2yq scenario: one live player, one dead player (Science Officer),
        # plus an inactive NPC that replaced the dead player (same seat),
        # plus one independent active NPC.
        _make_player(100, "g1", role="Капитан", name="КхаГар")
        _make_player(200, "g1", role="Научный офицер", name="Ftgz")
        db.mark_player_dead(200, "g1")  # Ftgz dies but stays registered in the game
        # Inactive NPC holding the same Science Officer seat as the dead player.
        _make_npc(
            "npc_sci_g1", "g1", role="Научный офицер", name="Квазар-Вектор",
            is_active=False, replaces_player_id=200,
        )
        # Independent active NPC — a distinct seat.
        _make_npc("npc_eng_g1", "g1", role="Инженер-механик", name="Базальт-Шесть")

        # Without dedup this would be 2 players + 2 NPCs = 4.
        # With dedup the replaced seat is counted once → 2 + 1 = 3.
        self.assertEqual(self._compute_total("g1"), 3)

    def test_active_independent_npcs_all_counted(self):
        _make_player(1, "g2")
        _make_npc("npc_a", "g2", role="A")
        _make_npc("npc_b", "g2", role="B")
        _make_npc("npc_c", "g2", role="C")
        self.assertEqual(self._compute_total("g2"), 4)

    def test_kicked_player_npc_counts_as_distinct(self):
        # When a player is kicked (game_id set to NULL), the NPC that replaced
        # them is a distinct seat and must be counted.
        _make_player(5, "g3", role="Pilot", name="Kicked")
        db.leave_game(5)  # sets game_id = NULL
        _make_npc(
            "npc_pilot_g3", "g3", role="Pilot", name="Replacement",
            is_active=True, replaces_player_id=5,
        )
        # Player no longer in game (game_id NULL) → only the NPC counts.
        self.assertEqual(self._compute_total("g3"), 1)


class TestDeathNoticeRoster(unittest.TestCase):
    """Death notices must be a persistent DB-derived roster, not ephemeral."""

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

    def _build_death_notices(self, game_id):
        """Mirror of main.py's death_notices roster construction."""
        all_npcs_total = db.get_all_npcs(game_id)
        player_ids = set(db.get_players_in_game(game_id))
        notices = []
        for dead_pid in db.get_dead_players(game_id):
            p = db.get_player_profile(dead_pid)
            if p:
                notices.append({"name": p.get("player_name") or str(dead_pid), "role": p.get("role", "")})
        for n in all_npcs_total:
            if n.get("is_active"):
                continue
            if n.get("replaces_player_id") in player_ids:
                continue
            notices.append({"name": n.get("npc_name", ""), "role": n.get("role", "")})
        return notices

    def test_dead_player_and_replaced_npc_is_one_notice(self):
        # epl2yq scenario: dead player (Science Officer) + an inactive NPC that
        # replaced that same player (same seat). Only ONE notice must appear.
        _make_player(200, "g1", role="Научный офицер", name="Ftgz")
        db.mark_player_dead(200, "g1")
        _make_npc(
            "npc_sci_g1", "g1", role="Научный офицер", name="Квазар-Вектор",
            is_active=False, replaces_player_id=200,
        )
        notices = self._build_death_notices("g1")
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["role"], "Научный офицер")

    def test_story_killed_npc_shows_separately(self):
        # A dead player plus a story-killed NPC (never replaced a player) → two
        # distinct dead seats, two notices.
        _make_player(100, "g2", role="Капитан", name="A")
        db.mark_player_dead(100, "g2")
        _make_npc("npc_eng_g2", "g2", role="Инженер", name="Eng", is_active=False)
        notices = self._build_death_notices("g2")
        roles = sorted(n["role"] for n in notices)
        self.assertEqual(roles, ["Инженер", "Капитан"])

    def test_notices_persist_with_no_new_deaths(self):
        # The roster is derived from DB state, so it appears every turn even
        # when no new death occurred — mirroring how injuries behave.
        _make_player(300, "g3", role="Пилот", name="P")
        db.mark_player_dead(300, "g3")
        # Simulate a later turn: no changes to DB, rebuild roster again.
        notices_turn_after = self._build_death_notices("g3")
        self.assertEqual(len(notices_turn_after), 1)
        self.assertEqual(notices_turn_after[0]["name"], "P")

    def test_alive_crew_produces_no_notices(self):
        _make_player(400, "g4", role="Пилот", name="Alive")
        _make_npc("npc_eng_g4", "g4", role="Инженер", name="Eng", is_active=True)
        self.assertEqual(self._build_death_notices("g4"), [])


if __name__ == "__main__":
    unittest.main()
