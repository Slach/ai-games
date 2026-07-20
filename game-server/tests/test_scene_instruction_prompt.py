"""Tests for the Qwen-Image-Edit scene-instruction prompt builder.

Regression guard: the character's role/title must NOT appear in the user
prompt. Mentioning a role like "Scientific Officer" biases Qwen-Image-Edit
toward a human in uniform and overrides the non-humanoid avatar in Picture 1.
"""

import inspect
import unittest

from language import LANGUAGE_EN, LANGUAGE_RU
from prompts import build_scene_instruction_system, build_scene_instruction_user


class TestSceneInstructionUserPrompt(unittest.TestCase):
    ACTION = "Use an outdated colonial network access code to slip past the jamming."
    SPECIES = (
        "A cluster of geometric crystals floating in a magnetic field, "
        "each facet vibrating at a unique frequency, wrapped in luminous plasma gas."
    )
    ROLE = "Scientific Officer"

    def test_signature_has_no_role_param(self):
        params = inspect.signature(build_scene_instruction_user).parameters
        self.assertNotIn("role", params)

    def test_prompt_has_no_role_ru(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_RU,
            self.ACTION,
            self.SPECIES,
            None,
            "",
        )
        self.assertNotIn("Роль", prompt)
        self.assertNotIn(self.ROLE, prompt)

    def test_prompt_has_no_role_en(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_EN,
            self.ACTION,
            self.SPECIES,
            None,
            "",
        )
        self.assertNotIn("Role", prompt)
        self.assertNotIn(self.ROLE, prompt)

    def test_preserves_action_and_species(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_EN,
            self.ACTION,
            self.SPECIES,
            None,
            "",
        )
        self.assertIn(self.ACTION, prompt)
        self.assertIn(self.SPECIES, prompt)

    def test_keeps_background_location_hint(self):
        prompt = build_scene_instruction_user(
            LANGUAGE_EN,
            self.ACTION,
            self.SPECIES,
            "bridge",
            "",
        )
        self.assertIn("Scene location hint: bridge", prompt)

    def test_keeps_scene_context(self):
        ctx = "Orbit of asteroid Hive in the Black Hole system (Sector 7G)."
        prompt = build_scene_instruction_user(
            LANGUAGE_EN,
            self.ACTION,
            self.SPECIES,
            None,
            ctx,
        )
        self.assertIn(ctx, prompt)


class TestSceneInstructionSystemPrompt(unittest.TestCase):
    def test_forbids_character_description_ru(self):
        system = build_scene_instruction_system(LANGUAGE_RU)
        self.assertIn("НЕ повторяй", system)

    def test_forbids_character_description_en(self):
        system = build_scene_instruction_system(LANGUAGE_EN)
        self.assertIn("Do NOT restate", system)


if __name__ == "__main__":
    unittest.main()
