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
from datetime import datetime, timezone

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

# Image kinds produced by the multi-reference edit model: avatar (Picture 1),
# background (Picture 2), optional pose skeleton (Picture 3).
EDIT_KINDS = frozenset({"player_action", "npc_action", "character_scene", "player_death"})


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


def _ref_filename(section_text: str | None) -> str | None:
    """First filename line of a reference-image section; '(none)' = absent."""
    if not section_text:
        return None
    for line in section_text.splitlines():
        stripped = line.strip()
        if stripped and stripped != "(none)":
            return stripped
    return None


def _edit_inputs(event: dict) -> dict | None:
    """Reference images + instruction fed into an edit-kind ComfyUI call.

    The request body is mirrored in generation_events.request_text (same
    content as the detailed log file), so it is parsed straight from the row.
    Avatar/background lines are 'game_id/file.png' subfolder paths; pose
    skeletons are bare volume-root filenames.
    """
    if event["backend"] != "comfyui" or event["kind"] not in EDIT_KINDS:
        return None
    if not event["request_text"]:
        return None
    sections = {s["name"]: s["text"] for s in _parse_sections(event["request_text"])}

    def ref(name: str) -> dict | None:
        filename = _ref_filename(sections.get(name))
        if not filename:
            return None
        return {"filename": filename, "image_url": image_endpoint_path(filename)}

    return {
        "instruction": sections.get("INSTRUCTION") or None,
        "avatar": ref("CHARACTER AVATAR"),
        "background": ref("BACKGROUND"),
        "pose": ref("POSE REFERENCE"),
        "output_image_url": image_endpoint_path(_comfyui_path(event["image_url"])),
    }


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
            "edit_inputs": _edit_inputs(event),
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
                {
                    "id": c.get("id"),
                    "text": c.get("text", ""),
                    "consequence": c.get("consequence", ""),
                    "consequence_kind": c.get("consequence_kind", ""),
                }
                for c in choices
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
            "SELECT * FROM player_profiles WHERE game_id = ? AND player_id = ?",
            (gid, meta["player_id"]),
        )
        if rows:
            profile = rows[0]
            return {
                "image": image_endpoint_path(_comfyui_path(profile["avatar_url"])),
                "text": "\n".join(filter(None, [
                    profile["role_description"],
                    profile["species_description"],
                ])),
            }
        # Ended games lose their player_profiles row; the roster card was
        # rebuilt from this game's briefings/events — reuse its avatar.
        return {"image": node.get("thumb"), "text": ""}
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


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO timestamp; aware values are normalized to naive UTC so
    that deadline (tz-aware from the scheduler) and player_actions.created_at
    (naive local UTC from game-server) stay comparable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _action_timing(brief: dict, auto: bool, submitted_at: str | None,
                   deadline: str | None) -> str:
    """Classify a briefing choice: submitted before the deadline or not."""
    if brief["is_npc"]:
        return "npc"
    if not brief["selected_action_id"]:
        return "pending"
    if auto:
        return "auto"
    if submitted_at is None:
        return "chosen"
    submitted = _parse_dt(submitted_at)
    due = _parse_dt(deadline)
    if submitted is not None and due is not None and submitted > due:
        return "late"
    return "on-time"


