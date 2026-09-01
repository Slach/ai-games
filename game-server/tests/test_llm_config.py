"""Tests for llm_config parameter resolution: MODEL_NAME -> use case -> params."""

import unittest

from llm_config import (
    DEFAULT_USE_CASES,
    MAX_TOKENS_AVATAR,
    MAX_TOKENS_DEFAULT,
    MODEL_SAMPLING_MODES,
    MODEL_USE_CASES,
    LLMParams,
    _use_case,
    resolve_llm_params,
    resolve_max_tokens,
)


class FakeServer:
    """Minimal stand-in for GameServer max_tokens attributes."""

    def __init__(self, vs_enabled: bool, llm_max_tokens=32768, llm_max_avatar_tokens=4096):
        self.vs_enabled = vs_enabled
        self.llm_max_tokens = llm_max_tokens
        self.llm_max_avatar_tokens = llm_max_avatar_tokens


TEST_MODEL = "test-model/override"


class LLMConfigTests(unittest.TestCase):
    def test_every_default_use_case_has_vs_and_non_vs_branches(self):
        for name, branches in DEFAULT_USE_CASES.items():
            with self.subTest(use_case=name):
                self.assertIn("vs", branches)
                self.assertIn("non_vs", branches)
                self.assertIsInstance(branches["vs"], LLMParams)
                self.assertIsInstance(branches["non_vs"], LLMParams)

    def test_thinking_enabled_only_for_outcome_use_cases(self):
        thinking_cases = {
            name
            for name, branches in DEFAULT_USE_CASES.items()
            if branches["non_vs"].enable_thinking
        }
        self.assertEqual(thinking_cases, {"combined_outcome", "game_over_outcome"})

    def test_resolve_selects_branch_by_vs_flag(self):
        # combined_outcome has identical vs/non_vs params — pick a divergent case.
        vs = resolve_llm_params("any-model", "npc_choice", vs_enabled=True)
        non_vs = resolve_llm_params("any-model", "npc_choice", vs_enabled=False)
        self.assertEqual(vs.max_tokens, 2048)
        self.assertEqual(non_vs.max_tokens, 512)

    def test_unknown_use_case_raises(self):
        with self.assertRaises(KeyError):
            resolve_llm_params("any-model", "does_not_exist", vs_enabled=False)

    def test_resolve_max_tokens_literal_passthrough(self):
        self.assertEqual(resolve_max_tokens(8192, FakeServer(False)), 8192)

    def test_resolve_max_tokens_default_sentinel(self):
        server = FakeServer(False, llm_max_tokens=12345)
        self.assertEqual(resolve_max_tokens(MAX_TOKENS_DEFAULT, server), 12345)

    def test_resolve_max_tokens_avatar_sentinel(self):
        server = FakeServer(False, llm_max_avatar_tokens=777)
        self.assertEqual(resolve_max_tokens(MAX_TOKENS_AVATAR, server), 777)

    def test_game_title_uses_default_sentinel(self):
        params = resolve_llm_params("any-model", "game_title", vs_enabled=False)
        self.assertEqual(params.max_tokens, MAX_TOKENS_DEFAULT)

    def test_npc_name_has_highest_temperature(self):
        params = resolve_llm_params("any-model", "npc_name", vs_enabled=False)
        self.assertAlmostEqual(params.temperature, 0.95)

    def test_model_override_takes_precedence_over_default(self):
        override = _use_case(
            LLMParams(temperature=0.42, max_tokens=999),
            LLMParams(temperature=0.42, max_tokens=999),
        )
        MODEL_USE_CASES.setdefault(TEST_MODEL, {})["npc_name"] = override
        try:
            params = resolve_llm_params(TEST_MODEL, "npc_name", vs_enabled=False)
            self.assertAlmostEqual(params.temperature, 0.42)
            self.assertEqual(params.max_tokens, 999)
        finally:
            MODEL_USE_CASES[TEST_MODEL].pop("npc_name")

    def test_model_override_falls_back_to_default_for_unlisted_use_cases(self):
        # TEST_MODEL exists in MODEL_USE_CASES but lists no use cases.
        MODEL_USE_CASES.setdefault(TEST_MODEL, {})
        try:
            params = resolve_llm_params(TEST_MODEL, "npc_name", vs_enabled=False)
            self.assertAlmostEqual(params.temperature, 0.95)
        finally:
            MODEL_USE_CASES.pop(TEST_MODEL, None)

    def test_unknown_model_uses_default_table(self):
        params = resolve_llm_params("never-seen-model", "npc_name", vs_enabled=False)
        self.assertAlmostEqual(params.temperature, 0.95)

    def test_qwen38_instruct_mode_sampling_overrides(self):
        params = resolve_llm_params("unsloth/Qwen3.8-27B-MTP", "npc_name", vs_enabled=False)
        self.assertAlmostEqual(params.temperature, 0.7)
        self.assertAlmostEqual(params.top_p, 0.80)
        self.assertEqual(params.top_k, 20)
        self.assertAlmostEqual(params.min_p, 0.0)
        self.assertAlmostEqual(params.presence_penalty, 1.5)
        self.assertAlmostEqual(params.repetition_penalty, 1.0)
        # max_tokens / enable_thinking keep their use-case values
        self.assertEqual(params.max_tokens, 256)
        self.assertFalse(params.enable_thinking)

    def test_qwen38_thinking_mode_sampling_overrides(self):
        params = resolve_llm_params("unsloth/Qwen3.8-27B-MTP", "combined_outcome", vs_enabled=False)
        self.assertAlmostEqual(params.temperature, 1.0)
        self.assertAlmostEqual(params.top_p, 0.95)
        self.assertEqual(params.top_k, 20)
        self.assertAlmostEqual(params.presence_penalty, 0.0)
        self.assertAlmostEqual(params.repetition_penalty, 1.0)
        self.assertTrue(params.enable_thinking)

    def test_sampling_modes_do_not_affect_other_models(self):
        params = resolve_llm_params("never-seen-model", "npc_name", vs_enabled=False)
        self.assertAlmostEqual(params.temperature, 0.95)
        self.assertIsNone(params.top_p)
        self.assertIsNone(params.presence_penalty)

    def test_every_sampling_mode_entry_lists_all_fields(self):
        expected = {"temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty"}
        for model, modes in MODEL_SAMPLING_MODES.items():
            for thinking, overrides in modes.items():
                with self.subTest(model=model, thinking=thinking):
                    self.assertEqual(set(overrides), expected)


if __name__ == "__main__":
    unittest.main()
