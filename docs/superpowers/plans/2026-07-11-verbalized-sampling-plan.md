# Verbalized Sampling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Verbalized Sampling (VS) prompting to all creative generation points in AI Games — missions, narrative, NPC dialogue, avatars, image prompts — to break mode collapse and increase output diversity.

**Architecture:** New `game-server/verbalize_sampling.py` module with `VSConfig`, `verbalize_prompt()` (instance→distribution prompt wrapper), and `select_response()` (weighted random selection). All `build_*_prompts` functions gain `use_vs=False` parameter. `game_server.py` callers use a generic `VS_RESPONSE_SCHEMA` then call `select_response()` to pick one candidate.

**Tech Stack:** Python 3.12, Pydantic, OpenAI client, unittest

## Global Constraints

- All imports at top of file
- Use UTF-8 characters directly, never `\uXXXX` escapes
- Use `language.py` constants (`LANGUAGE_RU`/`LANGUAGE_EN`), never raw `'ru'`/`'en'` strings
- Never swallow exceptions silently — at minimum `logger.warning(..., exc_info=True)`
- Every `logger.error(...)` must include `exc_info=True` or `stack_info=True`
- No workarounds — fix causes, not symptoms
- Keep changes minimal: no refactoring beyond what VS requires
- `PYTHONDONTWRITEBYTECODE=1` for running python

---

### Task 1: Core data structures — VSConfig and VS_RESPONSE_SCHEMA

**Files:**

- Create: `game-server/verbalize_sampling.py`

**Interfaces:**

- Produces: `VSConfig` dataclass, `VS_RESPONSE_SCHEMA` dict, `DIVERSITY_HINTS` dict

- [ ] **Step 1: Create the module with VSConfig, VS_RESPONSE_SCHEMA, DIVERSITY_HINTS**

