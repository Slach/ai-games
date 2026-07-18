"""Regression test for ghost NPCs left behind when a reset player re-onboards.

Reproduces the default_game "4 players" bug: a player reset twice (player_kicks
shows two entries) left an active replacement NPC on their first role
(security_chief), then re-onboarded into a different role (chief_engineer).
take_role() only deactivates NPCs for the NEW role_key, so the old NPC stayed
active forever and duplicated the player in /game/team and turn generation.
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


class TestGhostNpcOnReturn(unittest.TestCase):
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

    def test_reset_then_reonboard_different_role_deactivates_ghost_npc(self):
        # default_game scenario: player 281412419 first takes security_chief.
        pid = 281412419
        game_id = "default_game"
        _make_player(pid, game_id, role="Начальник безопасности", name="КхаГар")
        db.take_role("security_chief", pid, game_id)

        # /reset: NPC replaces the player on security_chief, profile wiped.
        _make_npc(
            "npc_security_chief_default_game", game_id,
            role="Начальник безопасности", name="Kha'Ghar",
            role_key="security_chief", is_active=True, replaces_player_id=pid,
        )
        db.release_role("security_chief", game_id)
        db.delete_player_profile(pid)

        # Player re-onboards into a DIFFERENT role (chief_engineer).
        _make_player(pid, game_id, role="Инженер-механик", name="КхаГар")
        db.take_role("chief_engineer", pid, game_id)
        # The call added in complete_onboarding to clear the ghost.
        deactivated = db.deactivate_replacement_npcs_for_player(pid, game_id)

        self.assertEqual(deactivated, 1)
        ghost = db.get_npc_profile("npc_security_chief_default_game")
        self.assertFalse(ghost["is_active"])

    def test_no_ghost_when_player_never_reset(self):
        # A fresh player with no replacement NPC → deactivates nothing.
        pid = 111
        game_id = "g1"
        _make_player(pid, game_id, role="Капитан", name="A")
        db.take_role("captain", pid, game_id)

        self.assertEqual(db.deactivate_replacement_npcs_for_player(pid, game_id), 0)

    def test_scoped_to_returning_players_game(self):
        # A replacement NPC in a DIFFERENT game must NOT be touched.
        pid = 222
        _make_npc(
            "npc_sec_other", "other_game",
            role="Начальник безопасности", name="Ghost",
            role_key="security_chief", is_active=True, replaces_player_id=pid,
        )

        deactivated = db.deactivate_replacement_npcs_for_player(pid, "default_game")
        self.assertEqual(deactivated, 0)
        self.assertTrue(db.get_npc_profile("npc_sec_other")["is_active"])

    def test_kicked_player_returning_to_same_game_deactivates_ghost(self):
        # A kicked player who later re-onboards into the SAME game leaves their
        # old replacement NPC as a ghost — the crew-count dedup (main.py) already
        # treats it as the same seat as the returning player, and /game/team
        # would duplicate them. Like the reset case, the ghost must be cleared.
        pid = 333
        game_id = "g3"
        _make_player(pid, game_id, role="Пилот", name="Kicked")
        db.take_role("pilot", pid, game_id)
        _make_npc(
            "npc_pilot_g3", game_id, role="Пилот", name="Replacement",
            role_key="pilot", is_active=True, replaces_player_id=pid,
        )
        # Kick: player leaves, NPC holds the seat while they're gone.
        db.leave_game(pid)
        self.assertTrue(db.get_npc_profile("npc_pilot_g3")["is_active"])

        # Player returns to the same game (re-onboarding) — ghost is cleared.
        deactivated = db.deactivate_replacement_npcs_for_player(pid, game_id)
        self.assertEqual(deactivated, 1)
        self.assertFalse(db.get_npc_profile("npc_pilot_g3")["is_active"])


class TestTeamRosterExcludesGhost(unittest.TestCase):
    """The /game/team roster must not list a ghost NPC that replaced a player
    who has since returned to the same game — otherwise the player is duplicated
    in the roster (the original "4 players" symptom)."""

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

    def _team_npc_keys(self, game_id):
        """Mirror of /game/team's NPC filter after the ghost-exclusion fix."""
        conn = db.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT n.npc_key FROM npc_profiles n "
            "LEFT JOIN ship_roles sr ON sr.role_key = n.role_key AND sr.game_id = n.game_id "
            "WHERE n.game_id = ? AND (n.is_active = 1 OR sr.taken_by IS NULL) "
            "AND n.replaces_player_id NOT IN ("
            "  SELECT player_id FROM player_profiles p WHERE p.game_id = n.game_id"
            ")",
            (game_id,),
        )
        keys = [row["npc_key"] for row in cursor.fetchall()]
        conn.close()
        return keys

    def test_returning_players_ghost_excluded_from_roster(self):
        # default_game: three live players (including one who previously held
        # security_chief and was reset), plus the deactivated ghost NPC that
        # replaced them. The roster must NOT list the ghost.
        game_id = "default_game"
        _make_player(281412419, game_id, role="Инженер-механик", name="КхаГар")
        _make_player(535628479, game_id, role="Научный офицер", name="ОООсики")
        _make_player(6734467915, game_id, role="Капитан", name="Пипкука")
        _make_npc(
            "npc_security_chief_default_game", game_id,
            role="Начальник безопасности", name="Kha'Ghar",
            role_key="security_chief", is_active=False, replaces_player_id=281412419,
        )

        self.assertNotIn("npc_security_chief_default_game", self._team_npc_keys(game_id))

    def test_independent_inactive_npc_still_listed(self):
        # An NPC that replaced a DIFFERENT, still-absent player (or no player at
        # all) must remain in the roster as a dead crew member — only the ghost
        # of a RETURNING player is hidden.
        game_id = "g1"
        _make_player(100, game_id, role="Капитан", name="A")
        _make_npc(
            "npc_eng_g1", game_id, role="Инженер", name="Eng",
            role_key="engineer", is_active=False, replaces_player_id=999,
        )

        self.assertIn("npc_eng_g1", self._team_npc_keys(game_id))


if __name__ == "__main__":
    unittest.main()
