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

And the 2026-11-14 GM feedback fixes:
- scene instruction schema: per-action `location` (the backdrop is cached per
  location id — an action in the reactor bay gets the reactor bay, not the
  turn's bridge) and `participants` (every crew member taking part in an
  action must appear in the frame).
- scene instruction system prompt: the scene picture is authoritative for the
  room — no invented furniture, seated = facing the operated console.
- turn-background prompt: compartment mode (a different room of the SAME ship,
  anchored to the ship's design language).
- bridge schema: ship_style (design language shared by all rooms of the
  vessel) + games.ship_style storage.
- outcome comic: per-panel anatomy locks for non-humanoid species (a
  lava-sphere crew member was rendered with a human mustache).
"""

import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# main.py hardcodes a daily /app/logs file handler at import time — redirect
# it to a temp dir so the module loads on the host.
import logging

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

    def test_ship_style_required(self):
        # GM feedback 2026-11-14 ("консистентно с остальными задниками"):
        # every compartment backdrop of the ship is generated from this
        # shared design language.
        schema = game_server.BRIDGE_IMAGE_SCHEMA["json_schema"]["schema"]
        self.assertIn("ship_style", schema["properties"])
        self.assertIn("ship_style", schema["required"])


class TestSceneInstructionLocationAndParticipants(unittest.TestCase):
    ACTION = "Patch the coolant leak in the reactor bay."
    SPECIES = "Human engineer."
    TURN_LOCATION = {
        "id": "witness_bridge",
        "name": "Main bridge",
        "description": "The ship's command deck",
        "aboard_ship": True,
    }

    def test_schema_has_location_and_participants(self):
        schema = game_server.scene_instruction_schema(["console_side"])
        props = schema["json_schema"]["schema"]["properties"]
        self.assertIn("location", props)
        self.assertIn("participants", props)
        self.assertIn("aboard_ship", props["location"]["properties"])
        self.assertIn("location", schema["json_schema"]["schema"]["required"])
        self.assertIn("participants", schema["json_schema"]["schema"]["required"])

    def test_turn_location_block_en(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_EN, self.ACTION, self.SPECIES, "A bridge stage", "", "",
            "- console_side: side view", turn_location=self.TURN_LOCATION,
        )
        self.assertIn("witness_bridge", prompt)
        self.assertIn("EXACTLY the same location.id", prompt)
        self.assertIn("reactor compartment", prompt)

    def test_turn_location_block_ru(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_RU, self.ACTION, self.SPECIES, "Мостик", "", "",
            "- console_side: боковой ракурс", turn_location=self.TURN_LOCATION,
        )
        self.assertIn("witness_bridge", prompt)
        self.assertIn("РОВНО тот же location.id", prompt)

    def test_no_location_block_without_turn_location(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_EN, self.ACTION, self.SPECIES, "A bridge stage", "", "",
        )
        self.assertNotIn("location.id", prompt)

    def test_participants_block_en(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_EN, "Repair the conduit together with the engineer", self.SPECIES,
            "A bridge stage", "", "", "", turn_location=self.TURN_LOCATION,
        )
        self.assertIn("PARTICIPANTS:", prompt)
        self.assertIn("each additional character reference picture", prompt)

    def test_participants_block_ru(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_RU, "Вместе с инженером заваривает трещину", self.SPECIES,
            "Мостик", "", "", "", turn_location=self.TURN_LOCATION,
        )
        self.assertIn("УЧАСТНИКИ:", prompt)


class TestFurnitureMandate(unittest.TestCase):
    # GM feedback 2026-11-14: the backdrop showed two seats and the character
    # was seated in an invented third one, chair turned away from the console.
    def test_furniture_mandate_en(self):
        system = build_scene_instruction_system(LANGUAGE_EN)
        self.assertIn("AUTHORITATIVE FOR THE ROOM", system)
        self.assertIn("do NOT invent new furniture", system)
        self.assertIn("body turned TOWARD the", system)

    def test_furniture_mandate_ru(self):
        system = build_scene_instruction_system(LANGUAGE_RU)
        self.assertIn("ТОЛЬКО ИЗ PICTURE 2", system)
        self.assertIn("НЕ выдумывай новую мебель", system)
        self.assertIn("развёрнут К консоли", system)


class TestCompartmentBackdrop(unittest.TestCase):
    SETTING = "Ash ruins perimeter"
    CONFLICT = "Patrols tighten"

    def test_compartment_mode_en(self):
        _, user = build_turn_background_prompts(
            LANGUAGE_EN, self.SETTING, self.CONFLICT,
            location_name="Reactor Bay",
            location_description="Primary reactor column behind blast shielding",
            compartment=True,
            ship_style="brushed titanium walls, amber emergency lighting, curved console banks",
        )
        self.assertIn("ONE SPECIFIC COMPARTMENT", user)
        self.assertIn("brushed titanium", user)
        self.assertIn("Reactor Bay", user)
        self.assertNotIn("EDIT instruction", user)

    def test_compartment_mode_ru(self):
        _, user = build_turn_background_prompts(
            LANGUAGE_RU, "Периметр пепла", "Патрули сжимают кольцо",
            location_name="Реакторный отсек",
            compartment=True,
        )
        self.assertIn("КОНКРЕТНЫЙ ОТСЕК", user)
        self.assertIn("Реакторный отсек", user)

    def test_generic_stage_has_no_compartment_wording(self):
        _, user = build_turn_background_prompts(LANGUAGE_EN, self.SETTING, self.CONFLICT)
        self.assertNotIn("ONE SPECIFIC COMPARTMENT", user)

    def test_edit_mode_has_no_compartment_wording(self):
        _, user = build_turn_background_prompts(
            LANGUAGE_EN, self.SETTING, self.CONFLICT, edit_reference=True,
        )
        self.assertNotIn("ONE SPECIFIC COMPARTMENT", user)


class TestShipStyleDb(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_ship_style_roundtrip(self):
        db.create_game({"game_id": "g1", "name": "Test Ship"})
        self.assertEqual(db.get_ship_style("g1"), "")
        db.save_ship_style("g1", "brushed titanium, amber emergency lighting")
        self.assertEqual(db.get_ship_style("g1"), "brushed titanium, amber emergency lighting")
        self.assertEqual(db.get_ship_style("other"), "")


class TestOutcomeAnatomyLock(unittest.TestCase):
    """GM feedback 2026-11-14: the outcome comic gave the lava-sphere crew
    member a human mustache and uniform — non-humanoid panels need explicit
    anatomy locks, and invented traits must be forbidden."""

    def test_lock_emitted_only_for_non_humanoids(self):
        import asyncio
        from unittest.mock import patch

        crew = [
            {
                "name": "Сфера", "role": "Passenger",
                "appearance": "a cracked magma sphere with thin tentacle arms",
                "avatar_url": "http://x/sphere.png",
                "species": "Сфера лавы", "species_description": "a sphere of living lava",
                "species_category": "energy",
            },
            {
                "name": "Ирина", "role": "Medic",
                "appearance": "dark-haired woman in a medical uniform",
                "avatar_url": "http://x/medic.png",
                "species": "Human", "species_description": "human",
                "species_category": "human",
            },
        ]

        async def run() -> str:
            gm = game_server.create_game_server(language=LANGUAGE_EN)
            captured: dict[str, str] = {}

            async def fake_call_llm(**kwargs):
                captured["user"] = kwargs["user_prompt"]
                return {"image_prompt": "test prompt"}

            with patch.object(gm, "_call_llm", fake_call_llm):
                await gm.generate_outcome_image_prompt(
                    "The crew seals the breach.", "Hull at 80%", "Cautious hope",
                    crew, game_id="g1", player_id=None, turn=4,
                    kind="outcome_image_prompt",
                )
            return captured["user"]

        user = asyncio.run(run())
        self.assertEqual(user.count("ANATOMY LOCK"), 1, "lock only for the non-humanoid panel")
        self.assertIn("a sphere of living lava", user)
        self.assertIn("NO human face", user)
        self.assertIn("human mustache", user, "the trait-invention ban must cite the failure")
        self.assertIn("MUST be copied from THAT Picture's own", user)

    def _capture_outcome_user(self, crew, **kwargs):
        import asyncio
        from unittest.mock import patch

        async def run() -> str:
            gm = game_server.create_game_server(language=LANGUAGE_EN)
            captured: dict[str, str] = {}

            async def fake_call_llm(**kw):
                captured["user"] = kw["user_prompt"]
                return {"image_prompt": "test prompt"}

            with patch.object(gm, "_call_llm", fake_call_llm):
                await gm.generate_outcome_image_prompt(
                    "n", "s", "m", crew,
                    game_id="g1", player_id=None, turn=4,
                    kind="outcome_image_prompt", **kwargs,
                )
            return captured["user"]

        return asyncio.run(run())

    def test_crew_positions_group_panels(self):
        # GM feedback 2026-11-14 v3: co-located crew share ONE wide panel.
        crew = [
            {"name": "Ирина", "role": "Medic", "appearance": "a medic",
             "avatar_url": "http://x/1.png", "species_category": "human"},
            {"name": "Слач", "role": "XO", "appearance": "an officer",
             "avatar_url": "http://x/2.png", "species_category": "human"},
        ]
        user = self._capture_outcome_user(
            crew,
            crew_positions=[
                {"role_key": "medic", "position": "reactor compartment, sealing the leak"},
                {"role_key": "xo", "position": "bridge, coordinating"},
            ],
        )
        self.assertIn("reactor compartment, sealing the leak", user)
        self.assertIn("GROUPING RULE", user)
        self.assertIn("share ONE wide panel", user)
        self.assertIn("one panel per location group", user)
        self.assertNotIn("exactly 2 panels", user)

    def test_no_positions_keeps_per_character_panels(self):
        crew = [
            {"name": "Ирина", "role": "Medic", "appearance": "a medic",
             "avatar_url": "http://x/1.png", "species_category": "human"},
            {"name": "Слач", "role": "XO", "appearance": "an officer",
             "avatar_url": "http://x/2.png", "species_category": "human"},
        ]
        user = self._capture_outcome_user(crew)
        self.assertIn("exactly 2 panels", user)
        self.assertIn("One panel per Picture", user)
        self.assertNotIn("GROUPING RULE", user)

    def test_backdrops_per_location(self):
        # GM feedback 2026-11-14: the security chief was already OUTSIDE the
        # ship — each location group needs ITS OWN backdrop picture, and the
        # prompt must map panels to their group's environment.
        crew = [
            {"name": "Тарас", "role": "Security", "appearance": "a magma sphere",
             "avatar_url": "http://x/1.png", "species_category": "energy"},
            {"name": "Ирина", "role": "Medic", "appearance": "a medic",
             "avatar_url": "http://x/2.png", "species_category": "human"},
        ]
        user = self._capture_outcome_user(
            crew,
            crew_positions=[
                {"role_key": "security", "position": "outside, among the ruins"},
                {"role_key": "medic", "position": "medbay"},
            ],
            backdrops=[
                {"picture": 1, "name": "Ash ruins", "description": "open ground among broken towers"},
                {"picture": 2, "name": "Medbay", "description": "cramped medical bay"},
            ],
        )
        self.assertIn("LOCATION BACKDROPS", user)
        self.assertIn("Picture 1 — LOCATION BACKDROP «Ash ruins»", user)
        self.assertIn("Picture 2 — LOCATION BACKDROP «Medbay»", user)
        self.assertIn("ITS group's backdrop picture", user)
        # crew avatars start AFTER the backdrop pictures
        self.assertIn("Picture 3 — Тарас", user)
        self.assertIn("Picture 4 — Ирина", user)
        # no phantom people (the hostage that was not met yet)
        self.assertIn("CREW ONLY", user)
        self.assertIn("no hostages, patients", user)

    def test_no_backdrops_keeps_shared_backdrop(self):
        crew = [
            {"name": "Ирина", "role": "Medic", "appearance": "a medic",
             "avatar_url": "http://x/1.png", "species_category": "human"},
        ]
        user = self._capture_outcome_user(crew, scene_description="a bridge stage")
        self.assertIn("Picture 1 — the SCENE BACKGROUND", user)
        self.assertNotIn("LOCATION BACKDROPS", user)

    def test_dead_crew_get_farewell_panels(self):
        # GM feedback 2026-11-14: "Taras умер — надо его как-то показать" —
        # the dead get their own farewell panel instead of silently vanishing.
        crew = [
            {"name": "Слач", "role": "XO", "appearance": "an officer",
             "avatar_url": "http://x/1.png", "species_category": "human"},
            {"name": "Тарас", "role": "Security", "appearance": "a magma sphere",
             "avatar_url": "http://x/2.png", "species_category": "energy",
             "died_this_turn": True, "dead_cause": "suffocated by the ancient mechanism"},
        ]
        user = self._capture_outcome_user(crew)
        self.assertIn("Picture 2 — Тарас (Security): a magma sphere — **DIED THIS TURN**", user)
        self.assertIn("suffocated by the ancient mechanism", user)
        self.assertIn("FAREWELL PANELS: 1 character(s) are marked DIED THIS TURN", user)
        self.assertIn("must NOT appear alive in any other panel", user)
        self.assertIn("The living crew at this moment numbers exactly 1", user)


class TestSceneInstructionSoloRule(unittest.TestCase):
    """GM feedback 2026-11-14: a hallucinated second 'crew member' appeared in
    a solo action image — the instruction must mandate exactly one person."""

    def test_solo_rule_en(self):
        system = build_scene_instruction_system(LANGUAGE_EN)
        self.assertIn("SOLO RULE", system)
        self.assertIn("COMPLETELY ALONE", system)
        self.assertIn("no doubles", system)

    def test_solo_rule_ru(self):
        system = build_scene_instruction_system(LANGUAGE_RU)
        self.assertIn("СОЛО-ПРАВИЛО", system)
        self.assertIn("ПОЛНОСТЬЮ ОДИН", system)
        self.assertIn("двойников", system)


class TestCharShotVariation(unittest.TestCase):
    def test_hint_cycles_by_turn(self):
        self.assertNotEqual(main._char_shot_hint(6), main._char_shot_hint(7))
        self.assertEqual(main._char_shot_hint(6), main._char_shot_hint(11))
        self.assertIn("shot", main._char_shot_hint(6).lower())


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
