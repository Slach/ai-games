"""Tests for verbalize_sampling module."""

import unittest
from verbalize_sampling import select_response


class TestSelectResponse(unittest.TestCase):
    def test_selects_weighted_full(self):
        responses = [
            {"probability": 0.6, "text": "A"},
            {"probability": 0.3, "text": "B"},
            {"probability": 0.1, "text": "C"},
        ]
        counts = {"A": 0, "B": 0, "C": 0}
        for _ in range(1000):
            result = select_response(responses, "full")
            counts[result["text"]] += 1
        # A should win most often
        self.assertGreater(counts["A"], counts["B"])
        self.assertGreater(counts["A"], counts["C"])

    def test_selects_tails_only(self):
        responses = [
            {"probability": 0.7, "text": "common"},
            {"probability": 0.2, "text": "uncommon"},
            {"probability": 0.05, "text": "rare"},
            {"probability": 0.05, "text": "very_rare"},
        ]
        for _ in range(50):
            result = select_response(responses, "tails")
            self.assertIn(result["text"], ["rare", "very_rare"])

    def test_normalizes_probabilities(self):
        responses = [
            {"probability": 2.0, "text": "A"},
            {"probability": 2.0, "text": "B"},
        ]
        counts = {"A": 0, "B": 0}
        for _ in range(200):
            result = select_response(responses, "full")
            counts[result["text"]] += 1
        # Should be roughly 50/50
        self.assertGreater(counts["A"], 40)
        self.assertGreater(counts["B"], 40)

    def test_empty_responses_raises(self):
        with self.assertRaises(ValueError):
            select_response([], "full")

    def test_single_response_returns_it(self):
        result = select_response([{"probability": 1.0, "text": "only"}], "full")
        self.assertEqual(result["text"], "only")


if __name__ == "__main__":
    unittest.main()
