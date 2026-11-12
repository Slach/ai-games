"""Tests for the pose-reference and location-keyed backdrop features.

Covers the 2026-11-11 GM feedback fixes:
- pose_library: every pose renders a valid 768x1024 PNG; the catalogue block
  lists all poses.
- scene-instruction prompt: pose catalogue embedded + Picture 3 mandate when
  supplied; no Picture 3 wording without a catalogue.
- turn-background prompt: staging requirements (standable surface in the
  lower half, character-scale anchors, domestic windows forbidden) and
  location name/description embedding.
- global-circumstances prompt: previous-location id-reuse instruction.
- DB: game_images.location_id round-trip (migration 27).
"""

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import pose_library  # noqa: E402
from language import LANGUAGE_EN, LANGUAGE_RU  # noqa: E402
from prompts import (  # noqa: E402
    build_global_circumstances_prompts,
    build_scene_instruction_user,
    build_turn_background_prompts,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class TestPoseLibrary(unittest.TestCase):
    def test_all_poses_render_valid_png(self):
        for pose_id in pose_library.pose_ids():
            with self.subTest(pose_id=pose_id):
                png = pose_library.render_pose_png(pose_id)
                self.assertEqual(png[:8], PNG_SIGNATURE)
                width, height = struct.unpack(">II", png[16:24])
                self.assertEqual((width, height), (pose_library.CANVAS_W, pose_library.CANVAS_H))

    def test_catalogue_lists_every_pose(self):
        block = pose_library.pose_prompt_block()
        for pose_id in pose_library.pose_ids():
            with self.subTest(pose_id=pose_id):
                self.assertIn(f"- {pose_id}: ", block)

    def test_unknown_pose_upload_returns_none(self):
        # No network should even be attempted for an unknown id.
        import asyncio
        self.assertIsNone(
            asyncio.run(pose_library.ensure_pose_uploaded("http://unused.invalid", "no_such_pose"))
        )

    def test_render_is_deterministic(self):
        self.assertEqual(
            pose_library.render_pose_png("standing"),
            pose_library.render_pose_png("standing"),
        )


class TestSceneInstructionPoseBlock(unittest.TestCase):
    ACTION = "Reroute the emergency hatch system at the docking clamps."
    SPECIES = "Human science officer."
    CATALOGUE = "- standing: standing upright\n- console_typing: leaning over a console"

    def test_catalogue_embedded_and_picture3_en(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_EN, self.ACTION, self.SPECIES, "A cargo bay stage", "", "", self.CATALOGUE,
        )
        self.assertIn("console_typing", prompt)
        self.assertIn("Picture 3", prompt)
        self.assertIn("pose_id", prompt)

    def test_catalogue_embedded_and_picture3_ru(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_RU, self.ACTION, self.SPECIES, "Грузовой отсек", "", "", self.CATALOGUE,
        )
        self.assertIn("console_typing", prompt)
        self.assertIn("Picture 3", prompt)
        self.assertIn("pose_id", prompt)

    def test_no_picture3_without_catalogue(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_EN, self.ACTION, self.SPECIES, "A cargo bay stage", "", "",
        )
        self.assertNotIn("Picture 3", prompt)


class TestTurnBackgroundStaging(unittest.TestCase):
    SETTING = "Орбита разрушенной станции, сектор 7-Delta"
    CONFLICT = "Дрон сканирует обломки"

    def test_staging_requirements_ru(self):
        _, user = build_turn_background_prompts(LANGUAGE_RU, self.SETTING, self.CONFLICT)
        self.assertIn(self.SETTING, user)
        self.assertIn("БЕЗ персонажей", user)
        self.assertIn("поверхность, на которой можно стоять", user)
        self.assertIn("ЗАПРЕЩЕНЫ бытовые окна", user)
        self.assertIn("нижней половине кадра", user)

    def test_staging_requirements_en(self):
        _, user = build_turn_background_prompts(LANGUAGE_EN, "Orbit of ruins", "A drone scans debris")
        self.assertIn("NO characters", user)
        self.assertIn("standable surface", user)
        self.assertIn("FORBIDDEN", user)
        self.assertIn("AT CHARACTER SCALE", user)

    def test_location_embedded_when_given(self):
        _, user = build_turn_background_prompts(
            LANGUAGE_EN, "Orbit of ruins", "A drone scans debris",
            location_name="Witness cargo bay",
            location_description="Cramped cargo hold with ruptured containers",
        )
        self.assertIn("Witness cargo bay", user)
        self.assertIn("Cramped cargo hold", user)

    def test_no_location_block_without_name(self):
        _, user = build_turn_background_prompts(LANGUAGE_EN, "Orbit of ruins", "")
        self.assertNotIn("Location:", user)


class TestGlobalCircumstancesLocation(unittest.TestCase):
    def test_previous_location_reuse_instruction_en(self):
        _, user = build_global_circumstances_prompts(
            LANGUAGE_EN, 3, "previous", "crew", "", use_vs=False, vs_k=0,
            previous_location="witness_bridge — Main bridge — the ship's command deck",
        )
        self.assertIn("witness_bridge", user)
        self.assertIn("EXACTLY the same location.id", user)

    def test_previous_location_reuse_instruction_ru(self):
        _, user = build_global_circumstances_prompts(
            LANGUAGE_RU, 3, "ранее", "экипаж", "", use_vs=False, vs_k=0,
            previous_location="witness_bridge — Главный мостик — командный пункт",
        )
        self.assertIn("witness_bridge", user)
        self.assertIn("РОВНО тот же location.id", user)

    def test_no_location_block_on_first_turn(self):
        _, user = build_global_circumstances_prompts(
            LANGUAGE_EN, 1, "first", "crew", "", use_vs=False, vs_k=0,
        )
        self.assertNotIn("Previous turn's location", user)


class TestLocationBackgroundDb(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_location_roundtrip(self):
        db.save_game_image(
            type="turn_background", image_url="http://x/bg1.png", game_id="g1",
            turn=1, prompt="p1", location_id="witness_bridge",
        )
        got = db.get_location_background("g1", "witness_bridge")
        self.assertIsNotNone(got)
        self.assertEqual(got["image_url"], "http://x/bg1.png")
        self.assertIsNone(db.get_location_background("g1", "another_place"))

    def test_turn_fallback_still_works(self):
        db.save_game_image(
            type="turn_background", image_url="http://x/bg2.png", game_id="g1",
            turn=2, prompt="p2", location_id=None,
        )
        got = db.get_turn_background("g1", 2)
        self.assertIsNotNone(got)
        self.assertEqual(got["image_url"], "http://x/bg2.png")


if __name__ == "__main__":
    unittest.main()
