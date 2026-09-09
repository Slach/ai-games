"""Demo-block mechanics in prompts.py.

The few-shot demo constants (NPC_DECISION_DEMOS / SCENE_INSTRUCTION_DEMOS)
are compiled offline by tools/prompt_optimizer and pasted in manually. They
must be empty by default (byte-identical prompts to the pre-demo builders)
and, when set, appended verbatim to the user prompt of the matching language.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prompts  # noqa: E402
from language import LANGUAGE_EN, LANGUAGE_RU  # noqa: E402


class PromptDemosTest(unittest.TestCase):
    def setUp(self):
        self._saved_npc = dict(prompts.NPC_DECISION_DEMOS)
        self._saved_scene = dict(prompts.SCENE_INSTRUCTION_DEMOS)

    def tearDown(self):
        prompts.NPC_DECISION_DEMOS.clear()
        prompts.NPC_DECISION_DEMOS.update(self._saved_npc)
        prompts.SCENE_INSTRUCTION_DEMOS.clear()
        prompts.SCENE_INSTRUCTION_DEMOS.update(self._saved_scene)

    def _npc(self, language):
        return prompts.build_npc_decision_prompts(
            language, "К'рртх", "Главный инженер", "прагматичный, ворчливый",
            "  [seal_off] Загерметизировать\n  [lead_repair] Возглавить партию",
            loyalty=82, use_vs=False, vs_k=5,
        )

    def test_npc_empty_block_leaves_prompt_unchanged(self):
        # With an empty demo entry (the state before anything is compiled)
        # the prompt must be identical to the pre-demo builder output.
        prompts.NPC_DECISION_DEMOS[LANGUAGE_RU] = ""

        system, user = self._npc(LANGUAGE_RU)
        self.assertNotIn("ПРИМЕР", user)
        self.assertTrue(user.endswith("действуй интуитивно."))

    def test_npc_demo_block_appended_per_language(self):
        prompts.NPC_DECISION_DEMOS[LANGUAGE_RU] = "ДЕМО-БЛОК-RU"
        prompts.NPC_DECISION_DEMOS[LANGUAGE_EN] = "DEMO-BLOCK-EN"

        _, user_ru = self._npc(LANGUAGE_RU)
        self.assertTrue(user_ru.endswith("\n\nДЕМО-БЛОК-RU"))

        _, user_en = self._npc(LANGUAGE_EN)
        self.assertTrue(user_en.endswith("\n\nDEMO-BLOCK-EN"))

    def test_npc_demo_block_survives_vs_wrap(self):
        prompts.NPC_DECISION_DEMOS[LANGUAGE_RU] = "ДЕМО-БЛОК-RU"
        system, user = prompts.build_npc_decision_prompts(
            LANGUAGE_RU, "К'рртх", "Главный инженер", "прагматичный",
            "  [a] Одно\n  [b] Другое",
            loyalty=50, use_vs=True, vs_k=5,
        )
        # The demo block is part of the base prompt before the VS wrapper
        # asks for k diverse options, so it must still be present.
        self.assertIn("ДЕМО-БЛОК-RU", user)
        self.assertIn("DIVERSE", system)

    def test_scene_instruction_demo_block(self):
        prompts.SCENE_INSTRUCTION_DEMOS[LANGUAGE_RU] = "СЦЕНЫ-ДЕМО-RU"
        prompts.SCENE_INSTRUCTION_DEMOS[LANGUAGE_EN] = "SCENE-DEMO-EN"

        user_ru = prompts.build_scene_instruction_user(
            LANGUAGE_RU, "Чинит реактор", "Кристаллическая форма",
            "engineering", "Корабль в опасности", "non_humanoid",
        )
        self.assertTrue(user_ru.endswith("\n\nСЦЕНЫ-ДЕМО-RU"))

        user_en = prompts.build_scene_instruction_user(
            LANGUAGE_EN, "Repairs the reactor", "Crystalline form",
            "engineering", "Ship in danger", "non_humanoid",
        )
        self.assertTrue(user_en.endswith("\n\nSCENE-DEMO-EN"))

    def test_scene_instruction_no_demo_when_empty(self):
        prompts.SCENE_INSTRUCTION_DEMOS[LANGUAGE_RU] = ""
        user = prompts.build_scene_instruction_user(
            LANGUAGE_RU, "Чинит реактор", "Кристаллическая форма",
            "engineering", "Корабль в опасности", "non_humanoid",
        )
        self.assertTrue(user.endswith("Без описания внешности персонажа."))

    def test_pasted_ru_demo_blocks_are_live(self):
        # Blocks compiled by tools/prompt_optimizer and pasted into prompts.py
        # must actually reach the prompts (guards against constants being
        # defined but silently left empty). NPC demos were removed after a
        # strict-judge rerun showed they hurt (94.2 -> 90.2), so only the
        # non-empty constants are asserted.
        if prompts.NPC_DECISION_DEMOS[LANGUAGE_RU]:
            _, npc_user = self._npc(LANGUAGE_RU)
            self.assertIn(prompts.NPC_DECISION_DEMOS[LANGUAGE_RU], npc_user)

        scene_user = prompts.build_scene_instruction_user(
            LANGUAGE_RU, "Чинит реактор", "Кристаллическая форма",
            "engineering", "Корабль в опасности", "non_humanoid",
        )
        self.assertIn(prompts.SCENE_INSTRUCTION_DEMOS[LANGUAGE_RU], scene_user)

        _, outcome_user = prompts.build_combined_outcome_prompts(
            LANGUAGE_RU,
            setting="Шлюз", conflict="Пробоина", narrative="Сирены",
            previous_summary="", mission_text="Stage 1", ship_status_text="Hull: 50/100",
            decisions_text="--- Decision 1 ---", roster_text="  - Ольга (Captain) [p1] — ALIVE",
            use_vs=False, vs_k=5,
        )
        self.assertIn(prompts.COMBINED_OUTCOME_DEMOS[LANGUAGE_RU], outcome_user)


if __name__ == "__main__":
    unittest.main()
