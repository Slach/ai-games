"""Graph builder / search / node detail tests against fixture sqlite DBs."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

GAME_SERVER_SCHEMA = """
CREATE TABLE games (game_id TEXT PRIMARY KEY, name TEXT, description TEXT,
    status TEXT DEFAULT 'active', language TEXT DEFAULT 'ru', created_at TEXT, started_at TEXT);
CREATE TABLE game_state (game_id TEXT, turn INTEGER, status TEXT, hull_integrity INTEGER,
    shields INTEGER, threat_level INTEGER, finale_outcome_type TEXT, finale_narrative TEXT,
    finale_image_url TEXT, last_updated TEXT);
CREATE TABLE game_missions (game_id TEXT, name TEXT, description TEXT, archetype TEXT,
    current_stage INTEGER, total_stages INTEGER, completed INTEGER, short_description TEXT);
CREATE TABLE player_profiles (player_id INTEGER PRIMARY KEY, player_name TEXT, role TEXT,
    role_description TEXT, species TEXT, gender TEXT, avatar_url TEXT, avatar_description TEXT,
    is_dead INTEGER DEFAULT 0, is_spectator INTEGER DEFAULT 0, wound_severity TEXT,
    created_at TEXT, game_id TEXT);
CREATE TABLE npc_profiles (npc_key TEXT PRIMARY KEY, role_key TEXT, npc_name TEXT, role TEXT,
    species TEXT, gender TEXT, avatar_description TEXT, is_active INTEGER DEFAULT 1,
    replaces_player_id INTEGER, loyalty INTEGER, wound_severity TEXT, created_at TEXT, game_id TEXT);
CREATE TABLE game_turns (turn INTEGER, game_id TEXT, created_at TEXT, deadline TEXT,
    combined_outcome TEXT, story TEXT, teaser TEXT, global_circumstances TEXT, crew_dialogues TEXT,
    PRIMARY KEY (turn, game_id));
CREATE TABLE player_briefings (id INTEGER PRIMARY KEY AUTOINCREMENT, turn INTEGER,
    player_id INTEGER, npc_key TEXT, is_npc INTEGER DEFAULT 0, briefing TEXT, choices TEXT,
    selected_action_id TEXT, choice_rationale TEXT, chosen_action_url TEXT, personal_title TEXT,
    created_at TEXT, game_id TEXT);
CREATE TABLE game_images (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, turn INTEGER,
    image_url TEXT, prompt TEXT, created_at TEXT, game_id TEXT);
CREATE TABLE onboarding_sessions (session_id TEXT PRIMARY KEY, player_id INTEGER,
    current_question INTEGER DEFAULT 0, completed INTEGER DEFAULT 0, created_at TEXT,
    proposal TEXT, answers TEXT);
CREATE TABLE player_actions (id INTEGER PRIMARY KEY AUTOINCREMENT, player_id INTEGER,
    turn INTEGER, action_id TEXT, choice TEXT, created_at TEXT);
CREATE TABLE player_action_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT,
    player_id INTEGER, turn INTEGER, action_id TEXT, action_text TEXT,
    consequence_kind TEXT, hull_integrity INTEGER, is_auto INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE game_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, player_id INTEGER,
    message TEXT, message_type TEXT, timestamp TEXT);
CREATE TABLE generation_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, turn INTEGER,
    job_type TEXT, status TEXT, current_step TEXT, started_at TEXT, finished_at TEXT, error TEXT);
CREATE TABLE generation_events (id INTEGER PRIMARY KEY AUTOINCREMENT, log_file TEXT,
    game_id TEXT, player_id TEXT, turn INTEGER, kind TEXT, stage TEXT, backend TEXT,
    request_at TEXT, response_at TEXT, request_text TEXT, response_text TEXT, model TEXT,
    params TEXT, finish_reason TEXT, usage TEXT, image_url TEXT,
    status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 1);
CREATE VIRTUAL TABLE generation_events_fts USING fts5(content);
"""

BOT_SCHEMA = """
CREATE TABLE push_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, player_id INTEGER,
    push_type TEXT, payload TEXT, status TEXT, error TEXT, retry_count INTEGER,
    turn INTEGER, game_id TEXT, created_at TEXT, sent_at TEXT);
