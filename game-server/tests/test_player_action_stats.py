"""DB-level tests for player_action_stats analytics log."""

import os
import sys
import tempfile
import logging
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

logger = logging.getLogger(__name__)


def _query_all() -> list[dict]:
    conn = db.get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM player_action_stats ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class TestPlayerActionStats(unittest.TestCase):
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

    def test_table_exists_with_expected_columns(self):
        conn = db.get_db_connection()
        try:
            cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(player_action_stats)").fetchall()
            }
        finally:
            conn.close()
        self.assertEqual(
            cols,
            {
                "id", "game_id", "player_id", "turn", "action_id",
                "action_text", "consequence_kind", "crew_health", "created_at",
            },
        )

    def test_save_round_trips_all_fields(self):
        result = db.save_player_action_stats(
            game_id="g1",
            player_id=111,
            turn=2,
            action_id="action_3",
            action_text="Заложить заряд в реактор",
            consequence_kind="injury",
            crew_health=84,
        )
        self.assertEqual(result["game_id"], "g1")
        self.assertEqual(result["consequence_kind"], "injury")
        self.assertEqual(result["crew_health"], 84)

        rows = _query_all()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["game_id"], "g1")
        self.assertEqual(row["player_id"], 111)
        self.assertEqual(row["turn"], 2)
        self.assertEqual(row["action_id"], "action_3")
        self.assertEqual(row["action_text"], "Заложить заряд в реактор")
        self.assertEqual(row["consequence_kind"], "injury")
        self.assertEqual(row["crew_health"], 84)
        self.assertTrue(row["created_at"])

    def test_same_player_in_two_games_does_not_mix(self):
        """Stats for one game must not show up under another game's player history."""
        db.save_player_action_stats(
            game_id="default_game", player_id=222, turn=1,
            action_id="action_1", action_text="a", consequence_kind="progress", crew_health=100,
        )
        db.save_player_action_stats(
            game_id="wjptt8", player_id=222, turn=1,
            action_id="action_2", action_text="b", consequence_kind="fatal", crew_health=95,
        )

        conn = db.get_db_connection()
        try:
            default = conn.execute(
                "SELECT game_id, consequence_kind FROM player_action_stats "
                "WHERE player_id = ? AND game_id = ?",
                (222, "default_game"),
            ).fetchall()
            new = conn.execute(
                "SELECT game_id, consequence_kind FROM player_action_stats "
                "WHERE player_id = ? AND game_id = ?",
                (222, "wjptt8"),
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(len(default), 1)
        self.assertEqual(default[0]["consequence_kind"], "progress")
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["consequence_kind"], "fatal")

    def test_index_on_game_and_turn_exists(self):
        """The (game_id, turn) index must exist for per-turn slices."""
        conn = db.get_db_connection()
        try:
            idxs = {
                r["name"]
                for r in conn.execute("PRAGMA index_list(player_action_stats)").fetchall()
            }
        finally:
            conn.close()
        self.assertIn("idx_player_action_stats_game", idxs)
        self.assertIn("idx_player_action_stats_player", idxs)


class TestBriefingSchemaConsequenceKind(unittest.TestCase):
    """The LLM JSON schema must require consequence_kind on every choice,
    otherwise strict mode forbids the LLM from emitting it and the column
    is always empty."""

    def _choice_item_schema(self):
        # _get_player_briefing_schema doesn't read instance state when total_actions
        # is passed explicitly, so bypass __init__ (which constructs an OpenAI client).
        from game_server import GameServer

        dummy = GameServer.__new__(GameServer)
        schema = dummy._get_player_briefing_schema(3)
        return schema["json_schema"]["schema"]["properties"]["choices"]["items"]

    def test_choice_requires_consequence_kind(self):
        item = self._choice_item_schema()
        self.assertIn("consequence_kind", item["properties"])
        self.assertIn("consequence_kind", item["required"])

    def test_consequence_kind_is_enum(self):
        prop = self._choice_item_schema()["properties"]["consequence_kind"]
        self.assertEqual(prop["type"], "string")
        self.assertEqual(set(prop["enum"]), {"progress", "injury", "fatal"})

    def test_additional_properties_still_false(self):
        """Strict mode: required must list every property, and extra keys forbidden."""
        item = self._choice_item_schema()
        self.assertFalse(item.get("additionalProperties", True))
        self.assertEqual(
            set(item["required"]),
            {"id", "text", "consequence", "consequence_kind"},
        )


if __name__ == "__main__":
    unittest.main()