```python
"""Verbalized Sampling — inference-time prompting to break mode collapse.

See: Zhang et al., "Verbalized Sampling: How to Mitigate Mode Collapse
and Unlock LLM Diversity", ICLR 2026.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VSConfig:
    k: int = 5
    sampling_mode: str = "full"  # "full" | "tails"


VS_RESPONSE_SCHEMA: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "vs_responses",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "responses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "probability": {"type": "number"},
                            "text": {"type": "string"},
                        },
                        "required": ["probability", "text"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["responses"],
            "additionalProperties": False,
        },
    },
}


# Per-function diversity hints (axes of variation the model should explore)
DIVERSITY_HINTS: dict[str, str] = {
    "mission": (
        "Vary across these axes:\n"
        "- Genre (diplomacy, combat, mystery, exploration, sabotage)\n"
        "- Tone (dark, heroic, absurd, tense, melancholic)\n"
        "- Scale (personal drama, ship crisis, galactic threat)\n"
    ),
    "game_title": (
        "Vary across these axes:\n"
        "- Style (metaphorical, technical, ironic, epic)\n"
        "- Length (short punchy, multi-word epic)\n"
    ),
    "turn_story": (
        "Vary across these axes:\n"
        "- Direction (escalation, de-escalation, revelation, character moment)\n"
        "- Pacing (fast action, slow burn, sudden twist)\n"
    ),
    "global_circumstances": (
        "Vary across these axes:\n"
        "- Threat type (external, internal, natural phenomenon, technogenic)\n"
        "- Scene mood (hopeful, tense, mysterious, catastrophic)\n"
        "- Location variety (ship interior, planet surface, space anomaly, station)\n"
    ),
    "combined_outcome": (
        "Vary across these axes:\n"
        "- Outcome (success, partial success, complication, unexpected twist)\n"
        "- Consequences (immediate danger, long-term implication, moral dilemma)\n"
        "- Tone shift (things get worse, silver lining, pyrrhic victory)\n"
    ),
    "player_message": (
        "Vary across these axes:\n"
        "- GM tone (serious, ironic, mysterious, encouraging, ominous)\n"
        "- Response length (terse and punchy, detailed and atmospheric)\n"
        "- Mood must reflect the current scene circumstances.\n"
    ),
    "npc_decision": (
        "Vary across these axes:\n"
        "- Decision style (rational, emotional, risky, cautious, self-serving)\n"
        "- Must reflect the current scene mood and circumstances.\n"
    ),
    "species_description": (
        "Vary across these axes:\n"
        "- Unusualness of appearance (subtle alien, radically non-humanoid)\n"
        "- Textures (crystalline, biological, metallic, energy-based)\n"
        "- Silhouette and body plan (bipedal, floating, amorphous, multi-limbed)\n"
    ),
    "npc_name": (
        "Vary across these axes:\n"
        "- Name style (technical designation, poetic, alien phonetics, functional title)\n"
    ),
    "avatar": (
        "Vary across these axes:\n"
        "- Body form (humanoid, alien, energy being, cybernetic, symbiotic)\n"
        "- Camera angle (portrait, 3/4, full body, dynamic pose)\n"
        "- Environment (ship interior, lab, planet surface, void)\n"
        "- Mood (stoic, intense, serene, alien, unsettling)\n"
        "CRITICAL: For non-human species, at least 3 of 5 options MUST be non-humanoid forms.\n"
    ),
    "npc_avatars": (
        "Vary across these axes:\n"
        "- Body form (humanoid, alien, energy being, cybernetic, symbiotic)\n"
        "- Species-to-species visual diversity — no two NPCs look similar\n"
        "- Camera angle, environment, mood as above\n"
    ),
    "action_prompt": (
        "Vary across these axes:\n"
        "- Composition (wide shot, close-up, Dutch angle, overhead)\n"
        "- Lighting (dramatic shadows, neon glow, harsh sun, bioluminescent)\n"
        "- Camera angle (eye-level, low angle heroic, high angle vulnerable)\n"
        "- Action dynamics (mid-motion freeze, before/after moment)\n"
    ),
    "bridge_image": (
        "Vary across these axes:\n"
        "- Crew arrangement (tight cluster, spread across stations, dramatic tableau)\n"
        "- Bridge lighting (alert red, calm blue, emergency flicker, nebula glow through viewport)\n"
        "- Overall mood (ready for action, tense standoff, routine calm, crisis)\n"
    ),
    "scene_prompt": (
        "Vary across these axes:\n"
        "- Color palette (cold blues, warm ambers, sickly greens, stark monochrome)\n"
        "- Atmosphere (fog, sparks, zero-g float, alien bioluminescence)\n"
        "- Scene scale (intimate close-up, expansive epic wide shot)\n"
    ),
}
```

- [ ] **Step 2: Verify module imports cleanly**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -c "from verbalize_sampling import VSConfig, VS_RESPONSE_SCHEMA, DIVERSITY_HINTS; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add game-server/verbalize_sampling.py
git commit -m "feat: add verbalize_sampling module with VSConfig and DIVERSITY_HINTS"
```

---

### Task 2: select_response() — weighted random selection

**Files:**

- Modify: `game-server/verbalize_sampling.py`

**Interfaces:**

- Produces: `select_response(responses: list[dict], sampling_mode: str = "full") -> dict`

- [ ] **Step 1: Write the tests**

Create `game-server/tests/test_verbalize_sampling.py`:

```python
"""Tests for verbalize_sampling module."""

import unittest
from verbalize_sampling import select_response, VSConfig


class TestSelectResponse(unittest.TestCase):
    def test_selects_weighted_full(self):
        responses = [
            {"probability": 0.6, "text": "A"},
            {"probability": 0.3, "text": "B"},
            {"probability": 0.1, "text": "C"},
        ]
        # Run many times and check distribution roughly matches
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/test_verbalize_sampling.py -v
```

Expected: FAIL — ImportError or AttributeError

- [ ] **Step 3: Implement select_response()**

Add to `game-server/verbalize_sampling.py`:

```python
import random


