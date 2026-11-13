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

And the 2026-11-13 GM feedback fixes:
- pose_library: skeletons render at ~44% of canvas height (scale anchor —
  full-canvas skeletons made the character a full-frame giant); side-view
  poses precede the back-view one in the catalogue.
- scene instruction: side/profile camera mandate (back view forbidden) and
  the shot enum + composition sentences.
- turn-background prompt: edit_reference mode (aboard-ship interior edit).
- global-circumstances: location progression instruction + aboard_ship in
  the schema.
"""

import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import game_server  # noqa: E402
import pose_library  # noqa: E402
from language import LANGUAGE_EN, LANGUAGE_RU  # noqa: E402
from prompts import (  # noqa: E402
    build_global_circumstances_prompts,
    build_scene_instruction_system,
    build_scene_instruction_user,
    build_turn_background_prompts,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_ink_rows(png: bytes) -> list[int]:
    """Row indices containing non-black pixels of an RGB PNG (stdlib decode)."""
    pos = 8
    idat = b""
    while pos < len(png):
        length = struct.unpack(">I", png[pos:pos + 4])[0]
        tag = png[pos + 4:pos + 8]
        if tag == b"IDAT":
            idat += png[pos + 8:pos + 8 + length]
        pos += 8 + length + 4
    raw = zlib.decompress(idat)
    width, height = pose_library.CANVAS_W, pose_library.CANVAS_H
    rows = []
    for y in range(height):
        start = y * (width * 3 + 1) + 1
        if any(raw[start:start + width * 3]):
            rows.append(y)
    return rows


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

    def test_render_scale_anchor(self):
        # GM feedback 2026-11-13 ("персонаж слишком большой"): the skeleton is
        # drawn at ~44% of the canvas height so the edit model does not map a
        # full-canvas reference onto a full-frame giant.
        for pose_id in pose_library.pose_ids():
            with self.subTest(pose_id=pose_id):
                rows = _png_ink_rows(pose_library.render_pose_png(pose_id))
                self.assertTrue(rows, "skeleton rendered empty")
                frac = (rows[-1] - rows[0]) / pose_library.CANVAS_H
                self.assertLessEqual(frac, 0.50, f"{pose_id} taller than half the frame")
                self.assertGreaterEqual(frac, 0.30, f"{pose_id} collapsed too small")
                self.assertLess(rows[-1], pose_library.CANVAS_H, "feet must stay inside the canvas")

    def test_catalogue_side_views_before_back_view(self):
        # The instruction LLM is biased toward early catalogue entries; the
        # side/profile poses must precede the back-to-camera one.
        ids = pose_library.pose_ids()
        self.assertLess(ids.index("console_side"), ids.index("console_typing"))
        self.assertLess(ids.index("console_sitting_side"), ids.index("console_typing"))


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


class TestSceneInstructionSideCameraAndShot(unittest.TestCase):
    ACTION = "Search the alien frequencies on the sensor console."
    SPECIES = "Human science officer."

    def test_back_view_forbidden_en(self):
        prompt = build_scene_instruction_system(LANGUAGE_EN)
        self.assertIn("FROM THE SIDE", prompt)
        self.assertIn("FORBIDDEN", prompt)

    def test_back_view_forbidden_ru(self):
        prompt = build_scene_instruction_system(LANGUAGE_RU)
        self.assertIn("СБОКУ", prompt)
        self.assertIn("ЗАПРЕЩЁН", prompt)

    def test_shot_block_en(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_EN, self.ACTION, self.SPECIES, "A bridge stage", "", "", "- console_side: side view",
        )
        self.assertIn("close_up", prompt)
        self.assertIn("medium", prompt)
        self.assertIn("wide", prompt)

    def test_shot_block_ru(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_RU, self.ACTION, self.SPECIES, "Мостик", "", "", "- console_side: боковой ракурс",
        )
        self.assertIn("close_up", prompt)
        self.assertIn("КРУПНЫЙ", prompt)

    def test_schema_has_shot_enum(self):
        schema = game_server.scene_instruction_schema(["console_side"])
        props = schema["json_schema"]["schema"]["properties"]
        self.assertEqual(props["shot"]["enum"], ["close_up", "medium", "wide"])
        self.assertIn("shot", schema["json_schema"]["schema"]["required"])

    def test_shot_composition_sentences(self):
        close_up = game_server.GameServer._shot_composition("close_up")
        self.assertIn("close-up", close_up)
        self.assertIn("readable", close_up)
        medium = game_server.GameServer._shot_composition("medium")
        self.assertIn("one third to one half", medium)
        wide = game_server.GameServer._shot_composition("wide")
        self.assertIn("one quarter", wide)


class TestBridgeStageSchema(unittest.TestCase):
    def test_bridge_stage_prompt_required(self):
        schema = game_server.BRIDGE_IMAGE_SCHEMA["json_schema"]["schema"]
        self.assertIn("bridge_stage_prompt", schema["properties"])
        self.assertIn("bridge_stage_prompt", schema["required"])
        self.assertIn("VACANT", schema["properties"]["bridge_stage_prompt"]["description"])


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

    def test_edit_reference_mode_en(self):
        # aboard_ship backdrops are an EDIT of the ship's own bridge, not a
        # fresh txt2img that invents "another ship's" interior. The bridge
        # reference itself may contain the crew, so the instruction must
        # demand their removal — a populated backdrop breaks compositing.
        _, user = build_turn_background_prompts(
            LANGUAGE_EN, "Ash ruins perimeter", "Patrols tighten",
            location_name="Ash Perimeter", location_description="Shadow zone among ruined towers",
            edit_reference=True,
        )
        self.assertIn("EDIT instruction", user)
        self.assertIn("THIS IS THEIR SHIP", user)
        self.assertIn("REMOVE ALL OF THEM", user)
        self.assertIn("COMPLETELY VACANT", user)
        self.assertIn("Ash Perimeter", user)

    def test_edit_reference_mode_ru(self):
        _, user = build_turn_background_prompts(
            LANGUAGE_RU, "Периметр пепла", "Патрули сжимаются",
            edit_reference=True,
        )
        self.assertIn("ЭТО ИХ КОРАБЛЬ", user)
        self.assertIn("УДАЛИ ИХ ВСЕХ", user)
        self.assertIn("ПОЛНОСТЬЮ ПУСТЫМ", user)
        self.assertIn("Picture 1", user)

    def test_txt2img_mode_has_no_edit_wording(self):
        _, user = build_turn_background_prompts(LANGUAGE_EN, "Orbit of ruins", "")
        self.assertNotIn("EDIT instruction", user)


class TestGlobalCircumstancesLocation(unittest.TestCase):
    def test_previous_location_reuse_instruction_en(self):
        _, user = build_global_circumstances_prompts(
            LANGUAGE_EN, 3, "previous", "crew", "", use_vs=False, vs_k=0,
            previous_location="witness_bridge — Main bridge — the ship's command deck",
        )
        self.assertIn("witness_bridge", user)
        self.assertIn("EXACTLY the same location.id", user)
        # GM feedback 2026-11-13: locations must progress, not repeat forever.
        self.assertIn("PROGRESS THE STORY THROUGH LOCATIONS", user)

    def test_previous_location_reuse_instruction_ru(self):
        _, user = build_global_circumstances_prompts(
            LANGUAGE_RU, 3, "ранее", "экипаж", "", use_vs=False, vs_k=0,
            previous_location="witness_bridge — Главный мостик — командный пункт",
        )
        self.assertIn("witness_bridge", user)
        self.assertIn("РОВНО тот же location.id", user)
        self.assertIn("ВЕДИ СЮЖЕТ ЧЕРЕЗ ЛОКАЦИИ", user)

    def test_aboard_ship_in_prompt_and_schema(self):
        _, user = build_global_circumstances_prompts(
            LANGUAGE_EN, 2, "prev", "crew", "", use_vs=False, vs_k=0,
        )
        self.assertIn("aboard_ship", user)
        loc = game_server.GLOBAL_CIRCUMSTANCES_SCHEMA["json_schema"]["schema"]["properties"]["location"]
        self.assertIn("aboard_ship", loc["properties"])
        self.assertIn("aboard_ship", loc["required"])

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
