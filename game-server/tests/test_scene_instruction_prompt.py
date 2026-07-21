"""Tests for the Qwen-Image-Edit scene-instruction prompt builder.

Regression guards:
- The character's role/title must NOT appear in the user prompt. Mentioning a
  role like "Scientific Officer" biases Qwen-Image-Edit toward a human in
  uniform and overrides the non-humanoid avatar in Picture 1.
- For non-humanoid / energy / symbiotic beings, an anatomy guard must forbid
  human-body terms (arms/hands/face/expression) — Qwen-Image-Edit trusts the
  text over Picture 1 and will collapse a crystal cluster back into a humanoid
  if those words appear in the instruction.
"""

import inspect
import unittest

from language import LANGUAGE_EN, LANGUAGE_RU
from prompts import build_scene_instruction_system, build_scene_instruction_user

# Categories that must trigger the anatomy guard.
ALIEN_CATEGORIES = ("non_humanoid", "energy", "symbiotic")
# Categories where pose / facial expression is allowed.
HUMANOID_CATEGORIES = ("human", "humanoid", "cybernetic")

ANATOMY_TERMS_RU = ("«рук»", "«кистей»", "«лица»", "«выражения лица»", "«глаз»")
ANATOMY_TERMS_EN = ("\"arms\"", "\"hands\"", "\"face\"", "\"facial expression\"", "\"eyes\"")


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

    def test_signature_has_species_category_param(self):
        params = inspect.signature(build_scene_instruction_user).parameters
        self.assertIn("species_category", params)

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


class TestAnatomyGuardForAlienSpecies(unittest.TestCase):
    """non_humanoid / energy / symbiotic must forbid human-body terms."""

    ACTION = "Synchronize the crystal vibrations with the event horizon."
    SPECIES = "A cluster of floating geometric crystals in a magnetic field."

    def _check_guard_present(self, language: str, category: str) -> str:
        prompt = build_scene_instruction_user(
            language,
            self.ACTION,
            self.SPECIES,
            None,
            "",
            species_category=category,
        )
        return prompt

    def test_guard_present_for_each_alien_category_ru(self):
        for cat in ALIEN_CATEGORIES:
            with self.subTest(category=cat):
                prompt = self._check_guard_present(LANGUAGE_RU, cat)
                for term in ANATOMY_TERMS_RU:
                    self.assertIn(term, prompt, f"RU guard for {cat} missing term {term}")
                self.assertIn(cat, prompt)

    def test_guard_present_for_each_alien_category_en(self):
        for cat in ALIEN_CATEGORIES:
            with self.subTest(category=cat):
                prompt = self._check_guard_present(LANGUAGE_EN, cat)
                for term in ANATOMY_TERMS_EN:
                    self.assertIn(term, prompt, f"EN guard for {cat} missing term {term}")
                self.assertIn(cat, prompt)

    def test_guard_absent_for_empty_category(self):
        """Default (empty) category behaves like human — no guard."""
        prompt = build_scene_instruction_user(
            LANGUAGE_EN,
            self.ACTION,
            self.SPECIES,
            None,
            "",
        )
        for term in ANATOMY_TERMS_EN:
            self.assertNotIn(term, prompt)


class TestAnatomyGuardAbsentForHumanoidSpecies(unittest.TestCase):
    """human / humanoid / cybernetic keep the pose / facial-expression vocabulary."""

    ACTION = "Salute the captain with a steady expression."
    SPECIES = "A seasoned officer in a uniform."

    def test_guard_absent_for_each_humanoid_category_ru(self):
        for cat in HUMANOID_CATEGORIES:
            with self.subTest(category=cat):
                prompt = build_scene_instruction_user(
                    LANGUAGE_RU,
                    self.ACTION,
                    self.SPECIES,
                    None,
                    "",
                    species_category=cat,
                )
                # Guard is RU-only and references the literal category name; if the
                # guard were triggered it would mention the species_category verbatim
                # plus the forbidden-terms preamble. We assert the preamble is absent.
                self.assertNotIn("НЕ описывай человеческую анатомию", prompt)

    def test_guard_absent_for_each_humanoid_category_en(self):
        for cat in HUMANOID_CATEGORIES:
            with self.subTest(category=cat):
                prompt = build_scene_instruction_user(
                    LANGUAGE_EN,
                    self.ACTION,
                    self.SPECIES,
                    None,
                    "",
                    species_category=cat,
                )
                self.assertNotIn("Do NOT impose human anatomy", prompt)


class TestSceneInstructionSystemPrompt(unittest.TestCase):
    def test_forbids_character_description_ru(self):
        system = build_scene_instruction_system(LANGUAGE_RU)
        self.assertIn("НЕ повторяй", system)

    def test_forbids_character_description_en(self):
        system = build_scene_instruction_system(LANGUAGE_EN)
        self.assertIn("Do NOT restate", system)


if __name__ == "__main__":
    unittest.main()