CREATE TABLE "references" (referred_id INTEGER, referrer_id INTEGER, game_id TEXT, created_at TEXT);
"""

GID = "tstgme"
COMFY = "http://comfyui:8188/view?filename={name}&subfolder={gid}&type=output"


def _seed(conn: sqlite3.Connection):
    c = conn.cursor()
    c.execute(
        "INSERT INTO games VALUES (?,?,?,?,?,?,?)",
        (GID, "Test Ship: Hold the Line", "desc", "active", "ru", "2026-11-01T10:00:00", None),
    )
    c.execute(
        "INSERT INTO game_state VALUES (?,?,?,?,?,?,?,?,?,?)",
        (GID, 2, "active", 80, 60, 30, "mission_complete", "The convoy made it.", "",
         "2026-11-01T12:00:00"),
    )
    c.execute(
        "INSERT INTO game_missions VALUES (?,?,?,?,?,?,?,?)",
        (GID, "Escort the convoy", "desc", "escort", 1, 3, 0, "short"),
    )
    c.execute(
        "INSERT INTO player_profiles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (111, "Slach", "security", "role desc", "reptiloid", "male",
         COMFY.format(name="avatar_111_00001_.png", gid=GID), "", 0, 0, None,
         "2026-11-01T10:05:00", GID),
    )
    c.execute(
        "INSERT INTO player_profiles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (222, "Anton", "pilot", "role desc 2", "human", "male",
         COMFY.format(name="avatar_222_00001_.png", gid=GID), "", 1, 0, None,
         "2026-11-01T10:30:00", GID),
    )
    c.execute(
        "INSERT INTO npc_profiles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"npc_engineer_{GID}", "engineer", "Mika", "Engineer", "human", "female",
         f"avatar_url={COMFY.format(name='avatar_engineer_00001_.png', gid=GID)};prompt", 1,
         None, 70, None, "2026-11-01T10:50:00", GID),
    )
    outcome = json.dumps({
        "outcome_narrative": "The convoy held.",
        "dead_crew_members": [{"entity_id": "p222", "cause": "hull breach"}],
    })
    c.execute(
        "INSERT INTO game_turns VALUES (?,?,?,?,?,?,?,?,?)",
        (1, GID, "2026-11-01T11:00:00", "2026-11-01T19:00:00+00:00", outcome,
         "story", "teaser", json.dumps({"narrative": "SOS repeats", "setting": "asteroids"}), "[]"),
    )
    c.execute(
        "INSERT INTO game_turns VALUES (?,?,?,?,?,?,?,?,?)",
        (2, GID, "2026-11-01T18:00:00", "2026-11-01T23:00:00+00:00", None,
         "story2", "teaser2", "{}", "[]"),
    )
    choices = json.dumps([
        {"id": "scan", "text": "Scan the wreck", "consequence": "reveal", "consequence_kind": "progress"},
        {"id": "run", "text": "Run away", "consequence": "boom", "consequence_kind": "fatal"},
    ])
    c.execute(
        "INSERT INTO player_briefings (turn, player_id, npc_key, is_npc, briefing, choices, "
        "selected_action_id, chosen_action_url, personal_title, created_at, game_id) "
        "VALUES (1, 111, NULL, 0, 'briefing text zebra', ?, 'scan', ?, 'Turn 1 — Slach', "
        "'2026-11-01T11:05:00', ?)",
        (choices, COMFY.format(name="action_turn1_p111_00001_.png", gid=GID), GID),
    )
    c.execute(
        "INSERT INTO player_briefings (turn, player_id, npc_key, is_npc, briefing, choices, "
        "selected_action_id, chosen_action_url, personal_title, created_at, game_id) "
        "VALUES (1, NULL, ?, 1, 'npc briefing', ?, 'run', ?, '', '2026-11-01T11:05:00', ?)",
        (f"npc_engineer_{GID}", "[]", COMFY.format(name="action_turn1_npc_engineer_00001_.png", gid=GID), GID),
    )
    c.execute(
        "INSERT INTO player_briefings (turn, player_id, npc_key, is_npc, briefing, choices, "
        "selected_action_id, chosen_action_url, personal_title, created_at, game_id) "
        "VALUES (1, 222, NULL, 0, 'anton briefing', ?, 'run', NULL, 'Turn 1 — Anton', "
        "'2026-11-01T11:06:00', ?)",
        (choices, GID),
    )
    # 222/turn1 (auto) and 111/turn2 (late) submit ONLY via player_action_stats
    # below — proves the game-scoped is_auto / created_at path.
    # Turn 2: manual choice submitted after the deadline → "late".
    c.execute(
        "INSERT INTO player_briefings (turn, player_id, npc_key, is_npc, briefing, choices, "
        "selected_action_id, chosen_action_url, personal_title, created_at, game_id) "
        "VALUES (2, 111, NULL, 0, 'turn 2 briefing', ?, 'scan', NULL, 'Turn 2 — Slach', "
        "'2026-11-01T18:30:00', ?)",
        (choices, GID),
    )
    # player_action_stats mirrors the above plus the is_auto marker; the
    # game-scoped source the analyzer prefers.
    stats = [
        (GID, 111, 1, "scan", "Scan the wreck", "progress", 80, 0, "2026-11-01T12:00:00"),
        (GID, 222, 1, "run", "Run away", "fatal", 80, 1, "2026-11-01T19:00:30"),
        (GID, 111, 2, "scan", "Scan the wreck", "progress", 80, 0, "2026-11-01T23:30:00"),
    ]
    c.executemany(
        "INSERT INTO player_action_stats (game_id, player_id, turn, action_id, action_text, "
        "consequence_kind, hull_integrity, is_auto, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        stats,
    )
    c.execute(
        "INSERT INTO game_images (type, turn, image_url, prompt, created_at, game_id) VALUES "
        "('bridge', 0, ?, '', '2026-11-01T10:55:00', ?)",
        (COMFY.format(name="bridge_00001_.png", gid=GID), GID),
    )
    c.execute(
        "INSERT INTO game_images (type, turn, image_url, prompt, created_at, game_id) VALUES "
        "('scene', 1, ?, '', '2026-11-01T11:01:00', ?)",
        (COMFY.format(name="scene_turn1_00001_.png", gid=GID), GID),
    )
    c.execute(
        "INSERT INTO game_images (type, turn, image_url, prompt, created_at, game_id) VALUES "
        "('character', 1, ?, '', '2026-11-01T11:02:00', ?)",
        (COMFY.format(name="char_turn1_p111_00001_.png", gid=GID), GID),
    )
    c.execute(
        "INSERT INTO game_images (type, turn, image_url, prompt, created_at, game_id) VALUES "
        "('outcome', 1, ?, '', '2026-11-01T17:00:00', ?)",
        (COMFY.format(name="outcome_turn1_00001_.png", gid=GID), GID),
    )
    c.execute("INSERT INTO player_actions (player_id, turn, action_id, choice, created_at) "
              "VALUES (111, 1, 'scan', 'selected', '2026-11-01T12:00:00')")
    events = [
        # (log_file, player, turn, kind, stage, backend, request_at, response_at,
        #  request_text, response_text, image_url, status)
        ("game_%s_player_none_turn0_mission_llm" % GID, "", 0, "mission", "game_concept", "llm",
         "2026-11-01T10:00:10", "2026-11-01T10:00:20", "--- SYSTEM PROMPT ---\ngen mission", "--- RESPONSE CONTENT ---\n{}", None, "ok"),
        ("game_%s_player_111_turn0_avatar_prompt_llm" % GID, "111", 0, "avatar_prompt", "onboarding", "llm",
         "2026-11-01T10:06:00", "2026-11-01T10:06:10", "--- SYSTEM PROMPT ---\navatar prompt sys", "--- RESPONSE CONTENT ---\n{}", None, "ok"),
        ("game_%s_player_111_turn0_avatar_comfyui" % GID, "111", 0, "avatar", "onboarding", "comfyui",
         "2026-11-01T10:07:00", "2026-11-01T10:08:00",
         "Model: qwen\n--- PROMPT ---\navatar of zebra-man\n--- WORKFLOW JSON ---\n{}",
         "URL: %s\nPrompt ID: p1" % COMFY.format(name="avatar_111_00001_.png", gid=GID),
         COMFY.format(name="avatar_111_00001_.png", gid=GID), "ok"),
        ("game_%s_player_%s_turn1_npc_name_engineer_llm" % (GID, f"npc_engineer_{GID}"),
         f"npc_engineer_{GID}", 1, "npc_name_engineer", "npc", "llm",
         "2026-11-01T10:50:10", "2026-11-01T10:50:20", "--- SYSTEM PROMPT ---\nname", "--- RESPONSE CONTENT ---\n{}", None, "ok"),
        ("game_%s_player_none_turn1_player_briefing_llm_notused" % GID, "", 1, "player_briefing", "turn", "llm",
         "2026-11-01T11:03:00", "2026-11-01T11:03:30", "--- SYSTEM PROMPT ---\nbrief", "--- RESPONSE CONTENT ---\n{}", None, "ok"),
        ("game_%s_player_111_turn1_player_briefing_llm" % GID, "111", 1, "player_briefing", "turn", "llm",
         "2026-11-01T11:04:00", "2026-11-01T11:04:30",
         "--- SYSTEM PROMPT ---\nbriefing sys\n--- USER PROMPT ---\nbriefing user zebra",
         "--- RESPONSE CONTENT ---\nbriefing text zebra", None, "ok"),
        ("game_%s_player_111_turn1_player_action_llm" % GID, "111", 1, "player_action", "player_action", "llm",
         "2026-11-01T12:01:00", "2026-11-01T12:01:10", "--- SYSTEM PROMPT ---\naction", "--- RESPONSE CONTENT ---\n{}", None, "ok"),
        ("game_%s_player_111_turn1_player_action_comfyui" % GID, "111", 1, "player_action", "player_action", "comfyui",
         "2026-11-01T12:02:00", "2026-11-01T12:03:00",
         "Model: qwen edit\n--- INSTRUCTION ---\ncharacter at the wreck\n"
         "--- CHARACTER AVATAR ---\n%s/avatar_111_00001_.png\n--- BACKGROUND ---\n%s/turn_bg_turn1_00001_.png\n"
         "--- WORKFLOW JSON ---\n{}" % (GID, GID),
         "URL: %s\nPrompt ID: p2" % COMFY.format(name="action_turn1_p111_00001_.png", gid=GID),
         COMFY.format(name="action_turn1_p111_00001_.png", gid=GID), "ok"),
        ("game_%s_player_none_turn1_combined_outcome_llm" % GID, "", 1, "combined_outcome", "outcome", "llm",
         "2026-11-01T16:00:00", "2026-11-01T16:01:00", "--- SYSTEM PROMPT ---\noutcome", "--- RESPONSE CONTENT ---\n{}", None, "ok"),
        # NPC action panel logged with player_id='none' — the subject NPC is
        # only identifiable from the CHARACTER AVATAR reference.
        ("game_%s_player_none_turn1_npc_action_comfyui" % GID, "none", 1, "npc_action", "player_action", "comfyui",
         "2026-11-01T11:07:00", "2026-11-01T11:08:00",
         "Model: qwen edit\n--- INSTRUCTION ---\nengineer acts\n"
         "--- CHARACTER AVATAR ---\n%s/avatar_engineer_00001_.png\n--- WORKFLOW JSON ---\n{}" % GID,
         "URL: %s\nPrompt ID: p3" % COMFY.format(name="action_turn1_npc_engineer_00001_.png", gid=GID),
         COMFY.format(name="action_turn1_npc_engineer_00001_.png", gid=GID), "ok"),
        # Legacy outcome image logged with a hardcoded turn=0; the real turn
        # survives in the outcome_turn{N} filename.
        ("game_%s_player_none_turn0_outcome_comfyui" % GID, "", 0, "outcome", "outcome", "comfyui",
         "2026-11-01T17:01:00", "2026-11-01T17:02:00", "--- PROMPT ---\naftermath scene",
         "URL: %s\nPrompt ID: p4" % COMFY.format(name="outcome_turn1_00002_.png", gid=GID),
         COMFY.format(name="outcome_turn1_00002_.png", gid=GID), "ok"),
    ]
    for row in events:
        c.execute(
            "INSERT INTO generation_events (log_file, game_id, player_id, turn, kind, stage, backend, "
            "request_at, response_at, request_text, response_text, image_url, status, attempts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (row[0], GID, row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10], row[11]),
        )
    for row in c.execute("SELECT id, request_text, response_text FROM generation_events").fetchall():
        c.execute("INSERT INTO generation_events_fts(rowid, content) VALUES (?, ?)",
                  (row[0], (row[1] or "") + "\n" + (row[2] or "")))
    conn.commit()


def _seed_bot(conn: sqlite3.Connection):
    c = conn.cursor()
    pushes = [
        (111, "onboarding", '{"session": 1}', "sent", 0, None, None, GID, "2026-11-01T10:09:00", "2026-11-01T10:09:05"),
        (111, "briefing", '{"turn": 1}', "sent", 0, None, 1, GID, "2026-11-01T11:10:00", "2026-11-01T11:10:05"),
        (111, "action", '{"turn": 1}', "sent", 0, None, 1, GID, "2026-11-01T12:05:00", "2026-11-01T12:05:03"),
        (111, "outcome", '{"turn": 1}', "sent", 0, None, 1, GID, "2026-11-01T17:05:00", "2026-11-01T17:05:04"),
        (222, "player_death", '{"turn": 1}', "sent", 0, None, 1, GID, "2026-11-01T17:05:00", "2026-11-01T17:05:06"),
    ]
    c.executemany(
        "INSERT INTO push_queue (player_id, push_type, payload, status, retry_count, error, turn, game_id, created_at, sent_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)", pushes,
    )
    c.execute('INSERT INTO "references" VALUES (?,?,?,?)', (222, 111, GID, "2026-11-01T10:20:00"))
    conn.commit()


class TestGraphBuilder(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        gs = Path(self._tmpdir.name) / "game_server.db"
        bot = Path(self._tmpdir.name) / "player_states.db"
        conn = sqlite3.connect(gs)
        conn.executescript(GAME_SERVER_SCHEMA)
        _seed(conn)
        conn.close()
        conn = sqlite3.connect(bot)
        conn.executescript(BOT_SCHEMA)
        _seed_bot(conn)
        conn.close()
        self._old = (config.GAME_SERVER_DB, config.BOT_DB, config.COMFYUI_FILES)
        config.GAME_SERVER_DB = str(gs)
        config.BOT_DB = str(bot)
        config.COMFYUI_FILES = str(Path(self._tmpdir.name))
        import graph_builder

        graph_builder._graph_cache.clear()
        self.graph_builder = graph_builder

    def tearDown(self):
        config.GAME_SERVER_DB, config.BOT_DB, config.COMFYUI_FILES = self._old
        self._tmpdir.cleanup()

    def _nodes_by_type(self, graph, ntype):
        return [n for n in graph["nodes"] if n["type"] == ntype]

    def test_graph_structure(self):
        graph = self.graph_builder.build_game_graph(GID)
        self.assertIsNotNone(graph)
        # Mission is the root; the spine is mission → game → players group →
        # turns group → finale.
        self.assertEqual(graph["root"], f"concept:{GID}")
        for ntype in ("game", "concept", "players", "turns", "player", "npc", "turn",
                      "scene", "dialogue", "brief", "choice", "actionimg", "outcome",
                      "death", "finale"):
            self.assertTrue(self._nodes_by_type(graph, ntype), f"missing nodes of type {ntype}")
        nodes = {n["id"]: n for n in graph["nodes"]}
        self.assertEqual(nodes[f"game:{GID}"]["parent"], f"concept:{GID}")
        self.assertEqual(nodes[f"player:{GID}:111"]["parent"], f"players:{GID}")
        self.assertEqual(nodes[f"npc:{GID}:engineer"]["parent"], f"players:{GID}")
        self.assertEqual(nodes[f"turn:{GID}:1"]["parent"], f"turns:{GID}")
        self.assertEqual(nodes[f"finale:{GID}"]["parent"], f"game:{GID}")
        game_node = nodes[f"game:{GID}"]
        self.assertEqual(game_node["children_count"], 3)  # players group, turns group, finale
        # Chain edges rank the spine top-down.
        chain_pairs = {(e["source"], e["target"]) for e in graph["edges"] if e["etype"] == "chain"}
        self.assertIn((f"players:{GID}", f"turns:{GID}"), chain_pairs)
        self.assertIn((f"turns:{GID}", f"finale:{GID}"), chain_pairs)
        self.assertIn((f"turn:{GID}:2", f"finale:{GID}"), chain_pairs)
        # Turn 1 had an auto-selected action → badge on the turn node.
        self.assertIn("auto", nodes[f"turn:{GID}:1"]["badges"])
        # Dead player badge + invite edge
        dead = next(n for n in graph["nodes"] if n["type"] == "player" and n["meta"]["player_id"] == 222)
        self.assertIn("dead", dead["badges"])
        invites = [e for e in graph["edges"] if e["etype"] == "invite"]
        self.assertEqual(len(invites), 1)
        # Delivered badge on the player briefing
        brief = next(n for n in graph["nodes"] if n["type"] == "brief" and n["meta"]["participant"] == "p111")
        self.assertIn("delivered", brief["badges"])
        self.assertIn("player", brief["badges"])
        # NPC briefing badge
        npc_brief = next(n for n in graph["nodes"] if n["type"] == "brief" and n["meta"]["is_npc"])
        self.assertIn("npc", npc_brief["badges"])
        # Events attached to the right nodes
        self.assertTrue(brief["event_ids"], "briefing LLM event not attached")
        player = next(n for n in graph["nodes"] if n["type"] == "player" and n["meta"]["player_id"] == 111)
        self.assertEqual(len(player["event_ids"]), 2)  # avatar_prompt + avatar comfyui
        outcome = next(n for n in graph["nodes"] if n["type"] == "outcome")
        # combined_outcome LLM + the legacy outcome image that recovered its
        # turn from the outcome_turn{N} filename.
        self.assertEqual(len(outcome["event_ids"]), 2)
        # Orphan NPC comfyui action panel (player_id='none') attaches via the
        # CHARACTER AVATAR reference → the engineer's action image node.
        npc_actionimg = nodes[f"actionimg:{GID}:1:n:engineer"]
        self.assertTrue(npc_actionimg["event_ids"], "orphan npc comfyui event not attached")
        # Death node from dead_crew_members
        death = next(n for n in graph["nodes"] if n["type"] == "death")
        self.assertEqual(death["meta"]["player_id"], 222)
        self.assertIn("delivered", death["badges"])
        # Choice badge carries consequence kind
        choice = next(n for n in graph["nodes"] if n["type"] == "choice" and n["meta"]["participant"] == "p111")
        self.assertIn("progress", choice["badges"])
        # Auto marker from player_action_stats.is_auto (222 has no
        # player_actions row in this fixture).
        choice222 = nodes[f"choice:{GID}:1:p222"]
        self.assertIn("auto", choice222["badges"])
        self.assertIn("auto-selected", choice222["subtitle"])

    def test_reference_edges_from_comfyui_request(self):
        graph = self.graph_builder.build_game_graph(GID)
        ref_edges = [e for e in graph["edges"] if e["etype"] == "ref"]
        sources = {e["source"] for e in ref_edges}
        # avatar reference → player node, turn_bg reference → scene node
        self.assertIn(f"player:{GID}:111", sources)
        self.assertIn(f"scene:{GID}:1", sources)
        targets = {e["target"] for e in ref_edges}
        self.assertIn(f"actionimg:{GID}:1:p111", targets)

    def test_search_resolves_node(self):
        import search

        hits = search.search_events("zebra", GID)
        self.assertTrue(hits)
        brief_hits = [h for h in hits if h["node_id"] == f"brief:{GID}:1:p111"]
        self.assertTrue(brief_hits, "search hit did not resolve to the briefing node")
        self.assertIn("[", brief_hits[0]["snippet"])

    def test_node_detail_chain_and_delivery(self):
        import node_detail

        detail = node_detail.get_node_detail(GID, f"actionimg:{GID}:1:p111")
        self.assertIsNotNone(detail)
        kinds = [(s["backend"], s["kind"]) for s in detail["chain"]]
        self.assertIn(("llm", "player_action"), kinds)
        self.assertIn(("comfyui", "player_action"), kinds)
        comfy_step = next(s for s in detail["chain"] if s["backend"] == "comfyui")
        avatar_section = next(
            sec for sec in comfy_step["request_sections"] if sec["name"] == "CHARACTER AVATAR"
        )
        self.assertTrue(avatar_section["images"][0].startswith("/api/image?path="))
        self.assertTrue(detail["player_view"]["image"])
        self.assertEqual(detail["delivery"][0]["push_type"], "action")
        self.assertEqual(detail["delivery"][0]["status"], "sent")
        upstream_ids = [u["node_id"] for u in detail["upstream"]]
        self.assertIn(f"choice:{GID}:1:p111", upstream_ids)
        self.assertIn(f"player:{GID}:111", upstream_ids)

    def test_brief_player_view_and_delivery(self):
        import node_detail

        detail = node_detail.get_node_detail(GID, f"brief:{GID}:1:p111")
        self.assertEqual(len(detail["player_view"]["choices"]), 2)
        self.assertIn("zebra", detail["player_view"]["text"])
        self.assertEqual(detail["delivery"][0]["push_type"], "briefing")
        upstream_ids = [u["node_id"] for u in detail["upstream"]]
        self.assertIn(f"scene:{GID}:1", upstream_ids)

    def test_turn_actions_detail(self):
        import node_detail

        detail = node_detail.get_node_detail(GID, f"turn:{GID}:1")
        self.assertIsNotNone(detail)
        actions = detail["actions"]
        self.assertEqual(actions["deadline"], "2026-11-01T19:00:00+00:00")
        self.assertEqual(actions["outcome_node_id"], f"outcome:{GID}:1")
        by_participant = {a["participant"]: a for a in actions["actions"]}
        slach = by_participant["Slach"]
        self.assertEqual(slach["timing"], "on-time")
        self.assertEqual(slach["chosen_kind"], "selected")
        self.assertEqual(slach["chosen"]["id"], "scan")
        self.assertEqual(len(slach["variants"]), 2)
        self.assertEqual(slach["brief_node_id"], f"brief:{GID}:1:p111")
        self.assertEqual(slach["choice_node_id"], f"choice:{GID}:1:p111")
        anton = by_participant["Anton"]
        self.assertEqual(anton["timing"], "auto")
        self.assertEqual(anton["chosen_kind"], "auto_selected")
        self.assertEqual(anton["chosen"]["id"], "run")
        npc = next(a for a in actions["actions"] if a["is_npc"])
        self.assertEqual(npc["timing"], "npc")
        self.assertEqual(npc["participant"], "Mika")
        # Non-turn nodes carry no actions payload.
        brief_detail = node_detail.get_node_detail(GID, f"brief:{GID}:1:p111")
        self.assertIsNone(brief_detail["actions"])
        # Manual submission after the deadline → "late" (naive created_at vs
        # tz-aware deadline).
        turn2 = node_detail.get_node_detail(GID, f"turn:{GID}:2")
        self.assertEqual(turn2["actions"]["actions"][0]["timing"], "late")

    def test_list_games(self):
        games = self.graph_builder.list_games()
        self.assertEqual(len(games), 1)
        game = games[0]
        self.assertEqual(game["game_id"], GID)
        self.assertEqual(game["players"]["total"], 2)
        self.assertEqual(game["players"]["dead"], 1)
        self.assertEqual(game["npcs"], 1)
        self.assertEqual(game["event_count"], 11)
        self.assertEqual(game["mission"]["archetype"], "escort")


if __name__ == "__main__":
    unittest.main()
