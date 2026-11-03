"""Builds the analyzer node graph for one game from the read-only databases.

The graph is a top-down pipeline: `concept` (mission, root) → `game` →
`players` group (roster + NPCs) → `turns` group (one node per turn, each
expanding into scene / dialogue / briefings → choice → action image /
outcome → deaths) → `finale`. The pipeline ranks are enforced with `chain`
edges; turn→turn `flow` arrows are visual-only. Extra non-tree edges:
invite links (references table) and dotted reference-image edges
(avatar/background inputs of ComfyUI calls).

Every node carries light metadata only (labels, thumbnails, badges, event
ids) — full prompts/responses are fetched by node_detail on demand.
"""

import json
import logging
import re
import threading
import time
from typing import Any

import db
from image_utils import comfyui_url_to_path, image_endpoint_path

logger = logging.getLogger(__name__)

_GRAPH_TTL_SECONDS = 5.0
_graph_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()

_PUSH_PRIORITY = {"failed": 1, "expired": 2, "pending": 3, "sent": 4}

_REF_AVATAR_RE = re.compile(r"--- CHARACTER AVATAR ---\n(\S+)")
_REF_BACKGROUND_RE = re.compile(r"--- BACKGROUND ---\n(\S+)")


def _j(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _norm(value: Any) -> str:
    """Normalize a participant identifier the same way log filenames do."""
    return re.sub(r"[^\w]", "_", str(value or ""))


def _img(url: str | None) -> str | None:
    return image_endpoint_path(comfyui_url_to_path(url))


def _npc_avatar_url(description: str | None) -> str | None:
    if not description or not description.startswith("avatar_url="):
        return None
    return description[len("avatar_url="):].split(";", 1)[0]


def _best_push_status(rows: list[dict]) -> str | None:
    best = None
    for row in rows:
        status = row.get("status") or ""
        if best is None or _PUSH_PRIORITY.get(status, 0) > _PUSH_PRIORITY.get(best, 0):
            best = status
    return best


def list_games() -> list[dict]:
    """Game summaries for the dropdown: state at a glance."""
    games = db.query_game_server(
        "SELECT game_id, name, status, language, created_at, started_at FROM games ORDER BY created_at DESC"
    )
    states = {r["game_id"]: r for r in db.query_game_server(
        "SELECT game_id, turn, status, hull_integrity, shields, threat_level, "
        "finale_outcome_type, finale_image_url FROM game_state"
    )}
    missions = {r["game_id"]: r for r in db.query_game_server(
        "SELECT game_id, name, archetype, current_stage, total_stages, completed FROM game_missions"
    )}
    player_counts = {}
    for row in db.query_game_server(
        "SELECT game_id, COUNT(*) total, SUM(is_dead) dead, SUM(is_spectator) spectators "
        "FROM player_profiles WHERE game_id IS NOT NULL GROUP BY game_id"
    ):
        player_counts[row["game_id"]] = row
    # Ended games lose their player_profiles rows; fall back to distinct
    # briefing participants so the dropdown still shows real player counts.
    for row in db.query_game_server(
        "SELECT game_id, COUNT(DISTINCT player_id) total FROM player_briefings "
        "WHERE player_id IS NOT NULL GROUP BY game_id"
    ):
        if not player_counts.get(row["game_id"]):
            player_counts[row["game_id"]] = {
                "total": row["total"], "dead": 0, "spectators": 0,
            }
    npc_counts = {}
    for row in db.query_game_server(
        "SELECT game_id, COUNT(*) total FROM npc_profiles GROUP BY game_id"
    ):
        npc_counts[row["game_id"]] = row["total"]
    turn_counts = {}
    for row in db.query_game_server(
        "SELECT game_id, COUNT(*) total, MAX(turn) max_turn FROM game_turns GROUP BY game_id"
    ):
        turn_counts[row["game_id"]] = row
    last_events = {}
    for row in db.query_game_server(
        "SELECT game_id, MAX(COALESCE(response_at, request_at)) last_at, COUNT(*) total "
        "FROM generation_events GROUP BY game_id"
    ):
        last_events[row["game_id"]] = row
    jobs = {r["game_id"]: r for r in db.query_game_server(
        "SELECT game_id, turn, job_type, status, current_step FROM generation_jobs "
        "WHERE status = 'in_progress' ORDER BY id DESC"
    )}

    result = []
    for game in games:
        gid = game["game_id"]
        state = states.get(gid, {})
        mission = missions.get(gid)
        players = player_counts.get(gid, {})
        job = jobs.get(gid)
        result.append({
            "game_id": gid,
            "name": game["name"],
            "status": game["status"],
            "game_status": state.get("status", ""),
            "language": game["language"],
            "created_at": game["created_at"],
            "current_turn": state.get("turn", 0),
            "max_turn": (turn_counts.get(gid) or {}).get("max_turn", 0),
            "turns_generated": (turn_counts.get(gid) or {}).get("total", 0),
            "players": {
                "total": players.get("total", 0),
                "dead": players.get("dead", 0) or 0,
                "spectators": players.get("spectators", 0) or 0,
            },
            "npcs": npc_counts.get(gid, 0),
            "mission": {
                "name": mission["name"] if mission else "",
                "archetype": mission["archetype"] if mission else "",
                "stage": f'{mission.get("current_stage", 0)}/{mission.get("total_stages", 0)}' if mission else "",
                "completed": bool(mission["completed"]) if mission else False,
            },
            "ship": {
                "hull": state.get("hull_integrity"),
                "shields": state.get("shields"),
                "threat": state.get("threat_level"),
            },
            "finale_outcome_type": state.get("finale_outcome_type", ""),
            "active_job": {
                "turn": job["turn"],
                "job_type": job["job_type"],
                "step": job["current_step"],
            } if job else None,
            "last_event_at": (last_events.get(gid) or {}).get("last_at"),
            "event_count": (last_events.get(gid) or {}).get("total", 0),
        })
    result.sort(key=lambda g: (
        0 if g["status"] == "active" else 1,
        g["last_event_at"] or g["created_at"] or "",
    ), reverse=True)
    return result


def build_game_graph(game_id: str) -> dict | None:
    """Full node graph for a game (cached briefly to keep the UI snappy)."""
    with _cache_lock:
        cached = _graph_cache.get(game_id)
        if cached and time.monotonic() - cached[0] < _GRAPH_TTL_SECONDS:
            return cached[1]
    graph = _build_graph_uncached(game_id)
    if graph is not None:
        with _cache_lock:
            _graph_cache[game_id] = (time.monotonic(), graph)
    return graph


def _build_graph_uncached(game_id: str) -> dict | None:
    games = db.query_game_server(
        "SELECT game_id, name, description, status, language, created_at, started_at FROM games WHERE game_id = ?",
        (game_id,),
    )
    if not games:
        return None
    game = games[0]
    state_rows = db.query_game_server(
        "SELECT turn, status, hull_integrity, shields, threat_level, finale_outcome_type, "
        "finale_narrative, finale_image_url, last_updated FROM game_state WHERE game_id = ?",
        (game_id,),
    )
    state = state_rows[0] if state_rows else {}
    mission_rows = db.query_game_server(
        "SELECT name, description, archetype, current_stage, total_stages, completed, short_description "
        "FROM game_missions WHERE game_id = ?",
        (game_id,),
    )
    mission = mission_rows[0] if mission_rows else None
    turns = db.query_game_server(
        "SELECT turn, created_at, deadline, combined_outcome, story, teaser, global_circumstances "
        "FROM game_turns WHERE game_id = ? ORDER BY turn",
        (game_id,),
    )
    players = db.query_game_server(
        "SELECT player_id, player_name, role, role_description, species, gender, avatar_url, "
        "avatar_description, is_dead, is_spectator, wound_severity, created_at "
        "FROM player_profiles WHERE game_id = ? ORDER BY player_id",
        (game_id,),
    )
    # Ended games lose their player_profiles rows (players move on to new
    # games), so the roster is supplemented from briefings and events.
    briefs = db.query_game_server(
        "SELECT id, turn, player_id, npc_key, is_npc, briefing, choices, selected_action_id, "
        "choice_rationale, chosen_action_url, personal_title, created_at "
        "FROM player_briefings WHERE game_id = ? ORDER BY turn, player_id, npc_key",
        (game_id,),
    )
    events = db.query_game_server(
        "SELECT id, log_file, player_id, turn, kind, stage, backend, request_at, response_at, "
        "model, status, image_url, request_text FROM generation_events WHERE game_id = ? ORDER BY id",
        (game_id,),
    )
    dead_ids: set[int] = set()
    for turn_row in turns:
        outcome = _j(turn_row.get("combined_outcome"), {})
        for death in outcome.get("dead_crew_members") or []:
            entity = str((death or {}).get("entity_id", ""))
            if entity.isdigit():
                dead_ids.add(int(entity))
    known_ids = {p["player_id"] for p in players}
    title_re = re.compile(r"^[^—]*—\s*(.+?)\s*—\s*(.+?)\s*—")
    for pid in sorted({b["player_id"] for b in briefs if b["player_id"]}
                      | {int(e["player_id"]) for e in events if str(e["player_id"]).isdigit()}):
        if pid in known_ids:
            continue
        last_brief = next(
            (b for b in reversed(briefs) if b["player_id"] == pid and b["personal_title"]), None
        )
        name, role = None, None
        if last_brief:
            match = title_re.match(last_brief["personal_title"])
            if match:
                name, role = match.group(1), match.group(2)
        avatar_event = next(
            (e for e in reversed(events)
             if str(e["player_id"]) == str(pid) and e["kind"] == "avatar" and e["image_url"]),
            None,
        )
        players.append({
            "player_id": pid,
            "player_name": name,
            "role": role or "",
            "role_description": "",
            "species": "",
            "gender": "",
            "avatar_url": avatar_event["image_url"] if avatar_event else None,
            "avatar_description": "",
            "is_dead": 1 if pid in dead_ids else 0,
            "is_spectator": 0,
            "wound_severity": None,
            "created_at": next(
                (e["request_at"] for e in events if str(e["player_id"]) == str(pid)), None
            ),
        })
    players.sort(key=lambda p: p["player_id"])
    npcs = db.query_game_server(
        "SELECT npc_key, role_key, npc_name, role, species, gender, avatar_description, "
        "is_active, replaces_player_id, loyalty, wound_severity, created_at "
        "FROM npc_profiles WHERE game_id = ? ORDER BY role_key",
        (game_id,),
    )
    images = db.query_game_server(
        "SELECT id, type, turn, image_url, prompt, created_at FROM game_images "
        "WHERE game_id = ? ORDER BY id",
        (game_id,),
    )
    onboarding = db.query_game_server(
        "SELECT session_id, player_id, current_question, completed, created_at, proposal "
        "FROM onboarding_sessions WHERE answers LIKE ? ",
        (f'%"{game_id}"%',),
    )
    player_ids = {p["player_id"] for p in players}
    onboarding = [o for o in onboarding if o["player_id"] in player_ids]
    game_player_ids = ",".join(str(pid) for pid in player_ids) or "-1"
    actions = db.query_game_server(
        f"SELECT player_id, turn, action_id, choice, created_at FROM player_actions "
        f"WHERE player_id IN ({game_player_ids}) ORDER BY id"
    )
    messages = db.query_game_server(
        f"SELECT id, player_id, message, message_type, timestamp FROM game_messages "
        f"WHERE player_id IN ({game_player_ids}) ORDER BY id"
    )
    jobs = db.query_game_server(
        "SELECT id, turn, job_type, status, current_step, started_at, finished_at, error "
        "FROM generation_jobs WHERE game_id = ? ORDER BY id DESC",
        (game_id,),
    )
    pushes = db.query_bot(
        "SELECT id, player_id, push_type, status, retry_count, error, turn, created_at, sent_at "
        "FROM push_queue WHERE game_id = ?",
        (game_id,),
    )
    references = db.query_bot(
        'SELECT referred_id, referrer_id, created_at FROM "references" WHERE game_id = ?',
        (game_id,),
    )

    nodes: dict[str, dict] = {}
    tree_edges: list[dict] = []
    extra_edges: list[dict] = []
    children: dict[str, list[str]] = {}

    def add(node: dict) -> dict:
        nodes[node["id"]] = node
        parent = node.get("parent")
        if parent and parent in nodes:
            children.setdefault(parent, []).append(node["id"])
            tree_edges.append({
                "id": f"t:{parent}->{node['id']}",
                "source": parent,
                "target": node["id"],
                "etype": "tree",
            })
        return node

    gid = game_id

    # --- participants resolution -------------------------------------------
    player_by_norm = {_norm(p["player_id"]): p["player_id"] for p in players}
    npc_by_norm: dict[str, dict] = {}
    for npc in npcs:
        npc_by_norm[_norm(npc["npc_key"])] = npc
        npc_by_norm[_norm(f"npc:{npc['role_key']}")] = npc
        npc_by_norm[_norm(npc["role_key"])] = npc
    npc_by_key = {npc["npc_key"]: npc for npc in npcs}
    npc_by_role = {npc["role_key"]: npc for npc in npcs}

    def resolve_participant(raw: str) -> tuple[str, Any] | None:
        """('player', player_id) | ('npc', npc_row) | None."""
        norm = _norm(raw)
        if norm in player_by_norm:
            return "player", player_by_norm[norm]
        if norm in npc_by_norm:
            return "npc", npc_by_norm[norm]
        return None

    # --- push delivery index ------------------------------------------------
    push_index: dict[tuple[str, Any, Any], list[dict]] = {}
    for push in pushes:
        push_index.setdefault(
            (push["push_type"], push["player_id"], push["turn"]), []
        ).append(push)

    def delivery_badge(push_type: str, player_id: Any, turn: Any) -> str | None:
        rows = push_index.get((push_type, player_id, turn))
        if not rows:
            return None
        status = _best_push_status(rows)
        if status == "sent":
            return "delivered"
        if status in ("failed", "expired"):
            return "delivery-failed"
        return "queued"

    # --- root: mission (concept) → game ---------------------------------------
    bridge = next((i for i in images if i["type"] == "bridge"), None)
    active_job = next((j for j in jobs if j["status"] == "in_progress"), None)
    game_node_id = f"game:{gid}"
    concept_node_id = f"concept:{gid}"
    if mission:
        add({
            "id": concept_node_id,
            "type": "concept",
            "parent": None,
            "label": mission["name"],
            "subtitle": f'Mission · {mission["archetype"]} · stage '
                        f'{mission["current_stage"]}/{mission["total_stages"]}',
            "summary": mission.get("short_description") or (mission["description"] or "")[:160],
            "thumb": None,
            "badges": ["completed" if mission["completed"] else "internal"],
            "event_ids": [],
            "meta": {"turn": 0, "participant": ""},
            "children_count": 0,
        })
    add({
        "id": game_node_id,
        "type": "game",
        "parent": concept_node_id if mission else None,
        "label": game["name"] or gid,
        "subtitle": f'{gid} · {game["status"]}',
        "summary": " · ".join(filter(None, [
            f'turn {state.get("turn", 0)}' if state else "",
            f'hull {state.get("hull_integrity")}' if state else "",
            f'threat {state.get("threat_level")}' if state else "",
            f'{len(players)} players, {len(npcs)} NPCs',
            f'job: {active_job["current_step"]}' if active_job else "",
        ])),
        "thumb": _img(bridge["image_url"]) if bridge else None,
        "badges": [game["status"]],
        "event_ids": [],
        "meta": {
            "language": game["language"],
            "created_at": game["created_at"],
            "game_status": state.get("status", ""),
            "finale_outcome_type": state.get("finale_outcome_type", ""),
            "active_job": active_job,
        },
        "children_count": 0,
    })

    # --- players group (roster + NPCs) ---------------------------------------
    players_group_id = f"players:{gid}"
    add({
        "id": players_group_id,
        "type": "players",
        "parent": game_node_id,
        "label": "Players",
        "subtitle": f"{len(players)} players · {len(npcs)} NPCs",
        "summary": " · ".join(
            (p["player_name"] or f'player {p["player_id"]}') for p in players
        ),
        "thumb": None,
        "badges": [],
        "event_ids": [],
        "meta": {},
        "children_count": 0,
    })

    # --- players ------------------------------------------------------------
    onboard_by_pid = {o["player_id"]: o for o in onboarding}
    ref_by_referred = {r["referred_id"]: r for r in references}
    for player in players:
        pid = player["player_id"]
        session = onboard_by_pid.get(pid)
        badges = []
        if player["is_dead"]:
            badges.append("dead")
        if player["is_spectator"]:
            badges.append("spectator")
        badges.append(delivery_badge("onboarding", pid, None) or "internal")
        ref = ref_by_referred.get(pid)
        add({
            "id": f"player:{gid}:{pid}",
            "type": "player",
            "parent": players_group_id,
            "label": player["player_name"] or f"player {pid}",
            "subtitle": f'{pid} · {player["role"]}',
            "summary": " · ".join(filter(None, [
                player["species"],
                player["gender"],
                f'character rerolls {session["current_question"]}' if session else "",
                f'wound {player["wound_severity"]}' if player["wound_severity"] else "",
            ])),
            "thumb": _img(player["avatar_url"]),
            "badges": badges,
            "event_ids": [],
            "meta": {
                "player_id": pid,
                "role": player["role"],
                "is_npc": False,
                "referrer_id": ref["referrer_id"] if ref else None,
                "created_at": player["created_at"],
            },
            "children_count": 0,
        })
        if ref and ref["referrer_id"] in player_ids:
            extra_edges.append({
                "id": f"invite:{gid}:{ref['referrer_id']}->{pid}",
                "source": f"player:{gid}:{ref['referrer_id']}",
                "target": f"player:{gid}:{pid}",
                "etype": "invite",
            })

    # --- NPCs ----------------------------------------------------------------
    for npc in npcs:
        badges = ["npc"]
        if not npc["is_active"]:
            badges.append("inactive")
        add({
            "id": f"npc:{gid}:{npc['role_key']}",
            "type": "npc",
            "parent": players_group_id,
            "label": npc["npc_name"],
            "subtitle": f'NPC · {npc["role_key"]} · {npc["role"]}',
            "summary": " · ".join(filter(None, [
                f'loyalty {npc["loyalty"]}',
                f'wound {npc["wound_severity"]}' if npc["wound_severity"] else "",
                f'replaces player {npc["replaces_player_id"]}' if npc["replaces_player_id"] else "",
            ])),
            "thumb": _img(_npc_avatar_url(npc["avatar_description"])),
            "badges": badges,
            "event_ids": [],
            "meta": {
                "npc_key": npc["npc_key"],
                "role_key": npc["role_key"],
                "is_npc": True,
                "created_at": npc["created_at"],
            },
            "children_count": 0,
        })

    # --- turns group ------------------------------------------------------------
    turns_group_id = f"turns:{gid}"
    add({
        "id": turns_group_id,
        "type": "turns",
        "parent": game_node_id,
        "label": "Turns",
        "subtitle": f"{len(turns)} turns",
        "summary": (turns[-1]["teaser"] or turns[-1]["story"] or "")[:140] if turns else "",
        "thumb": None,
        "badges": [],
        "event_ids": [],
        "meta": {},
        "children_count": 0,
    })

    # --- turns and their contents --------------------------------------------
    images_by_turn: dict[int, list[dict]] = {}
    for image in images:
        images_by_turn.setdefault(image["turn"] or 0, []).append(image)

    def turn_image(turn: int, img_type: str, participant: str | None = None) -> dict | None:
        candidates = [i for i in images_by_turn.get(turn, []) if i["type"] == img_type]
        if not candidates:
            return None
        if participant:
            # Participant ids live in the generated filename (char_turn1_p281412419).
            key = participant.lstrip("pn:").replace("_", "")
            if key:
                for image in candidates:
                    filename = (comfyui_url_to_path(image["image_url"]) or "").replace("_", "")
                    if key in filename:
                        return image
                return None
        return candidates[0]

    actions_index = {(a["player_id"], a["turn"]): a for a in actions}

    def part_of_brief(brief: dict) -> str:
        if brief["npc_key"]:
            role_key = (npc_by_key.get(brief["npc_key"]) or {}).get("role_key", brief["npc_key"])
            return f"n:{role_key}"
        return f"p{brief['player_id']}"

    turn_created = {t["turn"]: t["created_at"] for t in turns}

    def message_turn(timestamp: str) -> int | None:
        result = None
        for turn, created in sorted(turn_created.items()):
            if created and timestamp and timestamp >= created:
                result = turn
        return result

    for turn_row in turns:
        turn = turn_row["turn"]
        turn_id = f"turn:{gid}:{turn}"
        has_outcome = bool(_j(turn_row["combined_outcome"], None))
        scene_img = turn_image(turn, "scene") or turn_image(turn, "turn_background")
        turn_badges = [
            "done" if has_outcome else "active" if turn == (state.get("turn") or 0) else "pending"
        ]
        if any(a["turn"] == turn and a["choice"] == "auto_selected" for a in actions):
            turn_badges.append("auto")
        add({
            "id": turn_id,
            "type": "turn",
            "parent": turns_group_id,
            "label": f"Turn {turn}",
            "subtitle": (turn_row["deadline"] or "")[:16].replace("T", " ") or "no deadline",
            "summary": (turn_row["teaser"] or turn_row["story"] or "")[:140],
            "thumb": _img(scene_img["image_url"]) if scene_img else None,
            "badges": turn_badges,
            "event_ids": [],
            "meta": {"turn": turn, "created_at": turn_row["created_at"]},
            "children_count": 0,
        })

        scene_id = f"scene:{gid}:{turn}"
        add({
            "id": scene_id,
            "type": "scene",
            "parent": turn_id,
            "label": "Scene setup",
            "subtitle": f"circumstances + scene + background",
            "summary": str(_j(turn_row["global_circumstances"], {}).get("situation", ""))[:160]
            if turn_row["global_circumstances"] else "",
            "thumb": _img(scene_img["image_url"]) if scene_img else None,
            "badges": ["internal"],
            "event_ids": [],
            "meta": {"turn": turn, "participant": ""},
            "children_count": 0,
        })

        add({
            "id": f"dialogue:{gid}:{turn}",
            "type": "dialogue",
            "parent": turn_id,
            "label": "Crew dialogue",
            "subtitle": "crew_dialogue",
            "summary": "",
            "thumb": None,
            "badges": ["internal"],
            "event_ids": [],
            "meta": {"turn": turn, "participant": ""},
            "children_count": 0,
        })

        turn_briefs = [b for b in briefs if b["turn"] == turn]
        for brief in turn_briefs:
            part = part_of_brief(brief)
            is_npc = bool(brief["is_npc"])
            choices = _j(brief["choices"], [])
            chosen = next((c for c in choices if c.get("id") == brief["selected_action_id"]), None)
            profile_label = ""
            if is_npc:
                npc = npc_by_key.get(brief["npc_key"])
                profile_label = npc["npc_name"] if npc else brief["npc_key"]
            else:
                profile = next((p for p in players if p["player_id"] == brief["player_id"]), None)
                profile_label = (profile["player_name"] if profile else None) or f'player {brief["player_id"]}'

            brief_id = f"brief:{gid}:{turn}:{part}"
            char_img = turn_image(turn, "character", part)
            badges = ["npc" if is_npc else "player"]
            if not is_npc:
                badges.append(delivery_badge("briefing", brief["player_id"], turn) or "not-delivered")
            add({
                "id": brief_id,
                "type": "brief",
                "parent": turn_id,
                "label": profile_label,
                "subtitle": f'briefing · {part}',
                "summary": (brief["personal_title"] or brief["briefing"] or "")[:140],
                "thumb": _img(char_img["image_url"] if char_img else None)
                or _img(brief["chosen_action_url"]),
                "badges": badges,
                "event_ids": [],
                "meta": {
                    "turn": turn,
                    "participant": part,
                    "player_id": brief["player_id"],
                    "npc_key": brief["npc_key"],
                    "is_npc": is_npc,
                    "briefing_row_id": brief["id"],
                },
                "children_count": 0,
            })

            if brief["selected_action_id"]:
                choice_id = f"choice:{gid}:{turn}:{part}"
                auto = False
                if not is_npc and brief["player_id"] is not None:
                    action_row = actions_index.get((brief["player_id"], turn))
                    auto = bool(action_row and action_row["choice"] == "auto_selected")
                choice_badges = ["npc" if is_npc else "player"]
                if auto:
                    choice_badges.append("auto")
                if chosen and chosen.get("consequence_kind"):
                    choice_badges.append(chosen["consequence_kind"])
                add({
                    "id": choice_id,
                    "type": "choice",
                    "parent": brief_id,
                    "label": (chosen.get("text") if chosen else brief["selected_action_id"]) or brief["selected_action_id"],
                    "subtitle": f'choice · {brief["selected_action_id"]}',
                    "summary": (chosen or {}).get("consequence", "")[:160],
                    "thumb": None,
                    "badges": choice_badges,
                    "event_ids": [],
                    "meta": {
                        "turn": turn,
                        "participant": part,
                        "player_id": brief["player_id"],
                        "npc_key": brief["npc_key"],
                        "is_npc": is_npc,
                        "action_id": brief["selected_action_id"],
                        "briefing_row_id": brief["id"],
                    },
                    "children_count": 0,
                })
                if brief["chosen_action_url"]:
                    action_badges = ["internal"]
                    if not is_npc:
                        action_badges.insert(0, delivery_badge("action", brief["player_id"], turn) or "not-delivered")
                    add({
                        "id": f"actionimg:{gid}:{turn}:{part}",
                        "type": "actionimg",
                        "parent": choice_id,
                        "label": "Action image",
                        "subtitle": f'action panel · {part}',
                        "summary": "",
                        "thumb": _img(brief["chosen_action_url"]),
                        "badges": action_badges,
                        "event_ids": [],
                        "meta": {
                            "turn": turn,
                            "participant": part,
                            "player_id": brief["player_id"],
                            "npc_key": brief["npc_key"],
                            "is_npc": is_npc,
                            "briefing_row_id": brief["id"],
                        },
                        "children_count": 0,
                    })

        outcome_raw = _j(turn_row["combined_outcome"], {})
        if outcome_raw:
            outcome_img = turn_image(turn, "outcome")
            add({
                "id": f"outcome:{gid}:{turn}",
                "type": "outcome",
                "parent": turn_id,
                "label": f"Outcome · turn {turn}",
                "subtitle": outcome_raw.get("outcome_type", ""),
                "summary": (outcome_raw.get("narrative") or outcome_raw.get("summary") or "")[:160],
                "thumb": _img(outcome_img["image_url"]) if outcome_img else None,
                "badges": [delivery_badge("outcome", None, turn) or "internal"],
                "event_ids": [],
                "meta": {"turn": turn, "participant": ""},
                "children_count": 0,
            })
            deaths = outcome_raw.get("dead_crew_members") or []
            if isinstance(deaths, list):
                for death in deaths:
                    entity_id = death.get("entity_id") if isinstance(death, dict) else None
                    if entity_id is None or not str(entity_id).isdigit():
                        continue
                    dead_pid = int(entity_id)
                    add({
                        "id": f"death:{gid}:{turn}:{dead_pid}",
                        "type": "death",
                        "parent": f"outcome:{gid}:{turn}",
                        "label": f"Death · {dead_pid}",
                        "subtitle": "player_death",
                        "summary": (death.get("cause") or "")[:160] if isinstance(death, dict) else "",
                        "thumb": None,
                        "badges": [delivery_badge("player_death", dead_pid, turn) or "fatal"],
                        "event_ids": [],
                        "meta": {"turn": turn, "player_id": dead_pid, "participant": f"p{dead_pid}"},
                        "children_count": 0,
                    })

        for message in messages:
            if message_turn(message["timestamp"]) != turn:
                continue
            add({
                "id": f"msg:{gid}:{message['id']}",
                "type": "msg",
                "parent": turn_id,
                "label": f"message · {message['player_id']}",
                "subtitle": message["message_type"] or "text",
                "summary": (message["message"] or "")[:140],
                "thumb": None,
                "badges": ["internal"],
                "event_ids": [],
                "meta": {"turn": turn, "player_id": message["player_id"], "participant": f"p{message['player_id']}"},
                "children_count": 0,
            })

    # --- finale ---------------------------------------------------------------
    if state.get("finale_narrative") or state.get("finale_image_url"):
        add({
            "id": f"finale:{gid}",
            "type": "finale",
            "parent": game_node_id,
            "label": f'Finale · {state.get("finale_outcome_type", "")}',
            "subtitle": "game_over_outcome",
            "summary": (state.get("finale_narrative") or "")[:160],
            "thumb": _img(state.get("finale_image_url")),
            "badges": [delivery_badge("game_over", None, None) or "internal"],
            "event_ids": [],
            "meta": {"turn": 0, "participant": ""},
            "children_count": 0,
        })

    # --- turn sequence edges ----------------------------------------------------
    for turn_row in turns:
        turn = turn_row["turn"]
        next_turn = next((t["turn"] for t in turns if t["turn"] == turn + 1), None)
        if next_turn is not None:
            extra_edges.append({
                "id": f"flow:turn:{gid}:{turn}",
                "source": f"turn:{gid}:{turn}",
                "target": f"turn:{gid}:{next_turn}",
                "etype": "flow",
            })

    # --- pipeline chain edges ----------------------------------------------------
    # They participate in layout, ranking the spine top-down:
    # game → players group → turns group → finale (below the last turn).
    chain_pairs = [(players_group_id, turns_group_id)]
    finale_node_id = f"finale:{gid}"
    if finale_node_id in nodes:
        chain_pairs.append((turns_group_id, finale_node_id))
        if turns:
            last_turn_id = f"turn:{gid}:{turns[-1]['turn']}"
            if last_turn_id in nodes:
                chain_pairs.append((last_turn_id, finale_node_id))
    for source, target in chain_pairs:
        extra_edges.append({
            "id": f"chain:{source}->{target}",
            "source": source,
            "target": target,
            "etype": "chain",
        })

    # --- attach generation events to nodes ---------------------------------------
    def event_node(event: dict) -> str | None:
        kind = event["kind"]
        stage = event["stage"]
        turn = event["turn"] or 0
        participant = resolve_participant(event["player_id"])
        if stage == "game_concept":
            return f"concept:{gid}" if f"concept:{gid}" in nodes else None
        if stage == "game_start":
            return game_node_id
        if stage == "onboarding":
            if participant and participant[0] == "player":
                return f"player:{gid}:{participant[1]}"
            return None
        if stage == "npc":
            npc = None
            if participant and participant[0] == "npc":
                npc = participant[1]
            else:
                # NPC calls often log with an empty player_id; the role is
                # then only present in the kind suffix (npc_name_{role}, ...).
                role_match = re.match(r"^npc_(?:name|role_flavour|avatar)_(.+)$", kind)
                if role_match and role_match.group(1) in npc_by_role:
                    npc = npc_by_role[role_match.group(1)]
            if npc:
                return f"npc:{gid}:{npc['role_key']}"
            # Batch prompt call shared by all NPCs → game node chain.
            return game_node_id
        if stage == "content_pool":
            return None
        if stage == "turn":
            if kind in ("global_circumstances", "turn_background_prompt", "turn_background", "scene"):
                return f"scene:{gid}:{turn}" if f"scene:{gid}:{turn}" in nodes else None
            if kind in ("player_briefing", "character_scene"):
                if participant and participant[0] == "player":
                    return f"brief:{gid}:{turn}:p{participant[1]}" if f"brief:{gid}:{turn}:p{participant[1]}" in nodes else None
                if participant and participant[0] == "npc":
                    node_id = f"brief:{gid}:{turn}:n:{participant[1]['role_key']}"
                    return node_id if node_id in nodes else None
                return None
            if kind == "crew_dialogue":
                return f"dialogue:{gid}:{turn}" if f"dialogue:{gid}:{turn}" in nodes else None
            return None
        if stage == "player_action":
            if not participant:
                return None
            if participant[0] == "npc":
                role_key = participant[1]["role_key"]
                if kind in ("npc_choice", "player_auto_choice"):
                    return f"choice:{gid}:{turn}:n:{role_key}" if f"choice:{gid}:{turn}:n:{role_key}" in nodes else None
                if kind == "npc_action":
                    return f"actionimg:{gid}:{turn}:n:{role_key}" if f"actionimg:{gid}:{turn}:n:{role_key}" in nodes else None
                return None
            pid = participant[1]
            if kind == "player_auto_choice":
                return f"choice:{gid}:{turn}:p{pid}" if f"choice:{gid}:{turn}:p{pid}" in nodes else None
            if kind == "player_action":
                return f"actionimg:{gid}:{turn}:p{pid}" if f"actionimg:{gid}:{turn}:p{pid}" in nodes else None
            if kind == "player_message":
                return f"brief:{gid}:{turn}:p{pid}" if f"brief:{gid}:{turn}:p{pid}" in nodes else None
            return None
        if stage == "outcome":
            if kind in ("combined_outcome", "outcome_image_prompt", "outcome"):
                return f"outcome:{gid}:{turn}" if f"outcome:{gid}:{turn}" in nodes else None
            if kind in ("death_notice", "player_death") and participant and participant[0] == "player":
                return f"death:{gid}:{turn}:{participant[1]}" if f"death:{gid}:{turn}:{participant[1]}" in nodes else None
            return None
        if stage == "finale":
            return f"finale:{gid}" if f"finale:{gid}" in nodes else None
        return None

    def ref_target(path: str | None) -> str | None:
        """Map a reference image path (avatar/turn_bg) to its source node."""
        if not path:
            return None
        name = path.rsplit("/", 1)[-1]
        match = re.match(r"avatar_(\w+?)_\d{5}_", name)
        if match:
            token = match.group(1)
            if token.isdigit() and int(token) in player_ids:
                return f"player:{gid}:{int(token)}"
            for npc in npcs:
                if npc["role_key"] == token:
                    return f"npc:{gid}:{token}"
        bg = re.match(r"turn_bg_turn(\d+)_", name)
        if bg:
            scene_id = f"scene:{gid}:{bg.group(1)}"
            return scene_id if scene_id in nodes else None
        return None

    for event in events:
        node_id = event_node(event)
        if node_id:
            nodes[node_id]["event_ids"].append(event["id"])
            if event["status"] == "fallback" and "fallback" not in nodes[node_id]["badges"]:
                nodes[node_id]["badges"].append("fallback")
            if event["status"] == "pending" and not event["response_at"] and "no-response" not in nodes[node_id]["badges"]:
                nodes[node_id]["badges"].append("no-response")
            if event["backend"] == "comfyui" and not nodes[node_id].get("thumb") and event["image_url"]:
                nodes[node_id]["thumb"] = _img(event["image_url"])
        if event["backend"] == "comfyui" and event["request_text"] and node_id:
            for pattern in (_REF_AVATAR_RE, _REF_BACKGROUND_RE):
                match = pattern.search(event["request_text"])
                if not match:
                    continue
                source_id = ref_target(match.group(1))
                if source_id and source_id != node_id:
                    extra_edges.append({
                        "id": f"ref:{event['id']}:{pattern.pattern[:8]}",
                        "source": source_id,
                        "target": node_id,
                        "etype": "ref",
                    })

    for node in nodes.values():
        node["children_count"] = len(children.get(node["id"], []))

    return {
        "game_id": gid,
        "root": concept_node_id if mission else game_node_id,
        "nodes": list(nodes.values()),
        "edges": tree_edges + extra_edges,
    }


def get_graph_node(game_id: str, node_id: str) -> dict | None:
    graph = build_game_graph(game_id)
    if graph is None:
        return None
    return next((n for n in graph["nodes"] if n["id"] == node_id), None)
