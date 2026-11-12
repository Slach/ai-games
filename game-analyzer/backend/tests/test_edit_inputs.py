"""Edit-inputs parsing for edit-kind ComfyUI requests (node detail payload).

Fixtures use the exact section format of the detailed comfyui request logs
(mirrored verbatim into generation_events.request_text by logging_utils):
Model/Size/prefix/Attempt header, then --- INSTRUCTION ---, --- CHARACTER
AVATAR ---, --- BACKGROUND ---, --- POSE REFERENCE --- (optional, '(none)'
when no skeleton was picked) and --- WORKFLOW JSON --- sections.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import graph_builder  # noqa: E402
import node_detail  # noqa: E402
from image_utils import comfyui_url_to_path, image_endpoint_path  # noqa: E402
from tests.test_graph_builder import (  # noqa: E402
    BOT_SCHEMA,
    GAME_SERVER_SCHEMA,
    GID,
    _seed,
    _seed_bot,
)

FULL_REQUEST = """Model: Qwen-Image-2.1 Viggle Turbo edit (v0.2.1 LoRA, 6-step DMD)
Size: 1024x1024
Filename prefix: ixisyr/action_turn10_p281412419
Attempt: 1/2

--- INSTRUCTION ---
Place the character from Picture 1 at the central navigation console, adopting the exact pose shown in Picture 3.

--- CHARACTER AVATAR ---
ixisyr/avatar_281412419_00001_.png

--- BACKGROUND ---
ixisyr/turn_bg_loc_witness_orbit_black_hole_zone_00001_.png

--- POSE REFERENCE ---
pose_lib_console_side.png

