"""Evaluate the REAL production call path on the optimizer's datasets.

run_optimize.py scores dspy.Predict(signature) — dspy's own prompt
formatting, no response_format. That is where demos are compiled, but it is
NOT the request the game server sends. This script closes the loop: it
builds prompts with the real prompts.py builders, sends them through the
same AsyncOpenAI + response_format=json_schema call the runtime makes
(same sampling params via llm_config), and scores the parsed results with
the same metric. A compiled artifact can be verified end-to-end here
BEFORE it is pasted into prompts.py:

    python eval_prod_path.py --use-case npc_choice --language ru --n 20
    python eval_prod_path.py --use-case npc_choice --language ru --n 20 \
        --demos-from compiled/npc_choice_ru.json

--demos-from builds the demo block exactly as export_demos.py would,
installs it into the prompts.py demo constant for the run, and restores the
previous value afterwards. Failure modes the dspy path cannot see (bracket
echo around action_id, json_schema refusal, fallback plain-text parsing)
score 0.0 here — that is the point.
"""

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import dspy  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402

from export_demos import DEMO_CONSTANT, build_demo_block  # noqa: E402
from game_server import NPC_CHOICE_SCHEMA, _llm_extra_body  # noqa: E402
from language import LANGUAGE_EN, LANGUAGE_RU  # noqa: E402
from llm import make_judge_lm  # noqa: E402
from llm_config import resolve_llm_params  # noqa: E402
from metrics import METRIC_FACTORIES  # noqa: E402
from npc_dataset import build_examples as build_npc_examples  # noqa: E402
from outcome_dataset import build_examples as build_outcome_examples  # noqa: E402
from prompts import (  # noqa: E402
    COMBINED_OUTCOME_SCHEMA,
    build_combined_outcome_prompts,
    build_npc_decision_prompts,
)
import prompts as prompts_module  # noqa: E402

logger = logging.getLogger("eval_prod_path")


def build_dataset(use_case: str, language: str, n: int, seed: int) -> list[dspy.Example]:
    if use_case == "npc_choice":
        return build_npc_examples(language, n, seed=seed)
    return build_outcome_examples(language, n, seed=seed)


def build_prompts(use_case: str, language: str, example: dspy.Example) -> tuple[str, str]:
    if use_case == "npc_choice":
        loyalty = int(str(example.loyalty).split("/")[0])
        return build_npc_decision_prompts(
            language,
            example.npc_name,
            example.npc_role,
            example.traits,
            example.choices_text,
            loyalty=loyalty,
            use_vs=False,
            vs_k=0,
        )
    return build_combined_outcome_prompts(
        language,
        setting=example.setting,
        conflict=example.conflict,
        narrative=example.narrative,
        previous_summary=example.previous_summary,
        mission_text=example.mission_text,
        ship_status_text=example.ship_status_text,
        decisions_text=example.decisions_text,
        roster_text=example.roster_text,
        use_vs=False,
        vs_k=0,
    )


RESPONSE_SCHEMAS = {
    "npc_choice": NPC_CHOICE_SCHEMA,
    "combined_outcome": COMBINED_OUTCOME_SCHEMA,
}


def to_prediction(use_case: str, data: dict) -> dspy.Prediction:
    if use_case == "npc_choice":
        return dspy.Prediction(
            action_id=str(data.get("action_id", "")),
            rationale=str(data.get("rationale", "")),
        )
    return dspy.Prediction(outcome_json=json.dumps(data, ensure_ascii=False))


async def call_prod(client: AsyncOpenAI, model: str, use_case: str,
                    system: str, user: str) -> dict:
    """One runtime-shaped LLM call: json_schema first, plain-text fallback
    with a JSON-only instruction on failure (mirrors GameServer._call_llm)."""
    params = resolve_llm_params(model, use_case, vs_enabled=False)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    extra_body = _llm_extra_body(params)
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=params.temperature,
        max_tokens=params.max_tokens,
        response_format=RESPONSE_SCHEMAS[use_case],
        extra_body=extra_body,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"empty completion, finish_reason={response.choices[0].finish_reason}")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.warning("structured output not valid JSON, falling back to plain extraction")
        messages[1]["content"] = user + "\n\nIMPORTANT: Return ONLY valid JSON. No markdown, no code blocks, no explanation. Pure JSON only."
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=params.temperature,
            max_tokens=params.max_tokens,
            extra_body=extra_body,
        )
        content = response.choices[0].message.content or ""
        start, end = content.find("{"), content.rfind("}")
        return json.loads(content[start:end + 1])


def install_demos(use_case: str, language: str, artifacts_path: str) -> str:
    """Install the compiled demo block into the prompts.py constant for this
    run (the builder picks it up on its next call). Returns the previous value."""
    with open(artifacts_path, encoding="utf-8") as f:
        payload = json.load(f)
    if payload["use_case"] != use_case:
        raise SystemExit(f"artifact {artifacts_path} is for {payload['use_case']}, not {use_case}")
    block = build_demo_block(use_case, language, payload.get("demos", []))
    constant = DEMO_CONSTANT[use_case]
    demo_dict = getattr(prompts_module, constant)
    previous = demo_dict[language]
    demo_dict[language] = block
    return previous


async def run(args) -> None:
    language = LANGUAGE_RU if args.language == "ru" else LANGUAGE_EN
    examples = build_dataset(args.use_case, language, args.n, args.seed)

    previous_demo = None
    if args.demos_from:
        previous_demo = install_demos(args.use_case, language, args.demos_from)
        logger.info("Installed demo block from %s (%d chars)", args.demos_from, len(previous_demo or ""))

    client = AsyncOpenAI(
        base_url=os.getenv("LLM_URL", "http://localhost:8090/v1"),
        api_key=os.getenv("LLM_API_KEY", "placeholder-key-for-llama-cpp"),
    )
    model = os.getenv("LLM_MODEL", "unsloth/Qwen3.5-27B")
    metric = METRIC_FACTORIES[args.use_case](make_judge_lm())

    scores = []
    try:
        for i, example in enumerate(examples):
            system, user = build_prompts(args.use_case, language, example)
            try:
                data = await call_prod(client, model, args.use_case, system, user)
                pred = to_prediction(args.use_case, data)
            except Exception:
                logger.warning("case %d failed on the prod path", i, exc_info=True)
                scores.append(0.0)
                continue
            score = metric(example, pred)
            scores.append(score)
            logger.info("case %d/%d %s -> %.2f", i + 1, len(examples),
                        getattr(example, "case_id", getattr(example, "npc_name", "?")), score)
    finally:
        if previous_demo is not None:
            demo_dict = getattr(prompts_module, DEMO_CONSTANT[args.use_case])
            demo_dict[language] = previous_demo
            logger.info("Restored the previous demo constant value")

    average = sum(scores) / max(1, len(scores))
    print(f"\nprod-path score [{args.use_case}/{args.language}, n={len(scores)}, "
          f"model={model}, demos={args.demos_from or 'current prompts.py'}]: {average:.3f}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Evaluate the real production call path")
    parser.add_argument("--use-case", required=True, choices=["npc_choice", "combined_outcome"])
    parser.add_argument("--language", default="ru", choices=["ru", "en"])
    parser.add_argument("--n", type=int, default=20, help="Number of examples to evaluate")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--demos-from", default="", help="compiled/<use_case>_<lang>.json to install for this run")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