def select_response(
    responses: list[dict],
    sampling_mode: str = "full",
) -> dict:
    """Weighted random selection from verbalized responses.

    Args:
        responses: List of {"probability": float, "text": str} dicts.
        sampling_mode: "full" (sample from all) or "tails" (only p < 0.10).

    Returns:
        The selected response dict.

    Raises:
        ValueError: If responses list is empty.
    """
    if not responses:
        raise ValueError("Cannot select from empty responses list")

    if len(responses) == 1:
        return responses[0]

    if sampling_mode == "tails":
        candidates = [r for r in responses if r["probability"] < 0.10]
        if not candidates:
            logger.warning("No tails candidates found (all p >= 0.10), falling back to full sampling")
            candidates = responses
        responses = candidates

    total = sum(r["probability"] for r in responses)
    if total <= 0:
        # All zeros — fall back to uniform
        logger.warning("All probabilities are zero or negative, using uniform selection")
        return random.choice(responses)

    r = random.uniform(0, total)
    cumulative = 0.0
    for resp in responses:
        cumulative += resp["probability"]
        if r <= cumulative:
            return resp

    # Fallback (shouldn't reach here)
    return responses[-1]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/test_verbalize_sampling.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add game-server/verbalize_sampling.py game-server/tests/test_verbalize_sampling.py
git commit -m "feat: add select_response() with weighted random selection"
```

---

### Task 3: verbalize_prompt() — instance → distribution prompt wrapper

**Files:**

- Modify: `game-server/verbalize_sampling.py`

**Interfaces:**

- Produces: `verbalize_prompt(system_prompt: str, user_prompt: str, diversity_hint: str, k: int = 5) -> tuple[str, str]`

- [ ] **Step 1: Write failing tests**

Add to `game-server/tests/test_verbalize_sampling.py`:

```python
from verbalize_sampling import verbalize_prompt


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
        vs_system, vs_user = verbalize_prompt(system, user, hint)

        self.assertIn("You are Game Master", vs_system)
        self.assertIn("Create a mission about first contact", vs_user)

    def test_k_in_user_prompt(self):
        _, vs_user = verbalize_prompt("S", "U", "", k=7)
        self.assertIn("7", vs_user)

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/test_verbalize_sampling.py::TestVerbalizePrompt -v
```

Expected: FAIL — ImportError or AttributeError

- [ ] **Step 3: Implement verbalize_prompt()**

Add to `game-server/verbalize_sampling.py`:

```python
def verbalize_prompt(
    system_prompt: str,
    user_prompt: str,
    diversity_hint: str,
    k: int = 5,
) -> tuple[str, str]:
    """Wrap instance-level prompt into distribution-level VS prompt.

    Args:
        system_prompt: Original system prompt.
        user_prompt: Original user prompt.
        diversity_hint: Hints for what axes to vary.
        k: Number of candidate responses to request.

    Returns:
        (modified_system_prompt, modified_user_prompt)
    """
    vs_system = (
        f"{system_prompt}\n\n"
        f"You are a creative generator using Verbalized Sampling. "
        f"For each request, output k={k} DIVERSE options with verbalized "
        f"probabilities. Each option must be meaningfully different — explore "
        f"the full distribution including likely, unlikely, and surprising options."
    )

    vs_user = (
        f"{user_prompt}\n\n"
        f"Generate {k} DIVERSE options for the above. Each option must be "
        f"meaningfully different from the others.\n"
        f"{diversity_hint}\n\n"
        f"For each option, assign a numeric probability (0.0-1.0) representing "
        f"how likely or appropriate this option is. Probabilities must sum to 1.0. "
        f"Include both high-probability (conventional) and low-probability "
        f"(creative, surprising) options.\n\n"
        f"Format: output as JSON with a \"responses\" array. Each entry has "
        f"\"probability\" (float) and \"text\" (string with the full response content)."
    )

    return vs_system, vs_user
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/test_verbalize_sampling.py::TestVerbalizePrompt -v
```

Expected: 3 passed

- [ ] **Step 5: Run full test suite**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/test_verbalize_sampling.py -v
```

