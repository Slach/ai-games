"""Welcome text must onboard players to the failure rules.

The doom clock, permanent hull damage, wound escalation, mutiny and permanent
crew losses mean a game can be LOST — without a rules block in the welcome,
the first loss reads as a bug to the player. These tests guard the block in
both delivery paths: the fallback strings in language.py (GAME_STRINGS) and
the LLM prompt instruction in prompts.py (build_game_title_prompts), for RU
and EN.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from language import GAME_STRINGS, LANGUAGE_EN, LANGUAGE_RU  # noqa: E402
from prompts import build_game_title_prompts  # noqa: E402

# One key phrase per rule line of the block, plus the two headings.
RU_PHRASES = (
    "Как можно проиграть",
    "Угроза растёт каждый ход",
    "не чинится сам",
    "гибель корабля",
    "тяжёлая рана плюс любая новая",
    "Промедление",
    "взбунтоваться",
    "не пополняется",
    "Как победить",
)
EN_PHRASES = (
    "How you can lose",
    "Threat rises every turn",
    "never self-repairs",
    "ship is destroyed",
    "a critical wound plus any new one kills",
    "Hesitation",
    "mutiny",
    "never replenished",
    "How to win",
)


class TestWelcomeFallbackRulesBlock(unittest.TestCase):
    def test_ru_fallback_has_rules_block(self):
        text = GAME_STRINGS[LANGUAGE_RU]["welcome_text_fallback"]
        for phrase in RU_PHRASES:
            self.assertIn(phrase, text, f"RU welcome fallback missing phrase: {phrase}")

    def test_en_fallback_has_rules_block(self):
        text = GAME_STRINGS[LANGUAGE_EN]["welcome_text_fallback"]
        for phrase in EN_PHRASES:
            self.assertIn(phrase, text, f"EN welcome fallback missing phrase: {phrase}")

    def test_fallbacks_have_no_markdown_breakers(self):
        """The bot sends the welcome with parse_mode=Markdown: a bare * or _
        in the fallback would break formatting of the whole message."""
        for lang in (LANGUAGE_RU, LANGUAGE_EN):
            with self.subTest(lang=lang):
                text = GAME_STRINGS[lang]["welcome_text_fallback"]
                self.assertNotIn("*", text)
                self.assertNotIn("_", text)


class TestWelcomePromptRulesInstruction(unittest.TestCase):
    """build_game_title_prompts must tell the LLM to include the rules block."""

    def test_ru_prompt_requires_rules_block(self):
        _, user = build_game_title_prompts(LANGUAGE_RU, None, use_vs=False, vs_k=0)
        for phrase in ("Как можно проиграть", "Как победить", "угроза растёт каждый ход"):
            self.assertIn(phrase, user, f"RU title prompt missing instruction: {phrase}")

    def test_en_prompt_requires_rules_block(self):
        _, user = build_game_title_prompts(LANGUAGE_EN, None, use_vs=False, vs_k=0)
        for phrase in ("How you can lose", "How to win", "threat rises every turn"):
            self.assertIn(phrase, user, f"EN title prompt missing instruction: {phrase}")


if __name__ == "__main__":
    unittest.main()
