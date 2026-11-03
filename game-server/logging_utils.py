"""Shared logging utilities for detailed LLM/ComfyUI call logging.

Two sinks are written for every request/response:

1. A dedicated text file under logs/ (the original behaviour, unchanged) —
   the file is overwritten on retry, so one file = latest attempt.
2. A row in the ``generation_events`` SQLite table (structured mirror with
   timestamps, parsed metadata and FTS search), consumed read-only by the
   game-analyzer service.

Both sinks are keyed by the same filename stem, so a retry updates the same
event row and bumps ``attempts``.
"""

import logging
import os
import re

from database import record_generation_request, record_generation_response

logger = logging.getLogger(__name__)

# Only allow alphanumeric, underscore, hyphen, dot in filename components.
# Strips path separators and traversal sequences.
_FILENAME_SAFE_RE = re.compile(r"[^\w\-.]")

# Semantic pipeline stage for a log kind. Dynamic kinds (npc_name_{role},
# npc_avatar_{role}, npc_role_flavour_{role}) are matched by prefix below.
KIND_STAGES: dict[str, str] = {
    "mission": "game_concept",
    "game_title": "game_concept",
    "splash": "content_pool",
    "loading": "content_pool",
    "character_flavour": "onboarding",
    "avatar_prompt": "onboarding",
    "avatar": "onboarding",
    "onboarding_questions": "onboarding",
    "sg_question": "onboarding",
    "npc_role_flavour": "npc",
    "npc_name": "npc",
    "npc_avatar_prompts": "npc",
    "npc_avatar": "npc",
    "bridge_image_prompt": "game_start",
    "bridge": "game_start",
    "global_circumstances": "turn",
    "turn_background_prompt": "turn",
    "turn_background": "turn",
    "scene": "turn",
    "player_briefing": "turn",
    "character_scene": "turn",
    "crew_dialogue": "turn",
    "npc_choice": "player_action",
    "npc_action": "player_action",
    "player_action": "player_action",
    "player_auto_choice": "player_action",
    "player_message": "player_action",
    "combined_outcome": "outcome",
    "outcome_image_prompt": "outcome",
    "outcome": "outcome",
    "death_notice": "outcome",
    "player_death": "outcome",
    "game_over_outcome": "finale",
    "finale": "finale",
}


def kind_to_stage(kind: str) -> str:
    """Map a log kind to its pipeline stage (prefix fallback for dynamic kinds)."""
    if kind in KIND_STAGES:
        return KIND_STAGES[kind]
    for prefix, stage in KIND_STAGES.items():
        if kind.startswith(prefix + "_"):
            return stage
    return "other"


def _sanitize_filename_component(component: str) -> str:
    """Replace any character that could be used for path traversal."""
    return _FILENAME_SAFE_RE.sub("_", component)


def _build_log_stem(
    game_id: str,
    player_id: str,
    turn: str,
    kind: str,
    backend: str,
) -> str:
    """Build the filename stem shared by the request/response file pair.

    The player segment is ALWAYS present (even when empty → ``player_none``)
    so that concurrent calls for the same game/turn/kind but different players
    never collapse into one file. For NPC calls, callers pass the npc_key as
    ``player_id`` to disambiguate roles.
    """
    safe_game = _sanitize_filename_component(game_id)
    safe_player = _sanitize_filename_component(player_id) if player_id else "none"
    safe_turn = _sanitize_filename_component(turn)
    safe_kind = _sanitize_filename_component(kind)
    return f"game_{safe_game}_player_{safe_player}_turn{safe_turn}_{safe_kind}_{backend}"


def _build_log_filename(
    game_id: str,
    player_id: str,
    turn: str,
    kind: str,
    backend: str,
    log_type: str,
) -> str:
    """Build a unique, descriptive log filename."""
    stem = _build_log_stem(game_id, player_id, turn, kind, backend)
    safe_type = _sanitize_filename_component(log_type)
    return f"{stem}_{safe_type}.log"


def _ensure_logs_dir() -> str:
    """Return the logs directory path, creating it if necessary."""
    # In Docker, the host's ./logs/ is mounted at /app/logs/
    docker_logs = "/app/logs"
    if os.path.isdir(docker_logs):
        return docker_logs
    # Local development: logs/ is one level up from the game-server/ directory
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        logger.warning("Failed to create logs directory: %s", log_dir, exc_info=True)
    return log_dir


def _parse_header_map(content: str, max_lines: int = 8) -> dict[str, str]:
    """Parse the leading ``Key: value`` header lines of a log file body."""
    headers: dict[str, str] = {}
    for line in content.splitlines()[:max_lines]:
        stripped = line.strip()
        if not stripped or stripped.startswith("---") or stripped.startswith("==="):
            break
        if ": " in line:
            key, _, value = line.partition(": ")
            headers.setdefault(key, value)
    return headers


