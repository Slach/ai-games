"""Configuration via environment variables for the game-analyzer backend."""

import os
from pathlib import Path

# Read-only mounts of the sibling services (docker-compose) with local-dev
# fallbacks pointing at the repo checkout.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

GAME_SERVER_DB = os.getenv(
    "GAME_SERVER_DB", str(REPO_ROOT / "game-server" / "game_server.db")
)
BOT_DB = os.getenv(
    "BOT_DB", str(REPO_ROOT / "telegram-bot" / "player_states.db")
)
COMFYUI_FILES = os.getenv(
    "COMFYUI_FILES", str(REPO_ROOT / "comfyui" / "files")
)
GAME_SERVER_URL = os.getenv("GAME_SERVER_URL", "http://game-server:8000")

# Built frontend (multi-stage Docker copies it to /srv/static).
_DEFAULT_STATIC = "/srv/static" if Path("/srv/static").is_dir() else str(
    Path(__file__).resolve().parent.parent / "frontend" / "dist"
)
STATIC_DIR = os.getenv("STATIC_DIR", _DEFAULT_STATIC)