def _turn_actions(node: dict, gid: str, graph: dict) -> dict | None:
    """Per-participant action summary for a turn node: proposed variants,
    the chosen action and whether it was submitted before the deadline."""
    if node["type"] != "turn":
        return None
    turn = node["meta"].get("turn")
    if turn is None:
        return None
    rows = db.query_game_server(
        "SELECT deadline FROM game_turns WHERE game_id = ? AND turn = ?",
        (gid, turn),
    )
    deadline = rows[0]["deadline"] if rows else None
    briefs = db.query_game_server(
        "SELECT id, player_id, npc_key, is_npc, choices, selected_action_id "
        "FROM player_briefings WHERE game_id = ? AND turn = ? "
        "ORDER BY is_npc, player_id, npc_key",
        (gid, turn),
    )
    # player_action_stats is game-scoped and carries the authoritative
    # is_auto marker; player_actions is a legacy fallback (no game_id, so it
    # is scoped via the briefing roster).
    stat_by_pid = {
        r["player_id"]: r
        for r in db.query_game_server(
            "SELECT player_id, action_id, is_auto, created_at FROM player_action_stats "
            "WHERE game_id = ? AND turn = ?",
            (gid, turn),
        )
    }
    player_ids = sorted({b["player_id"] for b in briefs if b["player_id"] is not None})
    action_by_pid = {}
    if player_ids:
        placeholders = ",".join("?" * len(player_ids))
        action_by_pid = {
            r["player_id"]: r
            for r in db.query_game_server(
                f"SELECT player_id, action_id, choice, created_at FROM player_actions "
                f"WHERE turn = ? AND player_id IN ({placeholders})",
                (turn, *player_ids),
            )
        }
    names = {}
    brief_nodes = {}
    for n in graph["nodes"]:
        if n["type"] == "player" and n["meta"].get("player_id") is not None:
            names[("player", n["meta"]["player_id"])] = n["label"]
        elif n["type"] == "npc":
            names[("npc", n["meta"]["npc_key"])] = n["label"]
        elif n["type"] == "brief" and n["meta"].get("briefing_row_id") is not None:
            brief_nodes[n["meta"]["briefing_row_id"]] = n
    outcome_node_id = next(
        (n["id"] for n in graph["nodes"]
         if n["type"] == "outcome" and n["meta"].get("turn") == turn),
        None,
    )
    actions = []
    for brief in briefs:
        is_npc = bool(brief["is_npc"])
        variants = [
            {
                "id": c.get("id"),
                "text": c.get("text", ""),
                "consequence": c.get("consequence", ""),
                "consequence_kind": c.get("consequence_kind", ""),
            }
            for c in _j(brief["choices"], [])
        ]
        chosen = next(
            (v for v in variants if v["id"] == brief["selected_action_id"]), None
        )
        brief_node = brief_nodes.get(brief["id"])
        brief_node_id = brief_node["id"] if brief_node else None
        choice_node_id = None
        if brief_node and brief["selected_action_id"]:
            choice_node_id = (
                f"choice:{gid}:{turn}:{brief_node['meta'].get('participant', '')}"
            )
            if choice_node_id not in {n["id"] for n in graph["nodes"]}:
                choice_node_id = None
        if is_npc:
            participant = names.get(("npc", brief["npc_key"]), brief["npc_key"])
            stat = None
            action = None
        else:
            participant = names.get(("player", brief["player_id"]),
                                    f"player {brief['player_id']}")
            stat = stat_by_pid.get(brief["player_id"])
            action = action_by_pid.get(brief["player_id"])
        auto = bool(stat and stat["is_auto"]) or bool(
            action and action["choice"] == "auto_selected"
        )
        submitted_at = (stat["created_at"] if stat else None) or (
            action["created_at"] if action else None
        )
        actions.append({
            "participant": participant,
            "is_npc": is_npc,
            "brief_node_id": brief_node_id,
            "choice_node_id": choice_node_id,
            "variants": variants,
            "chosen": chosen,
            "chosen_kind": ("auto_selected" if auto else "selected")
            if (stat or action) else None,
            "submitted_at": submitted_at,
            "timing": _action_timing(brief, auto, submitted_at, deadline),
        })
    return {
        "deadline": deadline,
        "outcome_node_id": outcome_node_id,
        "actions": actions,
    }


def get_node_detail(game_id: str, node_id: str) -> dict | None:
    node = graph_builder.get_graph_node(game_id, node_id)
    if node is None:
        return None
    graph = graph_builder.build_game_graph(game_id)
    return {
        "node": node,
        "chain": _event_steps(node.get("event_ids", [])),
        "player_view": _player_view(node, game_id),
        "actions": _turn_actions(node, game_id, graph),
        "delivery": _delivery(node, game_id),
        "upstream": _upstream(node, graph),
    }
