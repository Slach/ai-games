"""Unit tests for the /v1/systemone decision-model client (decision_model.py)
and its integration into GameServer.generate_npc_choice."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import decision_model  # noqa: E402
from game_server import GameServer  # noqa: E402

_CHOICES = [
    {"id": "action_1", "text": "Scan the anomaly"},
    {"id": "action_2", "text": "Reroute power"},
    {"id": "action_3", "text": "Vent the deck"},
]


class TestBuildDecisionPayload(unittest.TestCase):
    def test_state_contains_persona_and_loyalty_rule(self):
        payload = decision_model.build_decision_payload(
            "ggml-org/lev", "Рипли", "Инженер", "храбрая, упрямая", 25, _CHOICES
        )
        self.assertEqual(payload["model"], "ggml-org/lev")
        self.assertIn("Рипли", payload["state"])
        self.assertIn("Инженер", payload["state"])
        self.assertIn("25/100", payload["state"])
        # loyalty 25 is the on_edge band — self-preservation rule must be in state
        self.assertIn("self-preservation", payload["state"])

    def test_criteria_map_offered_actions_only(self):
        payload = decision_model.build_decision_payload(
            "ggml-org/lev", "Ripley", "Engineer", "brave", 70, _CHOICES
        )
        criteria = payload["questions"]["action"]["criteria"]
        self.assertEqual(criteria, {c["id"]: c["text"] for c in _CHOICES})
        self.assertEqual(payload["questions"]["action"]["type"], "choice")

    def test_dialogue_context_appended_to_state(self):
        plain = decision_model.build_decision_payload(
            "ggml-org/lev", "Ripley", "Engineer", "brave", 70, _CHOICES
        )
        with_ctx = decision_model.build_decision_payload(
            "ggml-org/lev", "Ripley", "Engineer", "brave", 70, _CHOICES,
            dialogue_context="Medic: hull breach on deck 3.",
        )
        self.assertIn("Medic: hull breach on deck 3.", with_ctx["state"])
        self.assertNotIn("Medic", plain["state"])


class _FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self):
        return self._body


class _FakeClient:
    """Minimal async-context stand-in for httpx.AsyncClient."""

    body: dict = {}
    status: int = 200
    fail: Exception | None = None
    last_payload: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json=None):
        _FakeClient.last_payload = json
        if _FakeClient.fail is not None:
            raise _FakeClient.fail
        return _FakeResponse(_FakeClient.body, _FakeClient.status)


def _systemone_body(probs, confidence=0.5):
    return {"answers": {"action": {"type": "choice", "probabilities": probs,
                                   "choice": max(probs, key=probs.get),
                                   "confidence": confidence}},
            "usage": {"input_tokens": 10, "output_tokens": 0}}


class TestChooseAction(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _FakeClient.body = {}
        _FakeClient.status = 200
        _FakeClient.fail = None
        _FakeClient.last_payload = None

    async def test_returns_sampled_action_from_probabilities(self):
        _FakeClient.body = _systemone_body({"action_1": 0.1, "action_2": 0.8, "action_3": 0.1})
        with patch.object(decision_model.httpx, "AsyncClient", _FakeClient), \
             patch("decision_model.write_llm_log"):
            decision_model.random.seed(1)
            chosen = await decision_model.choose_action(
                url="http://x/v1/systemone", model="ggml-org/lev",
                npc_name="Ripley", npc_role="Engineer", traits_str="brave", loyalty=70,
                choices=_CHOICES, game_id=None, player_id=None, turn=1, kind="npc_choice_systemone",
            )
        # seed 1 draws ~0.13 -> second-highest weight wins here; any valid id is
        # fine, the guarantee is it comes from the offered set
        self.assertIn(chosen, {c["id"] for c in _CHOICES})

    async def test_dominant_probability_always_wins(self):
        _FakeClient.body = _systemone_body({"action_1": 0.98, "action_2": 0.01, "action_3": 0.01})
        with patch.object(decision_model.httpx, "AsyncClient", _FakeClient), \
             patch("decision_model.write_llm_log"):
            for seed in range(5):
                decision_model.random.seed(seed)
                chosen = await decision_model.choose_action(
                    url="http://x/v1/systemone", model="ggml-org/lev",
                    npc_name="Ripley", npc_role="Engineer", traits_str="brave", loyalty=70,
                    choices=_CHOICES, game_id=None, player_id=None, turn=1, kind="npc_choice_systemone",
                )
                self.assertEqual(chosen, "action_1")

    async def test_http_error_returns_none(self):
        _FakeClient.fail = httpx.ConnectError("no route")
        with patch.object(decision_model.httpx, "AsyncClient", _FakeClient), \
             patch("decision_model.write_llm_log") as log_mock:
            chosen = await decision_model.choose_action(
                url="http://x/v1/systemone", model="ggml-org/lev",
                npc_name="Ripley", npc_role="Engineer", traits_str="brave", loyalty=70,
                choices=_CHOICES, game_id=None, player_id=None, turn=1, kind="npc_choice_systemone",
            )
        self.assertIsNone(chosen)
        self.assertEqual(log_mock.call_count, 2)  # request + error response logged

    async def test_malformed_answer_returns_none(self):
        for body in [
            {},
            {"answers": {}},
            {"answers": {"action": {"probabilities": "not-a-dict"}}},
        ]:
            with self.subTest(body=body):
                _FakeClient.body = body
                with patch.object(decision_model.httpx, "AsyncClient", _FakeClient), \
                     patch("decision_model.write_llm_log"):
                    chosen = await decision_model.choose_action(
                        url="http://x/v1/systemone", model="ggml-org/lev",
                        npc_name="Ripley", npc_role="Engineer", traits_str="brave", loyalty=70,
                        choices=_CHOICES, game_id=None, player_id=None, turn=1,
                        kind="npc_choice_systemone",
                    )
                self.assertIsNone(chosen)

    async def test_probabilities_not_covering_actions_returns_none(self):
        _FakeClient.body = _systemone_body({"other_1": 0.5, "other_2": 0.5})
        with patch.object(decision_model.httpx, "AsyncClient", _FakeClient), \
             patch("decision_model.write_llm_log"):
            chosen = await decision_model.choose_action(
                url="http://x/v1/systemone", model="ggml-org/lev",
                npc_name="Ripley", npc_role="Engineer", traits_str="brave", loyalty=70,
                choices=_CHOICES, game_id=None, player_id=None, turn=1,
                kind="npc_choice_systemone",
            )
        self.assertIsNone(chosen)

    async def test_payload_sent_to_endpoint(self):
        _FakeClient.body = _systemone_body({"action_1": 1.0})
        with patch.object(decision_model.httpx, "AsyncClient", _FakeClient), \
             patch("decision_model.write_llm_log"):
            await decision_model.choose_action(
                url="http://x/v1/systemone", model="ggml-org/lev",
                npc_name="Ripley", npc_role="Engineer", traits_str="brave", loyalty=70,
                choices=_CHOICES, game_id="g1", player_id="npc_x_g1", turn=2,
                kind="npc_choice_systemone",
            )
        self.assertEqual(_FakeClient.last_payload["model"], "ggml-org/lev")
        self.assertIn("criteria", _FakeClient.last_payload["questions"]["action"])


class TestGenerateNpcChoiceIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_decision_model_choice_skips_generative_llm(self):
        agent = GameServer(language="en")
        agent.vs_enabled = False
        with patch("game_server.choose_action", new=AsyncMock(return_value="action_3")), \
             patch.object(GameServer, "_call_llm", new=AsyncMock(side_effect=AssertionError("must not be called"))):
            decision = await agent.generate_npc_choice(
                [dict(c, consequence_kind="progress", consequence="x") for c in _CHOICES],
                {"npc_name": "Ripley", "role": "Engineer", "personality_traits": ["brave"]},
                game_id=None, player_id=None, turn=1, kind="npc_choice",
            )
        self.assertEqual(decision, {"action_id": "action_3"})

    async def test_decision_model_unknown_action_falls_back_to_generative(self):
        agent = GameServer(language="en")
        agent.vs_enabled = False
        with patch("game_server.choose_action", new=AsyncMock(return_value="bogus")), \
             patch.object(GameServer, "_call_llm", new=AsyncMock(return_value={"action_id": "action_1"})):
            decision = await agent.generate_npc_choice(
                _CHOICES,
                {"npc_name": "Ripley", "role": "Engineer", "personality_traits": []},
                game_id=None, player_id=None, turn=1, kind="npc_choice",
            )
        self.assertEqual(decision, {"action_id": "action_1"})

    async def test_no_rationale_key_anywhere(self):
        agent = GameServer(language="en")
        agent.vs_enabled = False
        with patch("game_server.choose_action", new=AsyncMock(return_value="action_2")):
            decision = await agent.generate_npc_choice(
                _CHOICES,
                {"npc_name": "Ripley", "role": "Engineer", "personality_traits": []},
                game_id=None, player_id=None, turn=1, kind="npc_choice",
            )
        self.assertNotIn("rationale", decision)
        self.assertNotIn("rationale", decision_model.build_decision_payload(
            "m", "n", "r", "t", 70, _CHOICES
        ))


if __name__ == "__main__":
    unittest.main()
