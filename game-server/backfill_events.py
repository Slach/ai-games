"""One-shot importer of legacy detailed log files into generation_events.

Parses the existing ``logs/game_*_player_*_turn*_*_{llm,comfyui}_{request,response}.log``
files (written by logging_utils before the structured mirror existed) and
inserts them as generation events, so old games appear in game-analyzer too.

Idempotent: files whose stem already has an event row are skipped.

Runnable directly for debugging:

    PYTHONDONTWRITEBYTECODE=1 python backfill_events.py [logs_dir]

or via the game-server admin endpoint POST /admin/import-log-events.
"""

import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from database import (
    generation_event_exists,
    generation_event_has_response,
    record_generation_request,
    record_generation_response,
)
from logging_utils import extract_event_meta, kind_to_stage

logger = logging.getLogger(__name__)

FILENAME_RE = re.compile(
    r"^game_(?P<game>.+)_player_(?P<player>.+)_turn(?P<turn>\d+)"
    r"_(?P<kind>.+)_(?P<backend>llm|comfyui)_(?P<log_type>request|response)\.log$"
)


def default_logs_dir() -> str:
    """Same resolution as logging_utils._ensure_logs_dir, without creating it."""
    if Path("/app/logs").is_dir():
        return "/app/logs"
    return str(Path(__file__).resolve().parent.parent / "logs")


def import_logs_dir(logs_dir: str) -> dict[str, int]:
    """Import every detailed log file from logs_dir into generation_events."""
    imported = 0
    skipped_existing = 0
    skipped_unparsed = 0
    files = sorted(Path(logs_dir).glob("game_*_player_*_turn*_*_*.log"))
    for path in files:
        match = FILENAME_RE.match(path.name)
        if match is None:
            skipped_unparsed += 1
            continue
        stem = path.name[: -len(f"_{match.group('log_type')}.log")]
        # Idempotency: a request file whose stem already exists means the
        # whole pair was imported (or recorded live) before; a response file
        # is only skipped when its response half is already filled in.
        if match.group("log_type") == "request":
            if generation_event_exists(stem):
                skipped_existing += 1
                continue
        elif generation_event_has_response(stem):
            skipped_existing += 1
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("Failed to read log file %s", path, exc_info=True)
            skipped_unparsed += 1
            continue
        meta = extract_event_meta(match.group("backend"), match.group("log_type"), content)
        ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        common = {
            "log_file": stem,
            "game_id": match.group("game"),
            "player_id": match.group("player"),
            "turn": int(match.group("turn")),
            "kind": match.group("kind"),
            "stage": kind_to_stage(match.group("kind")),
            "backend": match.group("backend"),
        }
        if match.group("log_type") == "request":
            record_generation_request(
                **common,
                content=content,
                model=meta.get("model", ""),
                params=meta.get("params", ""),
                ts=ts,
            )
        else:
            record_generation_response(
                **common,
                content=content,
                model=meta.get("model", ""),
                params=meta.get("params", ""),
                finish_reason=meta.get("finish_reason", ""),
                usage=meta.get("usage", ""),
                image_url=meta.get("image_url", ""),
                status=meta.get("status", "ok"),
                ts=ts,
            )
        imported += 1
    result = {
        "imported": imported,
        "skipped_existing": skipped_existing,
        "skipped_unparsed": skipped_unparsed,
        "total_files": len(files),
    }
    logger.info("[BACKFILL] %s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    target_dir = sys.argv[1] if len(sys.argv) > 1 else default_logs_dir()
    print(import_logs_dir(target_dir))
