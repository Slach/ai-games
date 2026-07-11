# Verbalized Sampling for AI Games — Design Spec

**Date:** 2026-07-11
**Paper:** Zhang, Yu, Chong, Sicilia, Tomz, Manning, Shi — *Verbalized Sampling: How to Mitigate Mode Collapse and Unlock LLM Diversity* (ICLR 2026)
**Status:** Approved (sections A/B/C)

---

## Problem

RLHF-aligned LLMs suffer from **mode collapse** — outputs become stereotypical and repetitive. The root cause is *typicality bias* in human preference data: annotators systematically prefer familiar, fluent, schema-consistent text. This bias gets amplified through alignment training, causing the model to collapse onto a narrow set of "safe" responses.

**Symptoms in AI Games:**

- Missions follow the same patterns, no drama or plot variation
- NPCs and Game Master responses feel flat and predictable
- Avatars default to humanoid despite non-humanoid onboarding choices
- Content/image prompts lack variety in composition and mood
- Turn outcomes don't create a coherent developing plot

---

## Solution: Verbalized Sampling (VS)

Instead of instance-level prompts ("Generate one mission"), use **distribution-level prompts**:

> "Generate \(k=5\) different missions with corresponding probabilities. Explore the full distribution — include likely, unlikely, and surprising options."

The model verbalizes its internal probability distribution over options. Selecting from this distribution recovers the diversity of the pre-trained base model, bypassing the typicality-induced mode collapse of aligned models.

**Key properties:**

- Training-free — pure prompting technique
- Model-agnostic — works with any LLM (no logit access needed)
- Single LLM call per generation point (more output tokens, same number of calls for basic VS)
- 1.6–2.1× diversity improvement in creative writing tasks (paper results)

---

## Architecture: `game-server/verbalize_sampling.py`

### Components

```
verbalize_sampling.py
├── VSConfig          # k (number of candidates), sampling_mode
├── DiversityHints    # Per-function hints for what to vary
├── verbalize_prompt() # Wraps instance-level prompt → distribution-level
├── select_response()  # Weighted random selection by verbalized probabilities
└── log_selection()    # Log chosen/total options for observability
```

### `VSConfig`

```python
@dataclass
class VSConfig:
    k: int = 5                    # Number of candidates per VS call
    sampling_mode: str = "full"   # "full" | "tails"
```

- `full`: sample from the full verbalized distribution
- `tails`: sample from low-probability options only (p < 0.10 per paper)

### `verbalize_prompt(system_prompt, user_prompt, diversity_hints)`

Wraps a standard instance-level prompt pair into a distribution-level VS prompt:

1. Prepends distribution-level framing to the system prompt
2. Replaces the instance request with a k-option request
3. Adds diversity hints guiding the model to explore different axes
4. Specifies output format: `<response>` tags with `<text>` and `<probability>`

### `select_response(parsed_responses, sampling_mode)`

Given parsed `<response>` entries with `text` and `probability`:

- Validates probabilities sum to ~1.0
- Normalizes if needed
- Performs weighted random selection
- Returns the chosen response text

---

## Individual VS Templates (Diversity Hints)

Each prompt function gets its own diversity hints — specific axes of variation the model should explore.

### 🔴 Anchor-level (one VS call, result cascades downstream)

| Function | k | Diversity Hints |
|----------|---|-----------------|
| `build_mission_prompts` | 5 | Genre (diplomacy/combat/mystery/exploration), tone (dark/heroic/absurd/tense), scale (personal drama → galactic threat) |
| `build_game_title_prompts` | 5 | Title style (metaphorical/technical/ironic/epic), length |

### 🟡 Cascade-level (conditioned on anchor + previous context)

| `build_global_circumstances_prompts` | 5 | Threat type (external/internal/natural/technogenic), scene mood, location variety |
| `build_turn_story_prompts` | 5 | Story direction (escalation/de-escalation/revelation/character moment), pacing |
| `build_combined_outcome_prompts` | 5 | Outcome (success/partial/complication/twist), consequences, tone shift |

### 🟢 Independent (with full game context)

