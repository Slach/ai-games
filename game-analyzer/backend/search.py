"""Full-text search over generation events, with node resolution."""

import logging

import db
import graph_builder

logger = logging.getLogger(__name__)


def _resolve_node_ids(game_id: str, event_ids: list[int]) -> dict[int, str]:
    """Map event ids to the graph node that owns them."""
    graph = graph_builder.build_game_graph(game_id)
    mapping: dict[int, str] = {}
    if graph is None:
        return mapping
    wanted = set(event_ids)
    for node in graph["nodes"]:
        for event_id in node.get("event_ids", []):
            if event_id in wanted:
                mapping[event_id] = node["id"]
    return mapping


def search_events(query_text: str, game_id: str | None, limit: int = 30) -> list[dict]:
    """FTS5 prefix search with LIKE fallback; every hit carries its node id."""
    tokens = [t for t in query_text.replace('"', " ").split() if t]
    results: list[dict] = []
    if tokens:
        match_expr = " ".join(f'"{t}"*' for t in tokens)
        sql = (
            "SELECT e.id, e.game_id, e.player_id, e.turn, e.kind, e.stage, e.backend, "
            "e.status, e.request_at, e.response_at, "
            "snippet(generation_events_fts, 0, '[', ']', '…', 14) AS snippet "
            "FROM generation_events_fts f JOIN generation_events e ON e.id = f.rowid "
            "WHERE generation_events_fts MATCH ?"
        )
        args = [match_expr]
        if game_id:
            sql += " AND e.game_id = ?"
            args.append(game_id)
        sql += " ORDER BY e.id DESC LIMIT ?"
        args.append(limit)
        results = db.query_game_server(sql, tuple(args))
    if not results and query_text:
        like = f"%{query_text}%"
        sql = (
            "SELECT e.id, e.game_id, e.player_id, e.turn, e.kind, e.stage, e.backend, "
            "e.status, e.request_at, e.response_at, "
            "substr(COALESCE(NULLIF(e.response_text, ''), e.request_text), 1, 300) AS snippet "
            "FROM generation_events e WHERE (e.request_text LIKE ? OR e.response_text LIKE ?)"
        )
        args = [like, like]
        if game_id:
            sql += " AND e.game_id = ?"
            args.append(game_id)
        sql += " ORDER BY e.id DESC LIMIT ?"
        args.append(limit)
        results = db.query_game_server(sql, tuple(args))

    node_maps: dict[str, dict[int, str]] = {}
    for row in results:
        gid = row["game_id"]
        if gid not in node_maps:
            node_maps[gid] = _resolve_node_ids(gid, [r["id"] for r in results if r["game_id"] == gid])
        row["node_id"] = node_maps[gid].get(row["id"])
    return results
