"""Tests for the scene-stage comic (GM feedback 2026-11-14).

The scene stage used to be the shared empty turn backdrop while the crew
splits across compartments (reactor bay, medbay). It is now a stitched page of
the per-crew images (players' character images + NPCs' chosen-action images),
so no panel can drop, duplicate or drift a character — pure pixel stitching,
no image model in the loop.

Covers:
- scene_comic.stitch_panels: grid geometry per panel count + Telegram
  downscale;
- the Qwen-2.1 edit builders wire participants as contiguous Pictures right
  after the character/background/pose trio (no slot gaps, no node-id collision
  with QwenImage21Cache "45");
- _scene_image_for_turn prefers the freshly built scene_comic row over the
  shared backdrop.
"""

import io
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from comfyui_config import get_edit_model_config
from image_generator import (
    _build_qwen_edit_21_workflow,
    _build_qwen_edit_21_viggle_turbo_workflow,
)
import scene_comic
from language import LANGUAGE_EN

# main.py hardcodes a daily /app/logs file handler at import time — that
# directory only exists inside the container. Redirect file logging to a
# temp file for the duration of the import so the module loads on the host.
_orig_file_handler = logging.FileHandler


class _TempFileHandler(logging.FileHandler):
    def __init__(self, filename, *args, **kwargs):
        if str(filename).startswith("/app/logs/"):
            filename = os.path.join(tempfile.mkdtemp(), os.path.basename(filename))
        super().__init__(filename, *args, **kwargs)


logging.FileHandler = _TempFileHandler
import database as db  # noqa: E402
import main  # noqa: E402

logging.FileHandler = _orig_file_handler


def _png(width: int, height: int, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


class TestGroupDimensions(unittest.TestCase):
    """Every scene-stage picture is a wide strip; the group stretches wider
    proportionally to its size (GM feedback 2026-11-14 v3+v4): solo = 3:1,
    2 = 2:1, 3 = 3:1, 4+ clamped at 4:1."""

    def test_layouts(self):
        self.assertEqual(scene_comic.group_dimensions(1), (1536, 512))
        self.assertEqual(scene_comic.group_dimensions(2), (1536, 768))
        self.assertEqual(scene_comic.group_dimensions(3), (1536, 512))
        self.assertEqual(scene_comic.group_dimensions(5), (1536, 384))

    def test_heights_multiple_of_32(self):
        for k in range(1, 11):
            _, h = scene_comic.group_dimensions(k)
            self.assertEqual(h % 32, 0, f"k={k}")


class TestGroupCrewByLocation(unittest.TestCase):
    """Co-located characters share one scene-stage picture; entries without a
    location stay singletons."""

    def test_groups(self):
        group = main._group_crew_by_location([
            {"key": "a", "loc": "bridge"},
            {"key": "b", "loc": "medbay"},
            {"key": "c", "loc": "bridge"},
            {"key": "d", "loc": ""},
            {"key": "e"},
        ])
        self.assertEqual([[e["key"] for e in g] for g in group], [["a", "c"], ["b"], ["d"], ["e"]])

    def test_order_preserved(self):
        group = main._group_crew_by_location([
            {"key": "x", "loc": "medbay"},
            {"key": "y", "loc": "bridge"},
            {"key": "z", "loc": "medbay"},
        ])
        self.assertEqual([g[0]["loc"] for g in group], ["medbay", "bridge"])
        self.assertEqual([[e["key"] for e in g] for g in group], [["x", "z"], ["y"]])


class TestStitchPanels(unittest.TestCase):
    PANELS = [(_png(1536, 512, (255, 0, 0))), (_png(1536, 768, (0, 255, 0)))]

    def test_single_panel_native_size(self):
        page = Image.open(io.BytesIO(scene_comic.stitch_panels([_png(1024, 1024)])))
        self.assertEqual(page.size, (1024, 1024))

    def test_rows_scale_to_full_width(self):
        page = Image.open(io.BytesIO(scene_comic.stitch_panels(self.PANELS)))
        # wide 3:1 strip + 2:1 strip, both full width — no gaps, no empty cells
        self.assertEqual(page.size, (1568, round(1568 * 512 / 1536) + round(1568 * 768 / 1536)))

    def test_wider_group_row_is_thinner(self):
        three = scene_comic.stitch_panels([_png(1536, 512)])
        two = scene_comic.stitch_panels([_png(1536, 768)])
        self.assertLess(Image.open(io.BytesIO(three)).height, Image.open(io.BytesIO(two)).height)


class TestParticipantSlotWiring(unittest.TestCase):
    """Participants arrive as reference pictures right after the fixed
    character/background/pose trio — contiguously (no gap when the pose is
    absent) and never colliding with the QwenImage21Cache node "45"."""

    CFG = get_edit_model_config("qwen_image_edit_2_1")

    def _build(self, builder, **kwargs):
        base = dict(
            instruction="Place the character from Picture 1...",
            character_filename="char.png",
            background_filename="bg.png",
            width=1024,
            height=1024,
            seed=7,
            filename_prefix="t/img",
            cfg=self.CFG,
        )
        base.update(kwargs)
        return builder(**base)

    def test_participants_after_pose(self):
        wf = self._build(
            _build_qwen_edit_21_workflow,
            pose_filename="pose.png",
            participant_filenames=["a.png", "b.png"],
        )
        self.assertEqual(wf["70"]["inputs"]["images.image_4"], ["64", 0])
        self.assertEqual(wf["70"]["inputs"]["images.image_5"], ["65", 0])
        self.assertEqual(wf["45"]["class_type"], "QwenImage21Cache", "node 45 must stay the cache node")
        self.assertEqual(wf["64"]["inputs"]["image"], "a.png")

    def test_participants_contiguous_without_pose(self):
        wf = self._build(
            _build_qwen_edit_21_workflow,
            participant_filenames=["a.png"],
        )
        self.assertIn("images.image_3", wf["70"]["inputs"])
        self.assertNotIn("images.image_4", wf["70"]["inputs"])

    def test_viggle_turbo_participants_after_pose(self):
        wf = self._build(
            _build_qwen_edit_21_viggle_turbo_workflow,
            pose_filename="pose.png",
            participant_filenames=["a.png"],
        )
        self.assertEqual(wf["70"]["inputs"]["images.image_4"], ["64", 0])
        self.assertEqual(wf["45"]["class_type"], "QwenImage21Cache")


class TestSceneImagePrefersComic(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_comic_row_wins_over_backdrop(self):
        db.save_game_image(
            type="turn_background", image_url="http://x/bg.png", game_id="g1",
            turn=3, prompt="backdrop", location_id="loc1",
        )
        db.save_game_image(
            type="scene_comic", image_url="http://x/comic.png", game_id="g1",
            turn=3, prompt="stage",
        )
        self.assertEqual(main._scene_image_for_turn("g1", 3), "http://x/comic.png")

    def test_backdrop_fallback_without_comic(self):
        db.save_game_image(
            type="turn_background", image_url="http://x/bg.png", game_id="g2",
            turn=3, prompt="backdrop", location_id="loc1",
        )
        self.assertEqual(main._scene_image_for_turn("g2", 3), "http://x/bg.png")

    def test_language_constant_importable(self):
        # Guards against an accidental raw-string language check sneaking in.
        self.assertEqual(LANGUAGE_EN, "en")


if __name__ == "__main__":
    unittest.main()