| Function | k | Diversity Hints |
|----------|---|-----------------|
| `build_player_message_prompts` | 5 | GM tone (serious/ironic/mysterious/encouraging), response length. **Mood-aware:** tone should match current scene mood from circumstances |
| `build_npc_decision_prompts` | 5 | Decision style (rational/emotional/risky/cautious). **Mood-aware:** decisions should reflect current circumstances |
| `build_auto_choice_prompts` | 5 | Same as NPC decisions |
| `build_species_description_prompts` | 5 | Unusualness of appearance, textures, silhouette, body plan |
| `build_npc_name_user` | 5 | Name style (technical/poetic/alien/functional) |
| Avatars — `generate_avatar_prompt` | 5 | Body form (humanoid/alien/energy/cybernetic), angle, environment, mood. **CRITICAL:** for non-human species, explicitly require non-humanoid forms in at least 3 of 5 options |
| Avatars — `generate_npc_avatar_prompts` | 5 per NPC | Same as above + species-to-species visual diversity |
| Content — `generate_chosen_action_prompt` | 5 | Composition, lighting, camera angle, action dynamics |
| Content — `generate_bridge_image_prompt` | 5 | Crew arrangement, bridge lighting, viewport scene, overall mood |
| Content — scene_prompt (from circumstances) | 5 | Color palette, atmosphere, scene scale (intimate → epic) |

### Non-VS functions (unchanged)

- `build_onboarding_prompts` — structurally diverse by design
- `build_dynamic_sg_question_prompts` — structurally diverse by design
- `build_npc_dialogue_lang_note` — utility, not creative
- `build_content_prompt_note` — utility, not creative
- `build_personal_briefing_system` — utility, not creative

---

## Cascading Consistency

```
GAME CREATION:
  🔴 MISSION VS (k=5)  ──→ select 1
  🔴 TITLE VS  (k=5)   ──→ select 1
  🟢 BRIDGE IMG VS     ──→ select 1  (uses chosen mission)
  🟢 AVATAR VS per player/NPC  ──→ select 1 per character

TURN 1:
  🟡 CIRCUMSTANCES VS (k=5) ──→ select 1  (conditioned on: chosen mission + previous_summary)
  🟡 TURN STORY VS   (k=5) ──→ select 1  (conditioned on: chosen circumstances)
  🟡 OUTCOME VS      (k=5) ──→ select 1  (conditioned on: story + player decisions)

TURN N: same cascade, accumulated context grows

PER-INTERACTION:
  🟢 NPC DECISION VS   ──→ select 1  (context: mission + current circumstances)
  🟢 GM RESPONSE VS    ──→ select 1  (context: full game state)
  🟢 ACTION PROMPT VS  ──→ select 1  (context: current narrative)
```

**Context propagation:**

- Mission choice → all downstream functions receive `mission_name`, `mission_description`
- Circumstances choice → NPC decisions, GM responses, content prompts receive `setting`, `conflict`, `narrative`
- Previous turn summary → always passed forward

---

## Output Format

All VS prompts instruct the model to output in `<response>` / `<probability>` / `<text>` XML-style format (this is the **prompt-side** format the model reads). The actual API response is parsed via JSON schema:

```json
{
  "responses": [
    {"probability": 0.35, "text": "..."},
    {"probability": 0.25, "text": "..."}
  ]
}
```

The `response_schema` passed to `_call_llm` will specify this structure so parsing is deterministic, while the XML format in the prompt guides the model's generation.

## Implementation Scope

### Phase 1: Core VS Module

- [ ] `game-server/verbalize_sampling.py` — `VSConfig`, `verbalize_prompt()`, `select_response()`
- [ ] Update shared `DiversityHints` config

### Phase 2: Prompt Functions

- [ ] `build_mission_prompts` — VS variant + diversity hints
- [ ] `build_game_title_prompts` — VS variant
- [ ] `build_turn_story_prompts` — VS variant
- [ ] `build_global_circumstances_prompts` — VS variant
- [ ] `build_combined_outcome_prompts` — VS variant
- [ ] `build_player_message_prompts` — VS variant
- [ ] `build_npc_decision_prompts` — VS variant
- [ ] `build_auto_choice_prompts` — VS variant
- [ ] `build_species_description_prompts` — VS variant
- [ ] `build_npc_name_user` — VS variant

### Phase 3: Image/Avatar Functions (game_server.py)

- [ ] `generate_avatar_prompt` — VS integration
- [ ] `generate_npc_avatar_prompts` — VS integration
- [ ] `generate_chosen_action_prompt` — VS integration
- [ ] `generate_bridge_image_prompt` — VS integration
- [ ] scene_prompt generation (inside circumstances) — VS integration

### Phase 4: Wiring

- [ ] `game_server.py` — integration of `select_response()` at each call site
- [ ] Observability — log verbalized distributions and selections
- [ ] Per-function VS toggle (feature flag per game, or per env var)

---

## Non-Goals

- Does NOT change onboarding or dynamic question prompts (already structurally diverse)
- Does NOT modify the ComfyUI pipeline (only the prompts sent to it)
- Does NOT change the database schema
- Does NOT add training/fine-tuning — purely inference-time
