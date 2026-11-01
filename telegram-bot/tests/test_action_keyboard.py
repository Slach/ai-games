"""Regression tests for action-keyboard callback_data turn encoding.

Action ids repeat every turn (action_1..action_N), so a callback of
"action:{action_id}" made buttons from different turns byte-identical.
A player who missed a turn and later pressed [1] on the old message
silently recorded that choice into the *current* turn (2026-11-01,
game khfan2, player 281412419: two action_1 submits and two image
generations for turn 4). The keyboard must encode the turn so the
handler can reject presses from finished turns.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402
from language import LANGUAGE_EN, LANGUAGE_RU  # noqa: E402

ACTIONS = [
    {"id": "action_1", "text": "First"},
    {"id": "action_2", "text": "Second"},
    {"id": "action_3", "text": "Third"},
]


def _callback_data(keyboard, index: int = 0) -> str:
    return keyboard.inline_keyboard[0][index].callback_data


class ActionKeyboardCallbackTests(unittest.TestCase):
    def test_callback_encodes_turn_and_action_id(self):
        keyboard = bot.create_action_keyboard(ACTIONS, None, turn=4)
        self.assertEqual(_callback_data(keyboard, 0), "action:4:action_1")
        self.assertEqual(_callback_data(keyboard, 1), "action:4:action_2")

    def test_same_action_different_turn_is_not_identical(self):
        # The exact khfan2 regression: [1] from turn 3 vs [1] from turn 4.
        turn3 = bot.create_action_keyboard(ACTIONS, None, turn=3)
        turn4 = bot.create_action_keyboard(ACTIONS, None, turn=4)
        self.assertNotEqual(_callback_data(turn3), _callback_data(turn4))

    def test_selected_marker_does_not_change_callback_data(self):
        plain = bot.create_action_keyboard(ACTIONS, None, turn=2)
        marked = bot.create_action_keyboard(ACTIONS, "action_2", turn=2)
        self.assertEqual(_callback_data(plain, 1), _callback_data(marked, 1))
        self.assertIn("✅", marked.inline_keyboard[0][1].text)

    def test_callback_data_fits_telegram_64_byte_limit(self):
        keyboard = bot.create_action_keyboard([{"id": "action_1", "text": "x"}], None, turn=999)
        for button in keyboard.inline_keyboard[0]:
            self.assertLessEqual(len(button.callback_data), 64)

    def test_stale_turn_error_defined_for_both_languages(self):
        for language in (LANGUAGE_RU, LANGUAGE_EN):
            with self.subTest(language=language):
                self.assertTrue(bot.lang.get_errors(language)["stale_turn"])


if __name__ == "__main__":
    unittest.main()