--- WORKFLOW JSON ---
{}
"""

NONE_POSE_REQUEST = FULL_REQUEST.replace("pose_lib_console_side.png", "(none)")

MISSING_BACKGROUND_REQUEST = FULL_REQUEST.replace(
    "--- BACKGROUND ---\nixisyr/turn_bg_loc_witness_orbit_black_hole_zone_00001_.png\n\n",
    "",
)

OUTPUT_URL = (
    "http://comfyui:8188/view?filename=action_turn10_p281412419_00001_.png"
    "&subfolder=ixisyr&type=output"
)


def _event(request_text, kind="player_action", backend="comfyui", image_url=OUTPUT_URL):
    return {
        "backend": backend,
        "kind": kind,
        "request_text": request_text,
        "image_url": image_url,
    }


class TestEditInputsParser(unittest.TestCase):
    def test_full_request(self):
        edit = node_detail._edit_inputs(_event(FULL_REQUEST))
        self.assertIsNotNone(edit)
        self.assertTrue(edit["instruction"].startswith("Place the character from Picture 1"))
        self.assertEqual(edit["avatar"]["filename"], "ixisyr/avatar_281412419_00001_.png")
        self.assertEqual(edit["avatar"]["image_url"], "/api/image?path=ixisyr/avatar_281412419_00001_.png")
        self.assertEqual(edit["background"]["filename"], "ixisyr/turn_bg_loc_witness_orbit_black_hole_zone_00001_.png")
        self.assertEqual(edit["pose"]["filename"], "pose_lib_console_side.png")
        # Pose skeletons sit at the comfyui volume root (no game subfolder).
        self.assertEqual(edit["pose"]["image_url"], "/api/image?path=pose_lib_console_side.png")
        self.assertEqual(edit["output_image_url"], "/api/image?path=ixisyr/action_turn10_p281412419_00001_.png")

    def test_none_pose(self):
        edit = node_detail._edit_inputs(_event(NONE_POSE_REQUEST))
        self.assertIsNotNone(edit)
        self.assertIsNone(edit["pose"])
        self.assertIsNotNone(edit["avatar"])
        self.assertIsNotNone(edit["background"])

    def test_missing_background_section(self):
        edit = node_detail._edit_inputs(_event(MISSING_BACKGROUND_REQUEST))
        self.assertIsNotNone(edit)
        self.assertIsNone(edit["background"])
        self.assertIsNotNone(edit["avatar"])

    def test_bare_avatar_filename(self):
        request = FULL_REQUEST.replace("ixisyr/avatar_281412419_00001_.png", "avatar_281412419_00001_.png")
        edit = node_detail._edit_inputs(_event(request))
        self.assertEqual(edit["avatar"]["filename"], "avatar_281412419_00001_.png")
        self.assertEqual(edit["avatar"]["image_url"], "/api/image?path=avatar_281412419_00001_.png")

    def test_only_edit_kinds_and_comfyui_backend(self):
        self.assertIsNone(node_detail._edit_inputs(_event(FULL_REQUEST, kind="scene")))
        self.assertIsNone(node_detail._edit_inputs(_event(FULL_REQUEST, backend="llm")))
        self.assertIsNone(node_detail._edit_inputs(_event("", image_url="")))

    def test_root_filenames_are_servable(self):
        self.assertEqual(image_endpoint_path("pose_lib_console_side.png"), "/api/image?path=pose_lib_console_side.png")
        self.assertEqual(image_endpoint_path("ixisyr/avatar_1_.png"), "/api/image?path=ixisyr/avatar_1_.png")
        self.assertEqual(
            comfyui_url_to_path("http://comfyui:8188/view?filename=pose_lib_x.png&type=output"),
            "pose_lib_x.png",
        )


class TestEditInputsNodeDetail(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        gs = Path(self._tmpdir.name) / "game_server.db"
        bot = Path(self._tmpdir.name) / "player_states.db"
        conn = sqlite3.connect(gs)
        conn.executescript(GAME_SERVER_SCHEMA)
        _seed(conn)
        conn.close()
        conn = sqlite3.connect(bot)
        conn.executescript(BOT_SCHEMA)
        _seed_bot(conn)
        conn.close()
        self._old = (config.GAME_SERVER_DB, config.BOT_DB, config.COMFYUI_FILES)
        config.GAME_SERVER_DB = str(gs)
        config.BOT_DB = str(bot)
        config.COMFYUI_FILES = str(Path(self._tmpdir.name))
        graph_builder._graph_cache.clear()

    def tearDown(self):
        config.GAME_SERVER_DB, config.BOT_DB, config.COMFYUI_FILES = self._old
        graph_builder._graph_cache.clear()
        self._tmpdir.cleanup()

    def test_actionimg_step_carries_edit_inputs(self):
        detail = node_detail.get_node_detail(GID, f"actionimg:{GID}:1:p111")
        self.assertIsNotNone(detail)
        step = next(
            s for s in detail["chain"]
            if s["backend"] == "comfyui" and s["kind"] == "player_action"
        )
        edit = step["edit_inputs"]
        self.assertEqual(edit["instruction"], "character at the wreck")
        self.assertEqual(edit["avatar"]["filename"], f"{GID}/avatar_111_00001_.png")
        self.assertIn(f"path={GID}/avatar_111_00001_.png", edit["avatar"]["image_url"])
        self.assertEqual(edit["background"]["filename"], f"{GID}/turn_bg_turn1_00001_.png")
        # The fixture request has no POSE REFERENCE section → null pose.
        self.assertIsNone(edit["pose"])
        self.assertIn("action_turn1_p111_00001_.png", edit["output_image_url"])
        llm_steps = [s for s in detail["chain"] if s["backend"] == "llm"]
        self.assertTrue(llm_steps)
        self.assertTrue(all(s["edit_inputs"] is None for s in llm_steps))

    def test_npc_action_step_without_background(self):
        detail = node_detail.get_node_detail(GID, f"actionimg:{GID}:1:n:engineer")
        step = next(s for s in detail["chain"] if s["kind"] == "npc_action")
        edit = step["edit_inputs"]
        self.assertEqual(edit["avatar"]["filename"], f"{GID}/avatar_engineer_00001_.png")
        # The orphan NPC fixture request has no BACKGROUND section.
        self.assertIsNone(edit["background"])
        self.assertEqual(edit["instruction"], "engineer acts")


if __name__ == "__main__":
    unittest.main()
