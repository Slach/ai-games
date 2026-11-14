"""Control baseline: run the CURRENT generative NPC-choice path over the same
recorded decisions and measure how often it reproduces them.

Uses the exact runtime prompt builder (prompts.build_npc_decision_prompts) and
response schema (game_server.NPC_CHOICE_SCHEMA) against the loaded generative
model, sampling with the runtime npc_choice parameters (temperature=0.8).

Usage (from game-server/):
    ../.venv/bin/python tools/benchmark_generative_baseline.py \
        [--model bartowski/Altworld_Hemmingway-1] [--workers 4] [--limit N]
"""

import argparse
import collections
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from game_server import NPC_CHOICE_SCHEMA  # noqa: E402
from prompts import build_npc_decision_prompts  # noqa: E402
from benchmark_decision_model import fetch_dataset  # noqa: E402

DEFAULT_BASE_URL = "http://127.0.0.1:8090/v1/chat/completions"
DEFAULT_MODEL = "bartowski/Altworld_Hemmingway-1"
TEMPERATURE = 0.8  # llm_config "npc_choice" non_vs branch
MAX_TOKENS = 512


def call_chat(base_url: str, model: str, system: str, user: str) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "response_format": NPC_CHOICE_SCHEMA,
        # Same as GameServer._call_llm: npc_choice runs with thinking off.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300.0) as resp:
        body = json.loads(resp.read())
    return json.loads(body["choices"][0]["message"]["content"])


def evaluate(record: dict, base_url: str, model: str) -> dict:
    choices_text = "\n".join(f"  [{c['id']}] {c['text']}" for c in record["choices"])
    system, user = build_npc_decision_prompts(
        "en",  # decision state stays English; prompts are per-game language
        record["npc_name"],
        record["role"],
        record["traits"],
        choices_text,
        loyalty=record["loyalty"],
        use_vs=False,
        vs_k=1,
    )
    if record["language"] == "ru":
        # The runtime prompt language follows the game language.
        system, user = build_npc_decision_prompts(
            "ru", record["npc_name"], record["role"], record["traits"],
            choices_text, loyalty=record["loyalty"], use_vs=False, vs_k=1,
        )
    parsed = call_chat(base_url, model, system, user)
    action_id = parsed.get("action_id", "")
    return {
        "briefing_id": record["briefing_id"],
        "game_id": record["game_id"],
        "turn": record["turn"],
        "npc_key": record["npc_key"],
        "language": record["language"],
        "loyalty": record["loyalty"],
        "recorded_id": record["recorded_id"],
        "model_choice": action_id,
        "match": action_id == record["recorded_id"],
        "rationale": parsed.get("rationale", ""),
        "invalid_choice": action_id not in {c["id"] for c in record["choices"]},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path,
                        default=Path(__file__).resolve().parent.parent / "game_server.db")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/decision_benchmark"))
    args = parser.parse_args()

    dataset = fetch_dataset(args.db)
    if args.limit:
        dataset = dataset[: args.limit]
    print(f"Dataset: {len(dataset)} NPC decisions, model {args.model}, "
          f"{args.workers} workers")

    results = []
    failures = 0
    t0 = time.time()

    def work(record: dict):
        try:
            return evaluate(record, args.base_url, args.model)
        except Exception as e:  # noqa: BLE001 — benchmark must survive per-record errors
            return {"error": str(e), "briefing_id": record["briefing_id"]}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, res in enumerate(pool.map(work, dataset), 1):
            if "error" in res:
                failures += 1
                print(f"[{i}/{len(dataset)}] FAILED briefing {res['briefing_id']}: {res['error']}")
                continue
            results.append(res)
            if i % 20 == 0 or i == len(dataset):
                top1 = sum(r["match"] for r in results)
                print(f"[{i}/{len(dataset)}] top-1 so far: {top1}/{len(results)} "
                      f"({top1 / len(results):.1%})  elapsed {time.time() - t0:.0f}s")

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = args.model.replace("/", "_")
    out_path = out_dir / f"generative_{slug}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\nDetailed results: {out_path}")

    if not results:
        print("No successful evaluations.")
        return 1

    n = len(results)
    top1 = sum(r["match"] for r in results)
    invalid = sum(1 for r in results if r["invalid_choice"])
    print(f"\n=== Summary: generative baseline {args.model} on {n} decisions "
          f"({failures} failed calls) ===")
    print(f"top-1 accuracy (argmax == recorded):  {top1}/{n} = {top1 / n:.1%}")
    print(f"invalid action_id responses:          {invalid}")

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
