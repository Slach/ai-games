"""Idempotency tests for POST /game/actions (submit_player_action).

Action ids repeat every turn, and a briefing keeps its keyboard alive —
double presses (stale turn button + current turn button, or a Telegram
callback retry) used to submit twice: two player_actions rows and two
chosen-action image generations/pushes for the same turn (2026-11-01,
game khfan2, player 281412419). Re-submitting the same choice must be a
no-op; a first submit and a changed choice must keep working.
"""

import asyncio
import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# main.py hardcodes a daily /app/logs file handler at import time — that
# directory only exists inside the container. Redirect file logging to a
# temp file for the duration of the import so the module loads on the host.
_orig_file_handler = logging.FileHandler


class _TempFileHandler(logging.FileHandler):
    def __init__(self, filename, *args, **kwargs):
        if str(filename).startswith("/app/logs/"):
            filename = os.path.join(tempfile.mkdtemp(), os.path.basename(filename))
        super().__init__(filename, *args, **kwargs)


logging.FileHandler = _TempFileHandler
import main  # noqa: E402

logging.FileHandler = _orig_file_handler

PROFILE = {"player_id": 281412419, "game_id": "khfan2", "is_dead": False, "is_spectator": False}

CHOICES = [
    {"id": "action_1", "text": "First", "consequence": "", "consequence_kind": ""},
    {"id": "action_2", "text": "Second", "consequence": "", "consequence_kind": ""},
]


class SubmitPlayerActionIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def _submit(self, briefing, action_id):
        with patch.object(main, "get_player_profile", return_value=PROFILE), \
                patch.object(main, "get_player_briefing", return_value=briefing), \
                patch.object(main, "update_briefing_choice") as update_choice, \
                patch.object(main, "save_player_action") as save_action, \
                patch.object(main, "save_player_action_stats"), \
                patch.object(main, "get_game_state", return_value={"hull_integrity": 100}), \
                patch.object(main, "get_game_language", return_value="ru"), \
                patch.object(main, "_generate_chosen_action_image", new_callable=AsyncMock) as generate, \
                patch.object(main, "get_players_who_need_to_choose", return_value=[]), \
                patch.object(main, "_analyze_turn_outcome", new_callable=AsyncMock):
            response = await main.submit_player_action(
                main.PlayerActionRequest(player_id=281412419, turn=4, action_id=action_id, choice="selected")
            )
            # Let the create_task'd image generation / outcome check run out
            await asyncio.sleep(0)
        return response, update_choice, save_action, generate

    async def test_resubmit_of_same_choice_is_noop(self):
        briefing = {"id": 1, "choices": CHOICES, "selected_action_id": "action_1"}
        response, update_choice, save_action, generate = await self._submit(briefing, "action_1")
        self.assertEqual(response["status"], "already_chosen")
        update_choice.assert_not_called()
        save_action.assert_not_called()
        generate.assert_not_called()

    async def test_first_submit_is_accepted(self):
        briefing = {"id": 1, "choices": CHOICES, "selected_action_id": None}
        response, update_choice, save_action, generate = await self._submit(briefing, "action_1")
        self.assertEqual(response["status"], "accepted")
        update_choice.assert_called_once()
        save_action.assert_called_once()
        self.assertEqual(generate.await_count, 1)
        self.assertEqual(generate.await_args.kwargs["action_id"], "action_1")

    async def test_changed_choice_is_still_recorded(self):
        briefing = {"id": 1, "choices": CHOICES, "selected_action_id": "action_1"}
        response, update_choice, save_action, generate = await self._submit(briefing, "action_2")
        self.assertEqual(response["status"], "accepted")
        update_choice.assert_called_once()
        save_action.assert_called_once()
        self.assertEqual(generate.await_count, 1)


if __name__ == "__main__":
    unittest.main()
