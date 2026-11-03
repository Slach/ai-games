"""Tests for generation_events structured logging (migration 25) + backfill."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_events  # noqa: E402
import database as db  # noqa: E402
import logging_utils  # noqa: E402


class TestGenerationEvents(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_request_response_lifecycle(self):
        db.record_generation_request(
            log_file="game_g1_player_42_turn1_player_briefing_llm",
            game_id="g1",
            player_id="42",
            turn=1,
            kind="player_briefing",
            stage="turn",
            backend="llm",
            content=(
                "Model: m1\nTemperature: 0.7\nMax tokens: 100\n"
                "Enable thinking: False\n\n--- SYSTEM PROMPT ---\nhello"
            ),
            model="m1",
            params="temp=0.7 max_tokens=100 thinking=False",
        )
        events = db.list_generation_events(game_id="g1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "pending")
        self.assertEqual(events[0]["model"], "m1")
        self.assertIn("temp=0.7", events[0]["params"])
        self.assertIn("max_tokens=100", events[0]["params"])

        db.record_generation_response(
            log_file="game_g1_player_42_turn1_player_briefing_llm",
            game_id="g1",
            player_id="42",
            turn=1,
            kind="player_briefing",
            stage="turn",
            backend="llm",
            content='Finish reason: stop\nUsage: 1p/2c/3t\n\n--- RESPONSE CONTENT ---\n{"a": 1}',
            finish_reason="stop",
            usage="1p/2c/3t",
        )
        events = db.list_generation_events(game_id="g1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "ok")
        self.assertEqual(events[0]["finish_reason"], "stop")
        self.assertEqual(events[0]["usage"], "1p/2c/3t")

    def test_retry_bumps_attempts_and_resets_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = logging_utils._ensure_logs_dir
            logging_utils._ensure_logs_dir = lambda: tmp
            try:
                for _ in range(2):
                    logging_utils.write_comfyui_log(
                        game_id="g1", player_id="42", turn="1", kind="scene",
                        log_type="request",
                        content=(
                            "Model: img\nSize: 1024x1024\nFilename prefix: g1/scene_turn1\n"
                            "Attempt: 1/2\n\n--- PROMPT ---\np"
                        ),
                    )
                logging_utils.write_comfyui_log(
                    game_id="g1", player_id="42", turn="1", kind="scene",
                    log_type="response",
                    content="URL: http://comfyui:8188/view?filename=a.png\nPrompt ID: x\n",
                )
            finally:
                logging_utils._ensure_logs_dir = original
        events = db.list_generation_events(game_id="g1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["attempts"], 2)
        self.assertEqual(events[0]["status"], "ok")
        self.assertEqual(events[0]["model"], "img")
        self.assertIn("size=1024x1024", events[0]["params"])
        self.assertIn("attempt=1/2", events[0]["params"])
        self.assertEqual(events[0]["image_url"], "http://comfyui:8188/view?filename=a.png")

    def test_fts_search_and_like_fallback(self):
        db.record_generation_request(
            log_file="game_g1_player_42_turn1_player_briefing_llm",
            game_id="g1", player_id="42", turn=1, kind="player_briefing",
            stage="turn", backend="llm",
            content="--- SYSTEM PROMPT ---\nThe zebra crosses the corridor",
        )
        db.record_generation_request(
            log_file="game_g1_player_43_turn1_player_briefing_llm",
            game_id="g1", player_id="43", turn=1, kind="player_briefing",
            stage="turn", backend="llm",
            content="--- SYSTEM PROMPT ---\nNothing relevant here",
        )
        hits = db.search_generation_events("zebra")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["player_id"], "42")
        self.assertIn("zebra", hits[0]["snippet"])
        # Mid-word fragment: FTS finds nothing, LIKE fallback must catch it
        hits = db.search_generation_events("ebra cro")
        self.assertGreaterEqual(len(hits), 1)

    def test_write_llm_log_hook_records_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = logging_utils._ensure_logs_dir
            logging_utils._ensure_logs_dir = lambda: tmp
            try:
                logging_utils.write_llm_log(
                    game_id="g2", player_id="", turn="0", kind="mission",
                    log_type="request",
                    content="Model: m\n\n--- SYSTEM PROMPT ---\nx",
                )
                logging_utils.write_llm_log(
                    game_id="g2", player_id="", turn="0", kind="mission",
                    log_type="response",
                    content="Finish reason: stop\nUsage: 1p/1c/2t\n\n--- RESPONSE CONTENT ---\ny",
                )
            finally:
                logging_utils._ensure_logs_dir = original
        events = db.list_generation_events(game_id="g2")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "mission")
        self.assertEqual(events[0]["stage"], "game_concept")
        self.assertEqual(events[0]["turn"], 0)
        self.assertEqual(events[0]["player_id"], "")
        self.assertEqual(events[0]["status"], "ok")

    def test_kind_to_stage_dynamic_kinds(self):
        self.assertEqual(logging_utils.kind_to_stage("npc_name_chief_engineer"), "npc")
        self.assertEqual(logging_utils.kind_to_stage("npc_avatar_prompts"), "npc")
        self.assertEqual(logging_utils.kind_to_stage("npc_avatar_security_chief"), "npc")
        self.assertEqual(logging_utils.kind_to_stage("unknown_thing"), "other")

    def test_backfill_import_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "game_g3_player_7_turn2_scene_comfyui_request.log"
            resp = Path(tmp) / "game_g3_player_7_turn2_scene_comfyui_response.log"
            req.write_text(
                "Model: img\nSize: 1024x1024\nFilename prefix: g3/scene\nAttempt: 1/2\n\n"
                "--- PROMPT ---\nA zebra on the bridge\n",
                encoding="utf-8",
            )
            resp.write_text(
                "URL: http://comfyui:8188/view?filename=s.png\nPrompt ID: pid\n",
                encoding="utf-8",
            )
            result = backfill_events.import_logs_dir(tmp)
            self.assertEqual(result["imported"], 2)
            self.assertEqual(result["skipped_unparsed"], 0)
            events = db.list_generation_events(game_id="g3")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["status"], "ok")
            self.assertEqual(events[0]["image_url"], "http://comfyui:8188/view?filename=s.png")
            self.assertEqual(events[0]["turn"], 2)
            # Re-run: both files skipped, no attempt inflation
            result2 = backfill_events.import_logs_dir(tmp)
            self.assertEqual(result2["imported"], 0)
            self.assertEqual(result2["skipped_existing"], 2)
            events = db.list_generation_events(game_id="g3")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["attempts"], 1)
            # Backfilled text is searchable
            hits = db.search_generation_events("zebra")
            self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main()