Expected: 8 passed (5 select_response + 3 verbalize_prompt)

- [ ] **Step 6: Commit**

```bash
git add game-server/verbalize_sampling.py game-server/tests/test_verbalize_sampling.py
git commit -m "feat: add verbalize_prompt() wrapper"
```

---

### Task 4: VS variant for build_mission_prompts

**Files:**

- Modify: `game-server/prompts.py:975-1033`

**Interfaces:**

- Consumes: `verbalize_prompt`, `DIVERSITY_HINTS` from `verbalize_sampling`
- Modifies: `build_mission_prompts` signature: add `*, use_vs: bool = False, vs_k: int = 5`

- [ ] **Step 1: Add use_vs parameter and VS wrapping to build_mission_prompts**

In `game-server/prompts.py`, add import at top:

```python
from verbalize_sampling import DIVERSITY_HINTS, verbalize_prompt
```

Change `build_mission_prompts` signature (line 975):

```python
def build_mission_prompts(
    language: str,
    crew_desc: str,
    archetype: str | None = None,
    seeds: dict | None = None,
    *,
    use_vs: bool = False,
    vs_k: int = 5,
) -> tuple[str, str]:
```

At the end of the function, before `return system, user`, add:

```python
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["mission"], k=vs_k)
    return system, user
```

- [ ] **Step 2: Verify import and syntax**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -c "from prompts import build_mission_prompts; build_mission_prompts('en', 'crew: captain', use_vs=True); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add game-server/prompts.py
git commit -m "feat: add use_vs to build_mission_prompts"
```

---

### Task 5: VS variants for build_game_title_prompts, build_turn_story_prompts

**Files:**

- Modify: `game-server/prompts.py:582-639`

**Interfaces:**

- Modifies: both functions gain `*, use_vs: bool = False, vs_k: int = 5`

- [ ] **Step 1: Add use_vs to both functions**

`build_game_title_prompts` (line 582):

```python
def build_game_title_prompts(
    language: str,
    *,
    use_vs: bool = False,
    vs_k: int = 5,
) -> tuple[str, str]:
```

Add before `return system, user`:

```python
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["game_title"], k=vs_k)
```

`build_turn_story_prompts` (line 612):

```python
def build_turn_story_prompts(
    language: str,
    turn: int,
    previous_summary: str,
    player_role: str,
    *,
    use_vs: bool = False,
    vs_k: int = 5,
) -> tuple[str, str]:
```

Add before `return system, user`:

```python
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["turn_story"], k=vs_k)
```

- [ ] **Step 2: Verify**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -c "
from prompts import build_game_title_prompts, build_turn_story_prompts
s1, u1 = build_game_title_prompts('en', use_vs=True)
s2, u2 = build_turn_story_prompts('en', 1, 'prev', 'captain', use_vs=True)
print('OK')
"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add game-server/prompts.py
git commit -m "feat: add use_vs to build_game_title_prompts and build_turn_story_prompts"
```

---

### Task 6: VS variants for build_global_circumstances_prompts

**Files:**

- Modify: `game-server/prompts.py:908-969`

- [ ] **Step 1: Add use_vs parameter**

```python
def build_global_circumstances_prompts(
    language: str,
    turn: int,
    previous_summary: str,
    player_descriptions: str,
    mission_str: str,
    *,
    use_vs: bool = False,
    vs_k: int = 5,
) -> tuple[str, str]:
```

Add before `return system, user`:

```python
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["global_circumstances"], k=vs_k)
```

