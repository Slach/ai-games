"""Tests for the tolerant LLM JSON parser (_parse_llm_json).

Regression (ixisyr turn 11, 2026-11-11): the model appended trailing junk
after the JSON object; raw json.loads raised "Extra data" in BOTH the
structured and the fallback paths, and the whole turn ran on the
'Unknown space region' default global circumstances.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_server import _parse_llm_json  # noqa: E402


class TestParseLlmJson(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(_parse_llm_json('{"a": 1}'), {"a": 1})

    def test_trailing_prose_after_object(self):
        raw = '{"setting": "bridge", "conflict": "pursuit"} Hope this helps!'
        self.assertEqual(_parse_llm_json(raw), {"setting": "bridge", "conflict": "pursuit"})

    def test_trailing_fragment_after_object(self):
        raw = '{"a": {"b": 2}} {"c": 3}'
        self.assertEqual(_parse_llm_json(raw), {"a": {"b": 2}})

    def test_leading_prose_before_object(self):
        raw = 'Sure! Here is the JSON: {"x": "y"}'
        self.assertEqual(_parse_llm_json(raw), {"x": "y"})

    def test_no_object_raises(self):
        with self.assertRaises(ValueError):
            _parse_llm_json("no json here at all")

    def test_truncated_object_raises_jsondecodeerror(self):
        # A max_tokens-truncated response stays a hard error (the caller
        # falls back / retries) — the parser must not invent data.
        with self.assertRaises(Exception):
            _parse_llm_json('{"setting": "bridge", "narrative": "uncut')


if __name__ == "__main__":
    unittest.main()
