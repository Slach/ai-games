"""Node detail payload for the analyzer drawer.

For a graph node this assembles:
- chain: the full generation call chain (upstream LLM prompt calls →
  ComfyUI instruction/workflow → response), with every section of the
  original request/response texts and reference images resolved to
  /api/image URLs;
- player_view: the image + text as the player saw them (from DB rows);
- delivery: actual push_queue rows (what was sent, retries, failures);
- upstream: related graph nodes for provenance jumps.
"""

import json
import logging
import re

import db
import graph_builder
from image_utils import image_endpoint_path

logger = logging.getLogger(__name__)

_SECTION_HEADER_RE = re.compile(r"^--- (.+?) ---$")
_PATH_LINE_RE = re.compile(r"^[\w\-.]+/[\w\-.]+\.png$", re.MULTILINE)

_TEXT_OUTCOME_FIELDS = (
    "outcome_narrative", "ship_status_change", "crew_morale_change",
    "next_turn_hook",
)


def _j(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _parse_sections(text: str | None) -> list[dict]:
    """Split a rendered log body into header lines + named sections."""
    if not text:
        return []
    sections: list[dict] = []
    current: dict | None = None
    header_lines: list[str] = []
    for line in text.splitlines():
        match = _SECTION_HEADER_RE.match(line)
        if match:
            if current:
                sections.append(current)
            current = {"name": match.group(1), "lines": []}
        elif current is not None:
            current["lines"].append(line)
        else:
            header_lines.append(line)
    if current:
        sections.append(current)
    result = []
    if header_lines:
        result.append({"name": "meta", "text": "\n".join(header_lines).strip()})
    for section in sections:
        body = "\n".join(section["lines"]).strip("\n")
        entry = {"name": section["name"], "text": body}
        if section["name"] in ("CHARACTER AVATAR", "BACKGROUND"):
            entry["images"] = [
                image_endpoint_path(path) for path in _PATH_LINE_RE.findall(body)
            ]
        result.append(entry)
    return result


def _duration_ms(request_at: str | None, response_at: str | None) -> int | None:
    from datetime import datetime

    def parse(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    start, end = parse(request_at), parse(response_at)
    if start is None or end is None:
        return None
    return int((end - start).total_seconds() * 1000)


def _event_steps(event_ids: list[int]) -> list[dict]:
    if not event_ids:
        return []
    placeholders = ",".join("?" * len(event_ids))
    events = db.query_game_server(
        f"SELECT * FROM generation_events WHERE id IN ({placeholders})",
        tuple(event_ids),
    )
    # Backfilled rows get ids from an alphabetical file walk, so chronological
    # order is the timestamps, not the id.
    events.sort(key=lambda e: (e["request_at"] or e["response_at"] or "", e["id"]))
    steps = []
    for event in events:
        steps.append({
            "event_id": event["id"],
            "backend": event["backend"],
            "kind": event["kind"],
            "stage": event["stage"],
            "model": event["model"],
            "params": event["params"],
            "status": event["status"],
            "attempts": event["attempts"],
            "request_at": event["request_at"],
            "response_at": event["response_at"],
            "duration_ms": _duration_ms(event["request_at"], event["response_at"]),
            "image_url": image_endpoint_path(
                _comfyui_path(event["image_url"])
            ),
            "request_sections": _parse_sections(event["request_text"]),
            "response_sections": _parse_sections(event["response_text"]),
            "usage": event["usage"],
            "finish_reason": event["finish_reason"],
        })
    return steps


def _comfyui_path(url: str | None) -> str | None:
    from image_utils import comfyui_url_to_path

    return comfyui_url_to_path(url)


def _player_view(node: dict, gid: str) -> dict | None:
    """Image + text roughly as the player received them (from DB state)."""
    ntype = node["type"]
    meta = node["meta"]
    turn = meta.get("turn")
    if ntype == "brief":
        rows = db.query_game_server(
            "SELECT * FROM player_briefings WHERE id = ?", (meta["briefing_row_id"],)
        )
        if not rows:
            return None
        brief = rows[0]
        choices = _j(brief["choices"], [])
        char_images = db.query_game_server(
            "SELECT image_url FROM game_images WHERE game_id = ? AND turn = ? AND type = 'character' ORDER BY id",
            (gid, turn),
        )
        image = None
        part_digit = str(meta.get("player_id") or "")
        for row in char_images:
            path = _comfyui_path(row["image_url"]) or ""
            if part_digit and part_digit in path:
                image = image_endpoint_path(path)
                break
        return {
            "title": brief["personal_title"] or "",
            "text": brief["briefing"] or "",
            "image": image,
            "choices": [
                {"id": c.get("id"), "text": c.get("text", "")} for c in choices
            ],
        }
    if ntype == "choice":
        rows = db.query_game_server(
            "SELECT * FROM player_briefings WHERE id = ?", (meta["briefing_row_id"],)
        )
        if not rows:
            return None
        brief = rows[0]
        choices = _j(brief["choices"], [])
        chosen = next(
            (c for c in choices if c.get("id") == brief["selected_action_id"]), None
        )
        return {
            "action_id": brief["selected_action_id"],
            "text": (chosen or {}).get("text", ""),
            "consequence": (chosen or {}).get("consequence", ""),
            "consequence_kind": (chosen or {}).get("consequence_kind", ""),
        }
    if ntype == "actionimg":
        rows = db.query_game_server(
            "SELECT chosen_action_url FROM player_briefings WHERE id = ?",
            (meta["briefing_row_id"],),
        )
        url = rows[0]["chosen_action_url"] if rows else None
        return {"image": image_endpoint_path(_comfyui_path(url)), "text": ""}
    if ntype == "outcome":
        rows = db.query_game_server(
            "SELECT combined_outcome FROM game_turns WHERE game_id = ? AND turn = ?",
            (gid, turn),
        )
        outcome = _j(rows[0]["combined_outcome"], {}) if rows else {}
        image_rows = db.query_game_server(
            "SELECT image_url FROM game_images WHERE game_id = ? AND turn = ? AND type = 'outcome'",
            (gid, turn),
        )
        return {
            "text": "\n\n".join(
                outcome.get(field, "") for field in _TEXT_OUTCOME_FIELDS if outcome.get(field)
            ),
            "image": image_endpoint_path(_comfyui_path(image_rows[0]["image_url"])) if image_rows else None,
            "outcome_type": outcome.get("outcome_type", ""),
        }
    if ntype == "scene":
        rows = db.query_game_server(
            "SELECT global_circumstances, story FROM game_turns WHERE game_id = ? AND turn = ?",
            (gid, turn),
        )
        row = rows[0] if rows else {}
        gc = _j(row.get("global_circumstances"), {})
        image_rows = db.query_game_server(
            "SELECT image_url FROM game_images WHERE game_id = ? AND turn = ? AND type = 'scene'",
            (gid, turn),
        )
        return {
            "text": gc.get("narrative", ""),
            "setting": gc.get("setting", ""),
            "conflict": gc.get("conflict", ""),
            "key_events": gc.get("key_events", []),
            "image": image_endpoint_path(_comfyui_path(image_rows[0]["image_url"])) if image_rows else None,
        }
    if ntype == "dialogue":
        rows = db.query_game_server(
            "SELECT crew_dialogues FROM game_turns WHERE game_id = ? AND turn = ?",
            (gid, turn),
        )
        dialogues = _j(rows[0]["crew_dialogues"], []) if rows else []
        return {"dialogues": dialogues}
    if ntype == "player":
        rows = db.query_game_server(
            "SELECT * FROM player_profiles WHERE player_id = ?",
            (meta["player_id"],),
        )
        if not rows:
            return None
        profile = rows[0]
        return {
            "image": image_endpoint_path(_comfyui_path(profile["avatar_url"])),
            "text": "\n".join(filter(None, [
                profile["role_description"],
                profile["species_description"],
            ])),
        }
    if ntype == "npc":
        rows = db.query_game_server(
            "SELECT * FROM npc_profiles WHERE npc_key = ?", (meta["npc_key"],)
        )
        if not rows:
            return None
        npc = rows[0]
        avatar_url = None
        if npc["avatar_description"].startswith("avatar_url="):
            avatar_url = npc["avatar_description"][len("avatar_url="):].split(";", 1)[0]
        return {
            "image": image_endpoint_path(_comfyui_path(avatar_url)),
            "text": "\n".join(filter(None, [npc["role_description"], npc["personality_traits"]])),
        }
    if ntype == "finale":
        rows = db.query_game_server(
            "SELECT finale_narrative, finale_image_url, finale_outcome_type FROM game_state WHERE game_id = ?",
            (gid,),
        )
        row = rows[0] if rows else {}
        return {
            "text": row.get("finale_narrative", ""),
            "image": image_endpoint_path(_comfyui_path(row.get("finale_image_url"))),
            "outcome_type": row.get("finale_outcome_type", ""),
        }
    if ntype == "concept":
        rows = db.query_game_server(
            "SELECT * FROM game_missions WHERE game_id = ?", (gid,)
        )
        if not rows:
            return None
        mission = rows[0]
        return {
            "text": mission["description"],
            "objectives": _j(mission["objectives"], []),
            "name": mission["name"],
        }
    return None


_PUSH_MAP = {
    "brief": ("briefing", "player_id", "turn"),
    "actionimg": ("action", "player_id", "turn"),
    "outcome": ("outcome", None, "turn"),
    "player": ("onboarding", "player_id", None),
    "death": ("player_death", "player_id", "turn"),
    "finale": ("game_over", None, None),
}


def _delivery(node: dict, gid: str) -> list[dict]:
    meta = node["meta"]
    mapping = _PUSH_MAP.get(node["type"])
    push_types = []
    args: list = []
    if mapping:
        push_type, player_field, turn_field = mapping
        push_types = [push_type]
        if node["type"] == "finale":
            push_types = ["game_over", "game_summary"]
        sql = "SELECT * FROM push_queue WHERE game_id = ? AND push_type IN ({})".format(
            ",".join("?" * len(push_types))
        )
        args = [gid, *push_types]
        if player_field and meta.get(player_field) is not None:
            sql += " AND player_id = ?"
            args.append(meta[player_field])
        if turn_field and meta.get(turn_field) is not None:
            sql += " AND turn = ?"
            args.append(meta[turn_field])
        sql += " ORDER BY id"
        rows = db.query_bot(sql, tuple(args))
    else:
        rows = []
    result = []
    for row in rows:
        result.append({
            "push_type": row["push_type"],
            "status": row["status"],
            "retry_count": row["retry_count"],
            "error": row["error"],
            "turn": row["turn"],
            "created_at": row["created_at"],
            "sent_at": row["sent_at"],
            "payload": _j(row["payload"], {}),
        })
    return result


def _upstream(node: dict, graph: dict) -> list[dict]:
    gid = graph["game_id"]
    meta = node["meta"]
    turn = meta.get("turn")
    node_ids: list[tuple[str, str]] = []
    if node["type"] == "brief":
        node_ids = [
            (f"scene:{gid}:{turn}", "scene input"),
            (f"outcome:{gid}:{turn - 1}", "previous outcome") if turn and turn > 1 else None,
        ]
        if meta.get("player_id") is not None:
            node_ids.append((f"player:{gid}:{meta['player_id']}", "character"))
        if meta.get("npc_key"):
            role_key = meta["npc_key"].replace(f"npc_", "").replace(f"_{gid}", "")
            node_ids.append((f"npc:{gid}:{role_key}", "character"))
    elif node["type"] == "choice":
        node_ids = [(node["parent"], "briefing")]
    elif node["type"] == "actionimg":
        node_ids = [
            (node["parent"], "choice"),
            (f"scene:{gid}:{turn}", "turn background"),
        ]
        if meta.get("player_id") is not None:
            node_ids.append((f"player:{gid}:{meta['player_id']}", "avatar reference"))
    elif node["type"] == "outcome":
        node_ids = [
            (n["id"], "input action") for n in graph["nodes"]
            if n["type"] == "choice" and n["meta"].get("turn") == turn
        ]
    elif node["type"] == "death":
        node_ids = [(f"outcome:{gid}:{turn}", "outcome")]
    elif node["type"] == "finale":
        last_turn = max(
            (n["meta"]["turn"] for n in graph["nodes"] if n["type"] == "outcome"),
            default=None,
        )
        if last_turn:
            node_ids = [(f"outcome:{gid}:{last_turn}", "final outcome")]
    elif node["type"] == "scene" and turn and turn > 1:
        node_ids = [(f"outcome:{gid}:{turn - 1}", "previous outcome")]
    elif node["type"] == "dialogue":
        node_ids = [
            (n["id"], "briefing") for n in graph["nodes"]
            if n["type"] == "brief" and n["meta"].get("turn") == turn
        ]
    existing = {n["id"]: n for n in graph["nodes"]}
    result = []
    for item in node_ids:
        if item and item[0] in existing:
            result.append({
                "node_id": item[0],
                "label": existing[item[0]]["label"],
                "type": existing[item[0]]["type"],
                "relation": item[1],
            })
    return result


def get_node_detail(game_id: str, node_id: str) -> dict | None:
    node = graph_builder.get_graph_node(game_id, node_id)
    if node is None:
        return None
    graph = graph_builder.build_game_graph(game_id)
    return {
        "node": node,
        "chain": _event_steps(node.get("event_ids", [])),
        "player_view": _player_view(node, game_id),
        "delivery": _delivery(node, game_id),
        "upstream": _upstream(node, graph),
    }