- [ ] **Step 2: Verify and commit**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -c "
from prompts import build_global_circumstances_prompts
s, u = build_global_circumstances_prompts('en', 1, '', 'crew', 'mission', use_vs=True)
print('OK')
"
```

```bash
git add game-server/prompts.py
git commit -m "feat: add use_vs to build_global_circumstances_prompts"
```

---

### Task 7: VS variants for build_combined_outcome_prompts, build_game_over_prompts

**Files:**

- Modify: `game-server/prompts.py:355-483`

- [ ] **Step 1: Add use_vs to both functions**

`build_combined_outcome_prompts`:

```python
def build_combined_outcome_prompts(
    language: str,
    *,
    setting: str,
    conflict: str,
    narrative: str,
    previous_summary: str,
    mission_text: str,
    decisions_text: str,
    roster_text: str,
    use_vs: bool = False,
    vs_k: int = 5,
) -> tuple[str, str]:
```

Add before `return system, user`:

```python
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["combined_outcome"], k=vs_k)
```

`build_game_over_prompts`:

```python
def build_game_over_prompts(
    language: str,
    *,
    outcome_type: str,
    outcome_narrative: str,
    mission_summary: str,
    use_vs: bool = False,
    vs_k: int = 5,
) -> tuple[str, str]:
```

Add before `return system, user`:

```python
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["combined_outcome"], k=vs_k)
```

- [ ] **Step 2: Verify and commit**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -c "
from prompts import build_combined_outcome_prompts, build_game_over_prompts
s1, u1 = build_combined_outcome_prompts('en', setting='s', conflict='c', narrative='n', previous_summary='', mission_text='m', decisions_text='d', roster_text='r', use_vs=True)
s2, u2 = build_game_over_prompts('en', outcome_type='success', outcome_narrative='n', mission_summary='m', use_vs=True)
print('OK')
"
```

```bash
git add game-server/prompts.py
git commit -m "feat: add use_vs to build_combined_outcome_prompts and build_game_over_prompts"
```

---

### Task 8: VS variants for build_player_message_prompts

**Files:**

- Modify: `game-server/prompts.py:665-789`

- [ ] **Step 1: Add use_vs parameter**

Change signature to add `*, use_vs: bool = False, vs_k: int = 5` before closing `)` and add VS wrapping before return.

- [ ] **Step 2: Verify and commit**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -c "
from prompts import build_player_message_prompts
s, u = build_player_message_prompts('en', 'Jim', 'captain', ['brave'], 'hello', use_vs=True)
print('OK')
"
```

```bash
git add game-server/prompts.py
git commit -m "feat: add use_vs to build_player_message_prompts"
```

---

### Task 9: VS variants for NPC decisions, auto choices, species, names

**Files:**

- Modify: `game-server/prompts.py:795-902, 1072-1119`

**Functions:** `build_npc_decision_prompts`, `build_auto_choice_prompts`, `build_species_description_prompts`, `build_npc_name_user`

- [ ] **Step 1: Add use_vs to all four functions**

Each function gets `*, use_vs: bool = False, vs_k: int = 5` and:

```python
    if use_vs:
        system, user = verbalize_prompt(system, user, DIVERSITY_HINTS["npc_decision"], k=vs_k)
```

(using appropriate hint key: `"npc_decision"`, `"npc_decision"` (auto_choice reuses), `"species_description"`, `"npc_name"`)

- [ ] **Step 2: Verify and commit**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -c "
from prompts import (
    build_npc_decision_prompts, build_auto_choice_prompts,
    build_species_description_prompts, build_npc_name_user
)
s1, u1 = build_npc_decision_prompts('en', 'Korax', 'scientist', 'curious', 'choices', use_vs=True)
s2, u2 = build_auto_choice_prompts('en', 'Jim', 'captain', ['brave'], 'species', 'briefing', 'gc', 'choices', use_vs=True)
s3, u3 = build_species_description_prompts('en', 'captain', 'Vulcan', None, False, 'male', None, False, use_vs=True)
s4, u4 = build_npc_name_user('en', 'Doctor', 'medical_officer', 'Betazoid', 'female', 'tall', ['empathetic'], set(), use_vs=True)
print('OK')
"
```

