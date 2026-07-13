"""Tests for verbalize_sampling module."""

import unittest
from verbalize_sampling import select_response, verbalize_prompt


class TestVerbalizePrompt(unittest.TestCase):
    def test_adds_distribution_framing(self):
        system = "You are a Game Master."
        user = "Create a mission."
        hint = "Vary genre and tone."
        vs_system, vs_user = verbalize_prompt(system, user, hint, k=3)

        self.assertIn("distribution", vs_system.lower())
        self.assertIn("3", vs_user)
        self.assertIn("probability", vs_user.lower())
        self.assertIn("Vary genre and tone", vs_user)

    def test_preserves_original_content(self):
        system = "You are Game Master."
        user = "Create a mission about first contact."
        hint = "Vary genre."
        vs_system, vs_user = verbalize_prompt(system, user, hint, k=5)

        self.assertIn("You are Game Master", vs_system)
        self.assertIn("Create a mission about first contact", vs_user)

    def test_k_in_user_prompt(self):
        _, vs_user = verbalize_prompt("S", "U", "", k=7)
        self.assertIn("7", vs_user)


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