def extract_event_meta(backend: str, log_type: str, content: str) -> dict[str, str]:
    """Extract structured metadata from a rendered log body.

    Returns a dict with any of: model, params, finish_reason, usage,
    image_url, status. Used by the live recorder and by the file backfill.
    """
    headers = _parse_header_map(content)
    meta: dict[str, str] = {}
    if backend == "llm":
        meta["model"] = headers.get("Model", "")
        if log_type == "request":
            parts = []
            if "Temperature" in headers:
                parts.append(f"temp={headers['Temperature']}")
            if "Max tokens" in headers:
                parts.append(f"max_tokens={headers['Max tokens']}")
            if "Enable thinking" in headers:
                parts.append(f"thinking={headers['Enable thinking']}")
            if "Sampling" in headers:
                parts.append(f"sampling={headers['Sampling']}")
            meta["params"] = " ".join(parts)
        else:
            meta["finish_reason"] = headers.get("Finish reason", "")
            meta["usage"] = headers.get("Usage", "")
            meta["status"] = "fallback" if "=== FALLBACK ===" in content else "ok"
    else:
        meta["model"] = headers.get("Model", "")
        if log_type == "request":
            parts = []
            if "Size" in headers:
                parts.append(f"size={headers['Size']}")
            if "Attempt" in headers:
                parts.append(f"attempt={headers['Attempt']}")
            if "Filename prefix" in headers:
                parts.append(f"prefix={headers['Filename prefix']}")
            meta["params"] = " ".join(parts)
        else:
            meta["image_url"] = headers.get("URL", "")
            meta["status"] = "ok" if meta["image_url"] else "error"
    return meta


def _record_event(
    *,
    backend: str,
    stem: str,
    game_id: str,
    player_id: str,
    turn: str,
    kind: str,
    log_type: str,
    content: str,
) -> None:
    """Mirror a file log write into the generation_events table.

    DB problems must never break the game flow — same tolerance as the file
    write above.
    """
    try:
        turn_int = int(turn)
    except (TypeError, ValueError):
        turn_int = 0
    meta = extract_event_meta(backend, log_type, content)
    try:
        if log_type == "request":
            record_generation_request(
                log_file=stem,
                game_id=game_id,
                player_id=player_id,
                turn=turn_int,
                kind=kind,
                stage=kind_to_stage(kind),
                backend=backend,
                content=content,
                model=meta.get("model", ""),
                params=meta.get("params", ""),
            )
        else:
            record_generation_response(
                log_file=stem,
                game_id=game_id,
                player_id=player_id,
                turn=turn_int,
                kind=kind,
                stage=kind_to_stage(kind),
                backend=backend,
                content=content,
                model=meta.get("model", ""),
                params=meta.get("params", ""),
                finish_reason=meta.get("finish_reason", ""),
                usage=meta.get("usage", ""),
                image_url=meta.get("image_url", ""),
                status=meta.get("status", "ok"),
            )
    except Exception:
        logger.warning(
            "Failed to record generation event %s (%s/%s)",
            stem, backend, log_type, exc_info=True,
        )


def write_llm_log(
    *,
    game_id: str,
    player_id: str,
    turn: str,
    kind: str,
    log_type: str,
    content: str,
) -> None:
    """Write full LLM request or response to a dedicated log file.

    Args:
        game_id: Game identifier (or "none")
        player_id: Player identifier (or empty string if none)
        turn: Turn number (e.g. "1", "2")
        kind: Call type descriptor (e.g., "player_briefing")
        log_type: "request" or "response"
        content: Full text content to write
    """
    stem = _build_log_stem(game_id, player_id, turn, kind, "llm")
    filename = f"{stem}_{log_type}.log"
    filepath = os.path.join(_ensure_logs_dir(), filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.debug("Detailed LLM %s written to %s", log_type, filepath)
    except OSError:
        logger.warning("Failed to write LLM %s log to %s", log_type, filepath, exc_info=True)
    _record_event(
        backend="llm",
        stem=stem,
        game_id=game_id,
        player_id=player_id,
        turn=turn,
        kind=kind,
        log_type=log_type,
        content=content,
    )


def write_comfyui_log(
    *,
    game_id: str,
    player_id: str,
    turn: str,
    kind: str,
    log_type: str,
    content: str,
) -> None:
    """Write full ComfyUI request or response to a dedicated log file.

    Args:
        game_id: Game identifier (or "none")
        player_id: Player identifier (or empty string if none)
        turn: Turn number (e.g. "1", "2")
        kind: Call type descriptor (e.g., "avatar", "scene")
        log_type: "request" or "response"
        content: Full text content to write
    """
    stem = _build_log_stem(game_id, player_id, turn, kind, "comfyui")
    filename = f"{stem}_{log_type}.log"
    filepath = os.path.join(_ensure_logs_dir(), filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.debug("Detailed ComfyUI %s written to %s", log_type, filepath)
    except OSError:
        logger.warning("Failed to write ComfyUI %s log to %s", log_type, filepath, exc_info=True)
    _record_event(
        backend="comfyui",
        stem=stem,
        game_id=game_id,
        player_id=player_id,
        turn=turn,
        kind=kind,
        log_type=log_type,
        content=content,
    )