```bash
git add game-server/prompts.py
git commit -m "feat: add use_vs to NPC decision, auto choice, species, and name prompts"
```

---

### Task 10: VS integration — generate_mission, generate_game_title, generate_turn_story

**Files:**

- Modify: `game-server/game_server.py`

- [ ] **Step 1: Add import for VS module**

In `game-server/game_server.py` imports:

```python
from verbalize_sampling import VS_RESPONSE_SCHEMA, select_response
```

- [ ] **Step 2: Update generate_mission**

In `generate_mission` (line ~2490), change the prompt call to use VS:

```python
        system, user = build_mission_prompts(
            self.language, crew_desc,
            archetype=mission_seeds["archetype"],
            seeds=mission_seeds["seeds"],
            use_vs=True,
            vs_k=5,
        )

        try:
            vs_result = self._call_llm(
                system_prompt=system,
                user_prompt=user,
                response_schema=VS_RESPONSE_SCHEMA,
                max_tokens=8192,
                temperature=0.8,
            )
            chosen = select_response(vs_result["responses"], "full")
            result = json.loads(chosen["text"])
        except Exception as e:
            logger.error(f"[MISSION] Generation failed: {e}", exc_info=True)
            ...
```

- [ ] **Step 3: Update generate_game_title**

Wrap `build_game_title_prompts` call with `use_vs=True`, use `VS_RESPONSE_SCHEMA`, select and parse.

- [ ] **Step 4: Update generate_turn_story**

Wrap `build_turn_story_prompts` with `use_vs=True`, use `VS_RESPONSE_SCHEMA`, select and parse.

- [ ] **Step 5: Run existing tests to verify nothing breaks**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/ -v -k "not comfyui" 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add game-server/game_server.py
git commit -m "feat: integrate VS into mission, title, and turn story generation"
```

---

### Task 11: VS integration — generate_global_circumstances, generate_player_briefing_and_choices (outcome)

**Files:**

- Modify: `game-server/game_server.py`

- [ ] **Step 1: Update generate_global_circumstances**

Wrap `build_global_circumstances_prompts` with `use_vs=True`, VS schema, select.

- [ ] **Step 2: Update outcome generation in generate_player_briefing_and_choices**

Find the `build_combined_outcome_prompts` call, add `use_vs=True`, VS schema, select.

- [ ] **Step 3: Verify and commit**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/ -v -k "not comfyui" 2>&1 | tail -20
git add game-server/game_server.py
git commit -m "feat: integrate VS into circumstances and outcome generation"
```

---

### Task 12: VS integration — player messages and NPC decisions

**Files:**

- Modify: `game-server/game_server.py`

- [ ] **Step 1: Find and update player message generation**

Locate `build_player_message_prompts` call, add `use_vs=True`, VS schema, select. The `text` field is plain text (GM response), no JSON parsing needed.

- [ ] **Step 2: Update NPC decisions and auto choices**

Locate `build_npc_decision_prompts` and `build_auto_choice_prompts` calls, add `use_vs=True`, VS schema, select.

- [ ] **Step 3: Verify and commit**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/ -v -k "not comfyui" 2>&1 | tail -20
git add game-server/game_server.py
git commit -m "feat: integrate VS into player messages and NPC decisions"
```

---

### Task 13: VS integration — avatars and image prompts

**Files:**

- Modify: `game-server/game_server.py`

- [ ] **Step 1: Update generate_avatar_prompt**

Instead of calling `_call_llm` with `AVATAR_PROMPT_SCHEMA`, call with `VS_RESPONSE_SCHEMA` and select:

```python
        vs_system, vs_user = verbalize_prompt(system, user, DIVERSITY_HINTS["avatar"], k=5)

        parsed = self._call_llm(
            system_prompt=vs_system,
            user_prompt=vs_user,
            response_schema=VS_RESPONSE_SCHEMA,
            max_tokens=self.llm_max_avatar_tokens,
        )
        chosen = select_response(parsed["responses"], "full")
        avatar_prompt = json.loads(chosen["text"]).get("avatar_prompt", "")
