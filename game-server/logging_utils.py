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


def _ensure_logs_dir() -> str:
    """Return the logs directory path, creating it if necessary."""
    # Works both inside Docker (/app/logs/) and locally
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
    if os.getenv("DOCKER", ""):
        log_dir = "/app/logs"
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
        player_id: Player identifier (or "none")
        turn: Turn number (or "t0")
        kind: Call type descriptor (e.g., "player_briefing")
        log_type: "request" or "response"
        content: Full text content to write
    """
    log_dir = _ensure_logs_dir()
    safe_game = _sanitize_filename_component(game_id)
    safe_player = _sanitize_filename_component(player_id)
    safe_turn = _sanitize_filename_component(turn)
    safe_kind = _sanitize_filename_component(kind)
    safe_type = _sanitize_filename_component(log_type)
    filename = f"game_{safe_game}_player{safe_player}_turn{safe_turn}_{safe_kind}_llm_{safe_type}.log"
    filepath = os.path.join(log_dir, filename)
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
        player_id: Player identifier (or "none")
        turn: Turn number (or "t0")
        kind: Call type descriptor (e.g., "avatar", "scene")
        log_type: "request" or "response"
        content: Full text content to write
    """
    log_dir = _ensure_logs_dir()
    safe_game = _sanitize_filename_component(game_id)
    safe_player = _sanitize_filename_component(player_id)
    safe_turn = _sanitize_filename_component(turn)
    safe_kind = _sanitize_filename_component(kind)
    safe_type = _sanitize_filename_component(log_type)
    filename = f"game_{safe_game}_player{safe_player}_turn{safe_turn}_{safe_kind}_comfyui_{safe_type}.log"
    filepath = os.path.join(log_dir, filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.debug("Detailed ComfyUI %s written to %s", log_type, filepath)
    except OSError:
        logger.warning("Failed to write ComfyUI %s log to %s", log_type, filepath, exc_info=True)
