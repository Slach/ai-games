"""Benchmark decision models (/v1/systemone) against recorded NPC choices.

Reads every NPC decision from game_server.db (player_briefings where is_npc=1),
sends the same inputs the runtime prompt sees (persona, loyalty, stripped
choices) to the decision model, and compares the model's ranking against the
action that was actually recorded.

Usage (from game-server/):
    ../.venv/bin/python tools/benchmark_decision_model.py \
        --model ggml-org/lev [--limit 50] [--base-url http://127.0.0.1:8090]

Writes per-record results to logs/decision_benchmark/<model>.json and prints
a summary (top-1 / top-2 accuracy, mean rank of the recorded choice).
"""

import argparse
import collections
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from game_rules import loyalty_band  # noqa: E402

DEFAULT_DB = Path(__file__).resolve().parent.parent / "game_server.db"
DEFAULT_BASE_URL = "http://127.0.0.1:8090"

# Mirrors _NPC_LOYALTY_RULES_EN in prompts.py (decision state is always
# presented to the model in English, regardless of game language).
LOYALTY_RULES_EN = {
    "steadfast": "you act in the best interest of the mission and command",
    "uneasy": "you keep working, but you doubt the orders and the command",
    "on_edge": "your priority is self-preservation — you avoid risk and won't risk your life for the mission",
    "mutinous": "you openly disobey orders, sabotage them, and are preparing to mutiny against command",
}

INSTRUCTIONS = (
    "Which action would this crew member choose based on their "
    "personality, role, and loyalty? They see only the action "
    "descriptions, no consequences."
)


def is_ru(text: str) -> bool:
    return any("\u0400" <= ch <= "\u04FF" for ch in text)


