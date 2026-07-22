"""Tests for the cohesive crew dialogue scene generation.

Covers: weighted speaker selection (live players 2x over NPCs), the
too-few-members early return, the output format (role embedded in the
'npc' field), and the per-participant line mapping used to feed dialogue
context back into personal briefings.
"""

import collections
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_server import GameServer  # noqa: E402


def _member(mtype, key, name, role):
    """Build a roster entry matching all_participants shape."""
    entry = {
        "type": mtype,
        "role": role,
        "name": name,
        "species": "human",
        "personality_traits": ["calm", "brave"],
    }
    if mtype == "player":
        entry["player_id"] = key
    else:
        entry["npc_key"] = key
    return entry


class TestCrewDialogueScene(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gm = GameServer(language="ru")
        self.gm.crew_dialogue_speakers = 3

    async def _run(self, pool, llm_return):
        with patch.object(GameServer, "_call_llm", new=AsyncMock(return_value=llm_return)):
            return await self.gm.generate_crew_scene_dialogue(
                "Корабль под атакой.",
                pool,
                game_id="g1",
                player_id=None,
                turn=1,
                kind="crew_dialogue",
            )

    async def test_fewer_than_two_members_returns_empty(self):
        pool = [_member("player", "1", "Соло", "Пилот")]
        dialogues, lines = await self._run(pool, {"lines": []})
        self.assertEqual(dialogues, [])
        self.assertEqual(lines, {})

    async def test_two_members_uses_all(self):
        pool = [
            _member("player", "1", "Аня", "Капитан"),
            _member("npc", "eng", "Бот", "Инженер"),
        ]
        llm = {"lines": [
            {"speaker": "Аня", "dialogue": "Доклад!"},
            {"speaker": "Бот", "dialogue": "Щиты держатся."},
            {"speaker": "Аня", "dialogue": "Готовь манёвр."},
            {"speaker": "Бот", "dialogue": "Принято."},
        ]}
        dialogues, lines = await self._run(pool, llm)
        self.assertEqual(len(dialogues), 4)
        # role embedded in the npc field
        self.assertIn("(Капитан)", dialogues[0]["npc"])
        self.assertIn("(Инженер)", dialogues[1]["npc"])
        # per-key line mapping
        self.assertEqual(lines["player:1"], ["Доклад!", "Готовь манёвр."])
        self.assertEqual(lines["npc:eng"], ["Щиты держатся.", "Принято."])

    async def test_capped_at_crew_dialogue_speakers(self):
        self.gm.crew_dialogue_speakers = 2
        pool = [
            _member("player", "1", "Аня", "Капитан"),
            _member("player", "2", "Боря", "Пилот"),
            _member("player", "3", "Вера", "Медик"),
            _member("npc", "eng", "Бот", "Инженер"),
        ]
        llm = {"lines": []}
        # Patch the LLM to capture which speakers were passed in the prompt.
        captured = {}

        async def fake_call(system_prompt, user_prompt, **kwargs):
            captured["user"] = user_prompt
            return {"lines": []}

        with patch.object(GameServer, "_call_llm", side_effect=fake_call):
            await self.gm.generate_crew_scene_dialogue(
                "Нарратив.", pool, game_id="g1", player_id=None, turn=1, kind="crew_dialogue"
            )
        # Only 2 speaker entries ("1." and "2.") should be in the prompt.
        self.assertEqual(captured["user"].count("\n2. "), 1)
        self.assertNotIn("\n3. ", captured["user"])

    async def test_live_players_selected_more_often_than_npcs(self):
        # 3 live players + 3 NPCs, pick 3 each run. With 2x weight on players,
        # players should dominate over many runs.
        pool = [
            _member("player", "p1", "P1", "Пилот"),
            _member("player", "p2", "P2", "Навигатор"),
            _member("player", "p3", "P3", "Медик"),
            _member("npc", "n1", "N1", "Инженер"),
            _member("npc", "n2", "N2", "Стрелок"),
            _member("npc", "n3", "N3", "Связист"),
        ]
        selected_names = collections.Counter()
        with patch.object(GameServer, "_call_llm", new=AsyncMock(return_value={"lines": []})) as mock_call:
            for _ in range(400):
                await self.gm.generate_crew_scene_dialogue(
                    "Нарратив.", pool, game_id="g1", player_id=None, turn=1, kind="crew_dialogue"
                )
            for call in mock_call.call_args_list:
                user = call.kwargs.get("user_prompt") or call.args[1]
                if any(nm in user for nm in ("P1", "P2", "P3")):
                    selected_names["player"] += 1
                elif any(nm in user for nm in ("N1", "N2", "N3")):
                    selected_names["npc"] += 1
        # Each run contributes up to 3 speakers; across 400 runs players
        # (2x weight) must outnumber NPCs.
        self.assertGreater(selected_names["player"], selected_names["npc"])

    async def test_empty_llm_response_returns_empty(self):
        pool = [
            _member("player", "1", "Аня", "Капитан"),
            _member("npc", "eng", "Бот", "Инженер"),
        ]
        dialogues, lines = await self._run(pool, {"lines": []})
        self.assertEqual(dialogues, [])
        self.assertEqual(lines, {})

    async def test_lines_with_blank_speaker_or_text_skipped(self):
        pool = [
            _member("player", "1", "Аня", "Капитан"),
            _member("npc", "eng", "Бот", "Инженер"),
        ]
        llm = {"lines": [
            {"speaker": "Аня", "dialogue": "Вперёд."},
            {"speaker": "", "dialogue": "пустой спикер"},
            {"speaker": "Бот", "dialogue": ""},
            {"speaker": "Бот", "dialogue": "Принято."},
        ]}
        dialogues, lines = await self._run(pool, llm)
        self.assertEqual(len(dialogues), 2)
        self.assertEqual(lines["player:1"], ["Вперёд."])
        self.assertEqual(lines["npc:eng"], ["Принято."])


if __name__ == "__main__":
    unittest.main()
