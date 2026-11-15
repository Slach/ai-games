"""Tests for the GM text-review payload builder (main._build_gm_text_review_payload).

The builder feeds the /push/gm-text-review flow — briefings + action choices
with hidden consequences stripped (the review messages go to feedback
subscriber players too) plus the previous turn's outcome narrative.
"""

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# main.py hardcodes a daily /app/logs file handler at import time — redirect
# it to a temp dir so the module loads on the host.
_orig_file_handler = logging.FileHandler


class _TempFileHandler(logging.FileHandler):
    def __init__(self, filename, *args, **kwargs):
        if str(filename).startswith("/app/logs/"):
            filename = os.path.join(tempfile.mkdtemp(), os.path.basename(filename))
        super().__init__(filename, *args, **kwargs)


logging.FileHandler = _TempFileHandler
import main  # noqa: E402

logging.FileHandler = _orig_file_handler

import database as db  # noqa: E402

GAME_ID = "g1"


def _player_briefing(turn: int = 2) -> dict:
    return {
        "turn": turn,
        "player_id": 100,
        "npc_key": None,
        "is_npc": False,
        "briefing": "Bridge alarms are screaming.",
        "choices": [
            {
                "id": "action_1",
                "text": "Reroute power to shields",
                "consequence": "Shields hold, but the warp core strains.",
                "consequence_kind": "progress",
            },
            {
                "id": "action_2",
                "text": "Board the escape pod",
                "consequence": "The pod is hit by debris.",
                "consequence_kind": "injury",
            },
            {
                "id": "action_3",
                "text": "Charge the anomaly",
                "consequence": "The bridge implodes.",
                "consequence_kind": "fatal",
            },
        ],
        "selected_action_id": None,
        "consequence_result": {},
        "personal_title": "Ivan — Captain — Command decisions",
        "image_prompt": "",
    }


def _npc_briefing(turn: int = 2) -> dict:
    return {
        "turn": turn,
        "player_id": None,
        "npc_key": f"npc_science_officer_{GAME_ID}",
        "is_npc": True,
        "briefing": "Sensor readings make no sense.",
        "choices": [
            {
                "id": "scan",
                "text": "Scan the anomaly",
                "consequence": "Hidden data recovered.",
                "consequence_kind": "progress",
            },
            {
                "id": "wait",
                "text": "Keep watching",
                "consequence": "Nothing happens.",
                "consequence_kind": "delay",
            },
        ],
        "selected_action_id": "scan",
        "consequence_result": {},
        "personal_title": "Spock — Science officer — Logic of the void",
        "image_prompt": "",
    }


class TestGmTextReviewPayload(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()
        db.create_player_profile({
            "player_id": 100,
            "role": "captain",
            "player_name": "Ivan",
            "game_id": GAME_ID,
        })
        db.create_npc_profile({
            "npc_key": f"npc_science_officer_{GAME_ID}",
            "npc_name": "Spock",
            "role": "science officer",
            "game_id": GAME_ID,
        })
        db.save_player_briefing(_player_briefing(), GAME_ID)
        db.save_player_briefing(_npc_briefing(), GAME_ID)
        db.create_game_turn({
            "turn": 1,
            "story": "The mission begins.",
            "combined_outcome": json.dumps({"outcome_narrative": "The crew survived the debris field."}),
        }, GAME_ID)
        db.create_game_turn({"turn": 2, "story": "Next wave incoming."}, GAME_ID)

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logging.getLogger(__name__).error(
                "Failed to remove temp DB: %s", self._tmp.name, exc_info=True
            )

    def test_payload_strips_consequences_and_keeps_kind(self):
        payload = main._build_gm_text_review_payload(GAME_ID, 2, "ru")
        player = next(e for e in payload["entries"] if e["player_id"] == 100)
        self.assertEqual(len(player["actions"]), 3)
        for action in player["actions"]:
            self.assertNotIn("consequence", action)
            self.assertNotIn("consequence_result", action)
            self.assertIn("kind", action)
            self.assertIn("text", action)
        self.assertEqual(
            [a["kind"] for a in player["actions"]], ["progress", "injury", "fatal"]
        )
        self.assertEqual(player["actions"][0]["text"], "Reroute power to shields")

    def test_slugs_and_profiles(self):
        payload = main._build_gm_text_review_payload(GAME_ID, 2, "ru")
        player = next(e for e in payload["entries"] if e["player_id"] == 100)
        npc = next(e for e in payload["entries"] if e["npc_key"])
        self.assertEqual(player["slug"], "p100")
        self.assertEqual(player["name"], "Ivan")
        self.assertEqual(player["role"], "captain")
        self.assertFalse(player["is_npc"])
        self.assertEqual(npc["slug"], "science_officer")
        self.assertEqual(npc["name"], "Spock")
        self.assertTrue(npc["is_npc"])
        self.assertEqual(npc["selected_action_id"], "scan")

    def test_outcome_narrative_from_previous_turn(self):
        payload = main._build_gm_text_review_payload(GAME_ID, 2, "ru")
        self.assertEqual(payload["outcome_narrative"], "The crew survived the debris field.")

    def test_first_turn_has_no_outcome(self):
        payload = main._build_gm_text_review_payload(GAME_ID, 1, "ru")
        self.assertNotIn("outcome_narrative", payload)
        self.assertEqual(len(payload["entries"]), 0)

    def test_malformed_outcome_json_is_skipped(self):
        db.update_game_turn_outcome(1, "not json at all", GAME_ID)
        payload = main._build_gm_text_review_payload(GAME_ID, 2, "ru")
        self.assertNotIn("outcome_narrative", payload)

    def test_briefing_without_identity_is_skipped(self):
        orphan = _player_briefing()
        orphan["player_id"] = None
        db.save_player_briefing(orphan, GAME_ID)
        payload = main._build_gm_text_review_payload(GAME_ID, 2, "ru")
        self.assertEqual(len(payload["entries"]), 2)


if __name__ == "__main__":
    unittest.main()