def fetch_dataset(db_path: Path) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT pb.id, pb.game_id, pb.turn, pb.npc_key, pb.choices,
               pb.selected_action_id, pb.briefing,
               np.npc_name, np.role, np.personality_traits, np.loyalty
        FROM player_briefings pb
        LEFT JOIN npc_profiles np ON np.npc_key = pb.npc_key
        WHERE pb.is_npc = 1
          AND pb.selected_action_id IS NOT NULL
          AND pb.selected_action_id != ''
        ORDER BY pb.id
        """
    ).fetchall()
    con.close()

    dataset = []
    for r in rows:
        # Same stripping as generate_npc_choice: NPC never sees consequences.
        choices = [
            {"id": c.get("id", ""), "text": c.get("text", "")}
            for c in json.loads(r["choices"])
            if isinstance(c, dict) and c.get("id")
        ]
        if not choices or r["selected_action_id"] not in {c["id"] for c in choices}:
            continue
        dataset.append(
            {
                "briefing_id": r["id"],
                "game_id": r["game_id"],
                "turn": r["turn"],
                "npc_key": r["npc_key"],
                "npc_name": r["npc_name"] or r["npc_key"],
                "role": r["role"] or "",
                "traits": r["personality_traits"] or "[]",
                "loyalty": r["loyalty"] if r["loyalty"] is not None else 70,
                "choices": choices,
                "recorded_id": r["selected_action_id"],
                "language": "ru" if is_ru(r["briefing"] or "") else "en",
            }
        )
    return dataset


def build_payload(record: dict, model: str, state_format: str = "json") -> dict:
    traits = record["traits"]
    if isinstance(traits, str):
        try:
            traits = ", ".join(json.loads(traits))
        except json.JSONDecodeError:
            traits = traits
    band = loyalty_band(record["loyalty"])
    loyalty_rule = LOYALTY_RULES_EN[band]
    if state_format == "prose":
        state = (
            f"Crew member: {record['npc_name']}, role: {record['role']}. "
            f"Personality: {traits}. Loyalty to command: {record['loyalty']}/100 — {loyalty_rule}. "
            "The current situation on the ship requires a decision."
        )
    else:
        state = {
            "npc_name": record["npc_name"],
            "role": record["role"],
            "personality_traits": traits,
            "loyalty": record["loyalty"],
            "loyalty_rule": loyalty_rule,
            "situation": "The current situation on the ship requires a decision.",
        }
    return {
        "model": model,
        "state": state,
        "questions": {
            "action": {
                "type": "choice",
                "instructions": INSTRUCTIONS,
                "criteria": {c["id"]: c["text"] for c in record["choices"]},
            }
        },
    }


def call_systemone(base_url: str, payload: dict, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/systemone",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default="ggml-org/lev")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate only first N records")
    parser.add_argument("--state-format", choices=["json", "prose"], default="json")
    # logs/ is owned by the containers; benchmark output goes to /tmp by default.
    parser.add_argument(
        "--out-dir", type=Path, default=Path("/tmp/decision_benchmark"),
    )
    args = parser.parse_args()

    dataset = fetch_dataset(args.db)
    if args.limit:
        dataset = dataset[: args.limit]
    print(f"Dataset: {len(dataset)} NPC decisions from {args.db}")

    results = []
    failures = 0
    t0 = time.time()
    for i, record in enumerate(dataset, 1):
        payload = build_payload(record, args.model, args.state_format)
        try:
            response = call_systemone(args.base_url, payload)
            answer = response["answers"]["action"]
        except Exception as e:  # noqa: BLE001 — benchmark must survive per-record errors
            failures += 1
            print(f"[{i}/{len(dataset)}] FAILED {record['game_id']} t{record['turn']} "
                  f"{record['npc_key']}: {e}")
            continue
        probs = answer["probabilities"]
        ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
        ids_ranked = [k for k, _ in ranked]
        rank = ids_ranked.index(record["recorded_id"]) + 1
        results.append(
            {
                "briefing_id": record["briefing_id"],
                "game_id": record["game_id"],
                "turn": record["turn"],
                "npc_key": record["npc_key"],
                "language": record["language"],
                "loyalty": record["loyalty"],
                "recorded_id": record["recorded_id"],
                "model_choice": answer["choice"],
                "match": answer["choice"] == record["recorded_id"],
                "rank_of_recorded": rank,
                "prob_of_recorded": probs.get(record["recorded_id"], 0.0),
                "confidence": answer.get("confidence"),
                "probabilities": probs,
                "input_tokens": answer.get("usage", {}).get("input_tokens"),
            }
        )
        if i % 20 == 0 or i == len(dataset):
            top1 = sum(r["match"] for r in results)
            print(f"[{i}/{len(dataset)}] top-1 so far: {top1}/{len(results)} "
                  f"({top1 / len(results):.1%})  elapsed {time.time() - t0:.0f}s")

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.state_format == "json" else f"_{args.state_format}"
    out_path = out_dir / f"{args.model.replace('/', '_')}{suffix}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\nDetailed results: {out_path}")

    if not results:
        print("No successful evaluations.")
        return 1

    n = len(results)
    top1 = sum(r["match"] for r in results)
    top2 = sum(1 for r in results if r["rank_of_recorded"] <= 2)
    mean_rank = sum(r["rank_of_recorded"] for r in results) / n
    mean_prob = sum(r["prob_of_recorded"] for r in results) / n
    print(f"\n=== Summary: {args.model} on {n} NPC decisions "
          f"({failures} failed calls) ===")
    print(f"top-1 accuracy (model argmax == recorded): {top1}/{n} = {top1 / n:.1%}")
    print(f"top-2 (recorded in 2 best):               {top2}/{n} = {top2 / n:.1%}")
    print(f"mean rank of recorded choice:             {mean_rank:.2f}")
    print(f"mean probability of recorded choice:      {mean_prob:.3f}")

    by_lang = collections.defaultdict(lambda: [0, 0])
    for r in results:
        by_lang[r["language"]][1] += 1
        if r["match"]:
            by_lang[r["language"]][0] += 1
    for lang, (hit, total) in sorted(by_lang.items()):
        print(f"  {lang}: top-1 {hit}/{total} = {hit / total:.1%}")

    by_game = collections.defaultdict(lambda: [0, 0])
    for r in results:
        by_game[r["game_id"]][1] += 1
        if r["match"]:
            by_game[r["game_id"]][0] += 1
    for game, (hit, total) in sorted(by_game.items(), key=lambda kv: -kv[1][1]):
        print(f"  {game}: {hit}/{total} = {hit / total:.1%}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
