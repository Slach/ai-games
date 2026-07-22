"""Tests for NPC avatar prompt generation coverage and retry logic.

Regression for game 7hkua6: the LLM split 9 NPC roles across k VS options
(each option held a subset of roles) and select_response picked one with only
5 roles, so 4 NPCs never got avatars. generate_npc_avatar_prompts now (a)
demands all roles per option and (b) re-requests just the missing role_keys
instead of falling back to canned prompts.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_server import GameServer  # noqa: E402


def _roles(*keys: str) -> list[dict]:
    return [
        {
            "role_key": k,
            "role_name": k.replace("_", " ").title(),
            "species": "human",
            "gender": "female",
            "personality_traits": ["calm"],
        }
        for k in keys
    ]


class TestNpcAvatarCoverage(unittest.TestCase):
    def _run(self, npc_roles, llm_returns, vs_enabled):
        agent = GameServer(language="en")
        agent.vs_enabled = vs_enabled
        with patch.object(GameServer, "_call_llm", side_effect=llm_returns) as calls:
            return agent.generate_npc_avatar_prompts(
                npc_roles, game_id=None, player_id=None, turn=None, kind=None
            ), calls.call_count

    def test_non_vs_retries_until_all_roles_covered(self):
        roles = _roles("a", "b", "c", "d")
        first = {"prompts": [{"role_key": "a", "prompt": "pa"}, {"role_key": "b", "prompt": "pb"}]}
        second = {"prompts": [{"role_key": "c", "prompt": "pc"}, {"role_key": "d", "prompt": "pd"}]}
        result, n_calls = self._run(roles, [first, second], vs_enabled=False)

        covered = {p["role_key"] for p in result}
        self.assertEqual(covered, {"a", "b", "c", "d"})
        self.assertEqual(n_calls, 2)

    def test_non_vs_no_retry_when_complete_first_time(self):
        roles = _roles("a", "b")
        first = {"prompts": [{"role_key": "a", "prompt": "pa"}, {"role_key": "b", "prompt": "pb"}]}
        result, n_calls = self._run(roles, [first], vs_enabled=False)

        self.assertEqual({p["role_key"] for p in result}, {"a", "b"})
        self.assertEqual(n_calls, 1)

    def test_non_vs_gives_up_after_max_retries_without_fallback(self):
        # LLM never returns the missing role; we must NOT silently produce a
        # canned fallback prompt — we return what we got (partial) and stop.
        roles = _roles("a", "b")
        first = {"prompts": [{"role_key": "a", "prompt": "pa"}]}
        result, n_calls = self._run(roles, [first, first, first, first], vs_enabled=False)

        self.assertEqual({p["role_key"] for p in result}, {"a"})
        # 1 initial + 3 retries
        self.assertEqual(n_calls, 4)
        # No canned fallback text present
        self.assertFalse(any("Star Trek character portrait of" in p["prompt"] for p in result))

    def test_vs_mode_retries_missing_roles(self):
        roles = _roles("a", "b", "c")

        def vs_response(prompts):
            return {"responses": [{"probability": 1.0, "text": {"prompts": prompts}}]}

        first = vs_response([{"role_key": "a", "prompt": "pa"}])
        second = vs_response([{"role_key": "b", "prompt": "pb"}, {"role_key": "c", "prompt": "pc"}])
        result, n_calls = self._run(roles, [first, second], vs_enabled=True)

        self.assertEqual({p["role_key"] for p in result}, {"a", "b", "c"})
        self.assertEqual(n_calls, 2)


if __name__ == "__main__":
    unittest.main()
