"""Centralized LLM parameters: ``MODEL_NAME -> use case -> params``.

The active model comes from the ``LLM_MODEL`` env var. Each use case (the
``kind`` value logged by ``_call_llm``) resolves its parameters through this
table, so different models can be tuned independently for the same use case.

Resolution order in :func:`resolve_llm_params`:

1. ``MODEL_USE_CASES[model][use_case]`` — a per-model override.
2. ``DEFAULT_USE_CASES[use_case]`` — the baseline shared by all models.
3. ``MODEL_SAMPLING_MODES[model][enable_thinking]`` — vendor-recommended
   sampling overrides applied last (see that table's comment).

A model key need only list the use cases it wants to diverge on; everything
else falls back to :data:`DEFAULT_USE_CASES`. Each use case entry has two
branches — ``vs`` (Verbalized Sampling, larger token budget) and ``non_vs``
(plain single-shot) — selected by the caller's VS flag.

``max_tokens`` is either a literal int or one of the ``MAX_TOKENS_*`` sentinels,
resolved against ``GameServer`` instance attributes at call time
(:func:`resolve_max_tokens`). This keeps the config a plain static table while
still honoring the ``LLM_MAX_TOKENS`` / ``LLM_MAX_AVATAR_TOKENS`` env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class LLMParams:
    temperature: float
    max_tokens: int | str
    enable_thinking: bool = False
    # Optional llama.cpp sampling extensions. None = not sent, server defaults apply.
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None


# Sentinel values for max_tokens that resolve against GameServer instance attrs.
MAX_TOKENS_DEFAULT = "llm_max_tokens"
MAX_TOKENS_AVATAR = "llm_max_avatar_tokens"

# Active model, read once at import. Call sites pass ``self.llm_model`` instead,
# but exposing it here lets config readers / tests reference the resolved value.
DEFAULT_LLM_MODEL = os.getenv("LLM_MODEL", "unsloth/Qwen3.5-27B")


def _use_case(vs: LLMParams, non_vs: LLMParams) -> dict[str, LLMParams]:
    return {"vs": vs, "non_vs": non_vs}


# Baseline use-case parameters shared by every model. ``vs`` is the
# Verbalized-Sampling branch; ``non_vs`` is the single-shot branch. When a use
# case has no VS branch, both entries are identical and only ``non_vs`` is used.
DEFAULT_USE_CASES: dict[str, dict[str, LLMParams]] = {
    "onboarding_questions": _use_case(
        LLMParams(temperature=0.9, max_tokens=MAX_TOKENS_DEFAULT),
        LLMParams(temperature=0.7, max_tokens=MAX_TOKENS_DEFAULT),
    ),
    "game_title": _use_case(
        LLMParams(temperature=0.9, max_tokens=MAX_TOKENS_DEFAULT),
        LLMParams(temperature=0.9, max_tokens=MAX_TOKENS_DEFAULT),
    ),
    "turn_story": _use_case(
        LLMParams(temperature=0.7, max_tokens=8192),
        LLMParams(temperature=0.7, max_tokens=4096),
    ),
    "crew_dialogue": _use_case(
        LLMParams(temperature=0.8, max_tokens=256),
        LLMParams(temperature=0.8, max_tokens=256),
    ),
    "crew_scene_dialogue": _use_case(
        LLMParams(temperature=0.9, max_tokens=1024),
        LLMParams(temperature=0.9, max_tokens=1024),
    ),
    "content_prompts": _use_case(
        LLMParams(temperature=0.7, max_tokens=2048),
        LLMParams(temperature=0.7, max_tokens=2048),
    ),
    "player_message": _use_case(
        LLMParams(temperature=0.7, max_tokens=2048),
        LLMParams(temperature=0.7, max_tokens=1024),
    ),
    "avatar_prompt": _use_case(
        LLMParams(temperature=0.7, max_tokens=MAX_TOKENS_AVATAR),
        LLMParams(temperature=0.7, max_tokens=MAX_TOKENS_AVATAR),
    ),
    "chosen_action_prompt": _use_case(
        LLMParams(temperature=0.7, max_tokens=MAX_TOKENS_AVATAR),
        LLMParams(temperature=0.7, max_tokens=MAX_TOKENS_AVATAR),
    ),
    "sg_question": _use_case(
        LLMParams(temperature=0.9, max_tokens=1024),
        LLMParams(temperature=0.9, max_tokens=1024),
    ),
    "species_description": _use_case(
        LLMParams(temperature=0.8, max_tokens=2048),
        LLMParams(temperature=0.8, max_tokens=1024),
    ),
    "role_flavour": _use_case(
        LLMParams(temperature=0.8, max_tokens=2048),
        LLMParams(temperature=0.8, max_tokens=1024),
    ),
    "species_option_prompts": _use_case(
        LLMParams(temperature=0.8, max_tokens=1024),
        LLMParams(temperature=0.8, max_tokens=1024),
    ),
    "npc_choice": _use_case(
        LLMParams(temperature=0.8, max_tokens=2048),
        LLMParams(temperature=0.8, max_tokens=512),
    ),
    "player_auto_choice": _use_case(
        LLMParams(temperature=0.8, max_tokens=2048),
        LLMParams(temperature=0.8, max_tokens=512),
    ),
    "global_circumstances": _use_case(
        LLMParams(temperature=0.7, max_tokens=8192),
        LLMParams(temperature=0.7, max_tokens=4096),
    ),
    "player_briefing": _use_case(
        LLMParams(temperature=0.7, max_tokens=4096),
        LLMParams(temperature=0.7, max_tokens=4096),
    ),
    "combined_outcome": _use_case(
        LLMParams(temperature=0.7, max_tokens=262144, enable_thinking=True),
        LLMParams(temperature=0.7, max_tokens=262144, enable_thinking=True),
    ),
    "game_over_outcome": _use_case(
        LLMParams(temperature=0.7, max_tokens=8192, enable_thinking=True),
        LLMParams(temperature=0.7, max_tokens=8192, enable_thinking=True),
    ),
    "mission": _use_case(
        LLMParams(temperature=0.8, max_tokens=8192),
        LLMParams(temperature=0.8, max_tokens=4096),
    ),
    "bridge_image_prompt": _use_case(
        LLMParams(temperature=0.8, max_tokens=8192),
        LLMParams(temperature=0.8, max_tokens=4096),
    ),
    "background_prompts": _use_case(
        LLMParams(temperature=0.8, max_tokens=8192),
        LLMParams(temperature=0.8, max_tokens=8192),
    ),
    "scene_instruction": _use_case(
        LLMParams(temperature=0.7, max_tokens=1024),
        LLMParams(temperature=0.7, max_tokens=1024),
    ),
    "death_notice": _use_case(
        LLMParams(temperature=0.8, max_tokens=512),
        LLMParams(temperature=0.8, max_tokens=512),
    ),
    "npc_name": _use_case(
        LLMParams(temperature=0.95, max_tokens=256),
        LLMParams(temperature=0.95, max_tokens=256),
    ),
    "npc_avatar_prompts": _use_case(
        LLMParams(temperature=0.9, max_tokens=8192),
        LLMParams(temperature=0.9, max_tokens=4096),
    ),
    "player_message_text": _use_case(
        LLMParams(temperature=0.7, max_tokens=1024),
        LLMParams(temperature=0.7, max_tokens=1024),
    ),
}

# Per-model overrides. Keys are model names (matching the LLM_MODEL env value).
# Each value is a use-case table; use cases not listed fall back to
# DEFAULT_USE_CASES. Add a model key here only with the use cases it diverges on.
MODEL_USE_CASES: dict[str, dict[str, dict[str, LLMParams]]] = {
    "unsloth/Qwen3.6-35B-MTP": {
        # Example: this model is more creative — lower temperature for narrative.
        # Add real overrides here as the model is tuned.
    },
    # Muse-Glimmer emits reasoning (<think>) baked into its jinja chat template,
    # which llama.cpp honors regardless of the server-side --reasoning flag.
    # Reasoning consumes ~500-700 completion tokens before the model starts the
    # actual JSON answer, so short use cases must carry enough headroom for the
    # reasoning AND the structured payload, or they hit finish_reason=length and
    # fall back to a second plain-text request every time.
    "unsloth/Muse-Glimmer-30B": {
        "npc_name": _use_case(
            LLMParams(temperature=0.95, max_tokens=1024),
            LLMParams(temperature=0.95, max_tokens=1024),
        ),
        "crew_dialogue": _use_case(
            LLMParams(temperature=0.8, max_tokens=1024),
            LLMParams(temperature=0.8, max_tokens=1024),
        ),
        "death_notice": _use_case(
            LLMParams(temperature=0.8, max_tokens=1024),
            LLMParams(temperature=0.8, max_tokens=1024),
        ),
        "scene_instruction": _use_case(
            LLMParams(temperature=0.7, max_tokens=1536),
            LLMParams(temperature=0.7, max_tokens=1536),
        ),
        "player_message_text": _use_case(
            LLMParams(temperature=0.7, max_tokens=1536),
            LLMParams(temperature=0.7, max_tokens=1536),
        ),
        "species_option_prompts": _use_case(
            LLMParams(temperature=0.8, max_tokens=1536),
            LLMParams(temperature=0.8, max_tokens=1536),
        ),
        "sg_question": _use_case(
            LLMParams(temperature=0.9, max_tokens=1536),
            LLMParams(temperature=0.9, max_tokens=1536),
        ),
        "role_flavour": _use_case(
            LLMParams(temperature=0.8, max_tokens=3072),
            LLMParams(temperature=0.8, max_tokens=2048),
        ),
    },
}

# Vendor-recommended sampling settings keyed by the model's thinking mode
# (the ``enable_thinking`` value the use case resolved to). Applied on top of
# the per-use-case params in :func:`resolve_llm_params`: the matching mode
# entry overrides the sampling fields — temperature included — while
# ``max_tokens`` and ``enable_thinking`` keep their use-case values. Models
# absent here send only temperature and rely on server-side defaults.
MODEL_SAMPLING_MODES: dict[str, dict[bool, dict[str, float | int]]] = {
    "unsloth/Qwen3.8-27B-MTP": {
        # Thinking mode
        True: {
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 0.0,
            "repetition_penalty": 1.0,
        },
        # Instruct (non-thinking) mode
        False: {
            "temperature": 0.7,
            "top_p": 0.80,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repetition_penalty": 1.0,
        },
    },
}


def resolve_llm_params(model: str, use_case: str, vs_enabled: bool) -> LLMParams:
    """Return the LLMParams for ``model`` + ``use_case``, selecting vs/non_vs.

    Looks up ``MODEL_USE_CASES[model][use_case]`` first, then falls back to
    ``DEFAULT_USE_CASES[use_case]``. Raises KeyError for a use case unknown to
    both — every call site must map to a configured entry. Finally, if the
    model has an entry in :data:`MODEL_SAMPLING_MODES`, the branch matching
    the resolved ``enable_thinking`` overrides the sampling fields.
    """
    table = MODEL_USE_CASES.get(model, {})
    branches = table.get(use_case) or DEFAULT_USE_CASES[use_case]
    params = branches["vs" if vs_enabled else "non_vs"]
    mode_overrides = MODEL_SAMPLING_MODES.get(model, {}).get(params.enable_thinking)
    if mode_overrides:
        params = replace(params, **mode_overrides)
    return params


def resolve_max_tokens(value: int | str, instance: object) -> int:
    """Resolve a max_tokens config value against a GameServer instance.

    Literal ints pass through unchanged. Sentinel strings read the matching
    instance attribute (set from env vars in ``GameServer.__init__``).
    """
    if isinstance(value, int):
        return value
    return int(getattr(instance, value))