```

- [ ] **Step 2: Update generate_npc_avatar_prompts**

Wrap the prompt with verbalize_prompt + DIVERSITY_HINTS["npc_avatars"], use VS schema, select per-NPC.

- [ ] **Step 3: Update generate_chosen_action_prompt**

Wrap with DIVERSITY_HINTS["action_prompt"], VS schema, select.

- [ ] **Step 4: Update generate_bridge_image_prompt**

Wrap with DIVERSITY_HINTS["bridge_image"], VS schema, select.

- [ ] **Step 5: Update scene_prompt inside generate_global_circumstances**

The scene_prompt is already generated as part of global_circumstances — it's a field in the combined response. With VS, each response["text"] contains the full circumstances JSON including scene_prompt. The scene_prompt thus gets selected implicitly via the circumstances selection.

- [ ] **Step 6: Verify and commit**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/ -v -k "not comfyui" 2>&1 | tail -20
git add game-server/game_server.py
git commit -m "feat: integrate VS into avatar and image prompt generation"
```

---

### Task 14: VS toggle and observability

**Files:**

- Modify: `game-server/game_server.py`

- [ ] **Step 1: Add VS toggle env var**

In `GameServer.__init__`:

```python
        self.vs_enabled = os.getenv("VS_ENABLED", "1") == "1"
        self.vs_k = int(os.getenv("VS_K", "5"))
        self.vs_mode = os.getenv("VS_MODE", "full")  # "full" or "tails"
```

- [ ] **Step 2: Pass toggle to call sites**

Each VS call site checks `self.vs_enabled` before using VS. When disabled, fall back to original behavior.

```python
        use_vs = self.vs_enabled
        system, user = build_mission_prompts(
            self.language, crew_desc,
            archetype=mission_seeds["archetype"],
            seeds=mission_seeds["seeds"],
            use_vs=use_vs,
            vs_k=self.vs_k,
        )
```

- [ ] **Step 3: Add logging of VS selections**

After each `select_response()` call, log the chosen option and the full distribution:

```python
        chosen = select_response(vs_result["responses"], self.vs_mode)
        logger.info(
            "[VS] Selected option %d/%d with probability %.3f",
            vs_result["responses"].index(chosen) + 1,
            len(vs_result["responses"]),
            chosen["probability"],
        )
        logger.debug("[VS] Full distribution: %s", vs_result["responses"])
```

- [ ] **Step 4: Verify toggle works**

```bash
cd game-server && VS_ENABLED=0 PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -c "
from game_server import GameServer
gs = GameServer('en', 'test_game')
print('VS enabled:', gs.vs_enabled)  # Should be False
"
```

- [ ] **Step 5: Commit**

```bash
git add game-server/game_server.py
git commit -m "feat: add VS toggle (VS_ENABLED env var) and selection logging"
```

---

### Task 15: Full test suite and cleanup

**Files:**

- Modify: `game-server/tests/test_verbalize_sampling.py`

- [ ] **Step 1: Run full test suite**

```bash
cd game-server && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/ -v -k "not comfyui" 2>&1
```

Expected: all tests pass (existing + new VS tests)

- [ ] **Step 2: Verify imports are at file top**

```bash
grep -n "^from\|^import" game-server/verbalize_sampling.py
```

All imports must be at top. No import inside function body.

- [ ] **Step 3: Verify no raw 'ru'/'en' strings in new code**

```bash
grep -n "'en'\|\"en\"\|'ru'\|\"ru\"" game-server/verbalize_sampling.py
```

Should find no matches (VS module is language-agnostic).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: final cleanup and test verification for VS"
```
