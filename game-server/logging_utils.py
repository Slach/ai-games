"""Shared logging utilities for writing detailed LLM/ComfyUI logs to files.

Stores full request/response bodies as separate files under logs/,
keeping the main log stream concise and readable.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# Only allow alphanumeric, underscore, hyphen, dot in filename components.
# Strips path separators and traversal sequences.
_FILENAME_SAFE_RE = re.compile(r"[^\w\-.]")


def _sanitize_filename_component(component: str) -> str:
    """Replace any character that could be used for path traversal."""
    return _FILENAME_SAFE_RE.sub("_", component)


def _build_log_filename(
    game_id: str,
    player_id: str,
    turn: str,
    kind: str,
    backend: str,
    log_type: str,
) -> str:
    """Build a unique, descriptive log filename.

    The player segment is ALWAYS present (even when empty → ``player_none``)
    so that concurrent calls for the same game/turn/kind but different players
    never collapse into one file. For NPC calls, callers pass the npc_key as
    ``player_id`` to disambiguate roles.
    """
    safe_game = _sanitize_filename_component(game_id)
    safe_player = _sanitize_filename_component(player_id) if player_id else "none"
    safe_turn = _sanitize_filename_component(turn)
    safe_kind = _sanitize_filename_component(kind)
    safe_type = _sanitize_filename_component(log_type)
    return (
        f"game_{safe_game}_player_{safe_player}_turn{safe_turn}"
        f"_{safe_kind}_{backend}_{safe_type}.log"
    )


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
    filename = _build_log_filename(game_id, player_id, turn, kind, "llm", log_type)
    filepath = os.path.join(_ensure_logs_dir(), filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.debug("Detailed LLM %s written to %s", log_type, filepath)
    except OSError:
        logger.warning("Failed to write LLM %s log to %s", log_type, filepath, exc_info=True)


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
    filename = _build_log_filename(game_id, player_id, turn, kind, "comfyui", log_type)
    filepath = os.path.join(_ensure_logs_dir(), filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.debug("Detailed ComfyUI %s written to %s", log_type, filepath)
    except OSError:
        logger.warning("Failed to write ComfyUI %s log to %s", log_type, filepath, exc_info=True)
