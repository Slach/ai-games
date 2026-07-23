"""Tests for late-join NPC briefing inheritance.

When a player completes onboarding into an already-running game and takes a
role held by an active NPC, the NPC's briefing for the current turn is cloned
into the player's slot (auto-choice cleared so the player chooses themselves)
and the original NPC row is removed so the turn outcome resolves only the
player's decision — no double-counting.
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


def _make_player(player_id, game_id, role="Научный офицер", name="Test"):
    db.create_player_profile(
        {
            "player_id": player_id,
            "role": role,
            "game_id": game_id,
            "player_name": name,
        }
    )


def _make_npc_briefing(turn, npc_key, game_id, *, selected="action_1"):
    """An NPC briefing comes pre-chosen (auto-choice)."""
    return {
        "turn": turn,
        "player_id": None,
        "npc_key": npc_key,
        "is_npc": True,
        "briefing": "Вера Стервятник игнорирует визуальные галлюцинации.",
        "choices": [
            {"id": "action_1", "text": "Взломать протокол фильтрации ИИ", "consequence": "Системы мостика гаснут.", "consequence_kind": "good"},
            {"id": "action_2", "text": "Синхронизировать сенсоры с вибрациями корпуса", "consequence": "Успешная синхронизация.", "consequence_kind": "good"},
        ],
        "selected_action_id": selected,
        "choice_rationale": "Мой разум требует чистоты данных.",
        "consequence_result": {"consequence": "Системы мостика гаснут.", "consequence_kind": "good"},
        "chosen_action_url": "http://comfyui:8188/view?filename=action_npc.png",
        "personal_title": "",
        "image_prompt": "",
    }


class TestInheritNpcBriefing(unittest.TestCase):
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

    def test_get_npc_briefing_finds_npc_row(self):
        db.save_player_briefing(_make_npc_briefing(3, "npc_science_officer_g1", "g1"), "g1")
        got = db.get_npc_briefing(3, "npc_science_officer_g1", "g1")
        self.assertIsNotNone(got)
        self.assertTrue(got["is_npc"])
        self.assertEqual(got["selected_action_id"], "action_1")

    def test_get_npc_briefing_ignores_player_rows(self):
        # A player row must not be returned by the NPC lookup.
        db.save_player_briefing(
            {
                "turn": 3, "player_id": 100, "npc_key": None, "is_npc": False,
                "briefing": "p", "choices": [],
            },
            "g1",
        )
        self.assertIsNone(db.get_npc_briefing(3, "npc_science_officer_g1", "g1"))

    def test_delete_briefing_removes_npc_row_only(self):
        db.save_player_briefing(_make_npc_briefing(3, "npc_x_g1", "g1"), "g1")
        deleted = db.delete_briefing(3, "npc_x_g1", "g1")
        self.assertTrue(deleted)
        self.assertIsNone(db.get_npc_briefing(3, "npc_x_g1", "g1"))

    def test_delete_briefing_returns_false_when_nothing_matched(self):
        self.assertFalse(db.delete_briefing(9, "npc_missing_g1", "g1"))

    def test_inherit_flow_no_double_count_and_choice_cleared(self):
        """The full late-join inheritance: NPC briefing cloned to player slot,
        NPC row deleted, get_all_briefings_for_turn sees exactly one row for the
        role, and the inherited player briefing has its choice cleared."""
        game_id = "7hkua6"
        pid = 231113575
        npc_key = "npc_science_officer_7hkua6"
        turn = 3

        # Player takes the science_officer role (NPC exists but is active).
        _make_player(pid, game_id, role="Научный офицер", name="Draigon")
        db.take_role("science_officer", pid, game_id)
        # take_role deactivates the NPC for that role.
        # Save the NPC's pre-chosen briefing for the current turn.
        db.save_player_briefing(_make_npc_briefing(turn, npc_key, game_id), game_id)

        # --- Inheritance logic (mirrors _inherit_npc_briefing_for_player) ---
        npc_briefing = db.get_npc_briefing(turn, npc_key, game_id)
        self.assertIsNotNone(npc_briefing, "NPC briefing must exist to inherit")
        db.save_player_briefing(
            {
                "turn": turn,
                "player_id": pid,
                "npc_key": None,
                "is_npc": False,
                "briefing": npc_briefing["briefing"],
                "choices": npc_briefing.get("choices", []),
                "selected_action_id": None,
                "choice_rationale": "",
                "consequence_result": {},
                "chosen_action_url": None,
                "personal_title": npc_briefing.get("personal_title", ""),
                "image_prompt": npc_briefing.get("image_prompt", ""),
            },
            game_id,
        )
        db.delete_briefing(turn, npc_key, game_id)
        # ---------------------------------------------------------------------

        # NPC row is gone.
        self.assertIsNone(db.get_npc_briefing(turn, npc_key, game_id))

        # Player row exists, choice cleared so poll() returns it as "not chosen".
        inherited = db.get_player_briefing(turn, pid, game_id)
        self.assertIsNotNone(inherited, "player must have inherited the briefing")
        self.assertFalse(inherited["is_npc"])
        self.assertIsNone(inherited["selected_action_id"])
        self.assertEqual(inherited["consequence_result"], {})
        self.assertIsNone(inherited["chosen_action_url"])
        # Briefing text and choices preserved verbatim.
        self.assertEqual(inherited["briefing"], "Вера Стервятник игнорирует визуальные галлюцинации.")
        self.assertEqual(len(inherited["choices"]), 2)

        # Critical: the turn outcome resolver emits ONE decision per briefing
        # row, so there must be exactly one row for this role on this turn.
        turn_rows = db.get_all_briefings_for_turn(turn, game_id)
        role_rows = [
            b for b in turn_rows
            if b.get("player_id") == pid or b.get("npc_key") == npc_key
        ]
        self.assertEqual(len(role_rows), 1, "NPC + inherited player must not double-count")
        self.assertEqual(role_rows[0]["player_id"], pid)

    def test_inherit_noop_when_no_npc_briefing(self):
        # If the NPC never generated a briefing for the turn, inheritance is a no-op:
        # nothing is cloned and no player row appears.
        game_id = "g2"
        pid = 200
        _make_player(pid, game_id)
        db.take_role("science_officer", pid, game_id)
        self.assertIsNone(db.get_npc_briefing(1, "npc_science_officer_g2", game_id))
        self.assertIsNone(db.get_player_briefing(1, pid, game_id))


if __name__ == "__main__":
    unittest.main()
