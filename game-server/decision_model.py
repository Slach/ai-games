"""llama.cpp /v1/systemone decision-model client for NPC action choices.

Decision models (ggml-org/lev et al.) return calibrated probabilities per
option in a single forward pass — no text generation. The NPC persona and
loyalty rule go into the `state` prose block; the offered actions are the
`criteria`. Sampling from the probabilities keeps per-turn variety while the
same state always yields the same distribution.

On any failure (HTTP error, model unavailable, malformed probabilities) the
callers fall back to the generative LLM path.
"""

import json
import logging
import random

import httpx

from game_rules import loyalty_band
from logging_utils import write_llm_log
from prompts import _NPC_LOYALTY_RULES_EN

logger = logging.getLogger(__name__)

DECISION_INSTRUCTIONS = (
    "Which action would this crew member choose based on their "
    "personality, role, and loyalty? They see only the action "
    "descriptions, no consequences."
)


def build_decision_payload(
    model: str,
    npc_name: str,
    npc_role: str,
    traits_str: str,
    loyalty: int,
    choices: list[dict],
    dialogue_context: str | None = None,
) -> dict:
    """Build the /v1/systemone request for one NPC choice.

    `choices` must already be consequence-stripped (id + text only) — the
    NPC never sees the hidden consequences.
    """
    band = loyalty_band(loyalty)
    state = (
        f"Crew member: {npc_name}, role: {npc_role}. "
        f"Personality: {traits_str}. "
        f"Loyalty to command: {loyalty}/100 — {_NPC_LOYALTY_RULES_EN[band]}. "
        "The current situation on the ship requires a decision."
    )
    if dialogue_context:
        state += "\n\n" + dialogue_context
    return {
        "model": model,
        "state": state,
        "questions": {
            "action": {
                "type": "choice",
                "instructions": DECISION_INSTRUCTIONS,
                "criteria": {c["id"]: c["text"] for c in choices},
            }
        },
    }


async def choose_action(
    *,
    url: str,
    model: str,
    npc_name: str,
    npc_role: str,
    traits_str: str,
    loyalty: int,
    choices: list[dict],
    dialogue_context: str | None = None,
    game_id: str | None,
    player_id: str | None,
    turn: int | str | None,
    kind: str,
    timeout_s: float = 120.0,
) -> str | None:
    """Ask the decision model which action this NPC takes.

    Returns a sampled action_id from the model's probability distribution,
    or None when the decision model is unavailable or its answer cannot be
    trusted — the caller then falls back to the generative LLM path.

    Full request/response payloads go to the dedicated logs/ files via
    write_llm_log; the main log only carries compact one-liners.
    """
    payload = build_decision_payload(
        model, npc_name, npc_role, traits_str, loyalty, choices, dialogue_context
    )
    request_log = json.dumps(payload, ensure_ascii=False, indent=2)
    ctx_game = game_id or "none"
    ctx_player = str(player_id) if player_id else ""
    ctx_turn = str(turn) if turn is not None else "0"

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(
            f"[DECISION] systemone call failed for {npc_name}: {e}", exc_info=True
        )
        write_llm_log(
            game_id=ctx_game, player_id=ctx_player, turn=ctx_turn, kind=kind,
            log_type="request", content=request_log,
        )
        write_llm_log(
            game_id=ctx_game, player_id=ctx_player, turn=ctx_turn, kind=kind,
            log_type="response", content=f"ERROR: {e}",
        )
        return None

    write_llm_log(
        game_id=ctx_game, player_id=ctx_player, turn=ctx_turn, kind=kind,
        log_type="request", content=request_log,
    )
    write_llm_log(
        game_id=ctx_game, player_id=ctx_player, turn=ctx_turn, kind=kind,
        log_type="response", content=json.dumps(body, ensure_ascii=False, indent=2),
    )

    try:
        answer = body["answers"]["action"]
        probabilities = answer["probabilities"]
    except (KeyError, TypeError) as e:
        logger.warning(f"[DECISION] malformed systemone answer for {npc_name}: {e}")
        return None
    if not isinstance(probabilities, dict):
        logger.warning(f"[DECISION] probabilities is not an object for {npc_name}")
        return None

    weights = [float(probabilities.get(c["id"], 0.0)) for c in choices]
    if not any(w > 0 for w in weights):
        logger.warning(
            f"[DECISION] probabilities cover none of the offered actions for {npc_name}: "
            f"{sorted(probabilities)[:5]}"
        )
        return None

    chosen = random.choices([c["id"] for c in choices], weights=weights, k=1)[0]
    confidence = answer.get("confidence")
    logger.info(
        f"[DECISION] {npc_name} -> '{chosen}' "
        f"(p={probabilities.get(chosen, 0.0):.2f}, confidence={confidence})"
    )
    return chosen
