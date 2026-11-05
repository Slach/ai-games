"""Edit model bake-off: place each scene_instruction dataset character into
their turn background with the models in comfyui_config.EDIT_MODELS, score
identity preservation + instruction following with the VL judge (Qwen3.8
mmproj), and print a ranking table to pick COMFYUI_EDIT_MODEL.

Instructions are written once by the runtime LLM with the production
prompts.build_scene_instruction_* builders (cached to --instructions JSON)
and shared verbatim by every model — picture-number conventions are
adapted inside each builder. Runs against ComfyUI at COMFYUI_URL (default
http://localhost:8188) and the llama.cpp LLM/judge.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("COMFYUI_URL", "http://localhost:8188")
os.environ.setdefault("COMFYUI_IMAGE_CONCURRENCY", "1")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import time  # noqa: E402

import dspy  # noqa: E402

import comfyui_config  # noqa: E402
from image_generator import ImageGenerator  # noqa: E402
from language import LANGUAGE_RU  # noqa: E402
from llm import make_judge_lm, make_lm  # noqa: E402
from metrics import _data_url, _view_url  # noqa: E402
from prompts import build_scene_instruction_system, build_scene_instruction_user  # noqa: E402
from signatures import ImageEditJudge  # noqa: E402

logger = logging.getLogger("edit_bakeoff")

DATASET = os.path.join(os.path.dirname(__file__), "datasets", "scene_instruction_ru.json")


def parse_score(raw) -> float | None:
    import re

    match = re.search(r"\d+(?:\.\d+)?", str(raw))
    return float(match.group()) if match else None


def build_instructions(cache_path: str) -> dict[str, str]:
    """Write one English edit instruction per case with the runtime LLM.

    Mirrors the production scene-instruction call (same system/user prompt
    builders, thinking off) at low temperature, then caches case_id ->
    instruction so reruns and model additions reuse identical instructions.
    """
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        logger.info("Loaded %d cached instructions from %s", len(cached), cache_path)
        return cached

    with open(DATASET, encoding="utf-8") as f:
        cases = json.load(f)
    system = build_scene_instruction_system(LANGUAGE_RU)
    lm = make_lm(temperature=0.3, max_tokens=1024, enable_thinking=False)
    instructions = {}
    for case in cases:
        user = build_scene_instruction_user(
            LANGUAGE_RU,
            action_text=case["action_text"],
            species_desc=case["species_desc"],
            scene_description="",
            scene_context=case["scene_context"],
            species_category=case["species_category"],
        )
        response = lm(messages=[{"role": "system", "content": system},
                                {"role": "user", "content": user}])
        text = response[0] if isinstance(response, list) else str(response)
        instructions[case["case_id"]] = text.strip().strip('"')
        logger.info("instruction %s: %s", case["case_id"], instructions[case["case_id"]][:100])
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(instructions, f, ensure_ascii=False, indent=2)
    logger.info("Cached %d instructions to %s", len(instructions), cache_path)
    return instructions


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Edit bake-off across comfyui_config.EDIT_MODELS")
    parser.add_argument("--models", default="", help="comma-separated EDIT_MODELS keys (default: all)")
    parser.add_argument("--out", default="", help="save results JSON here")
    parser.add_argument("--instructions", default="", help="instruction cache JSON (default: compiled/edit_bakeoff_instructions_ru.json)")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    args = parser.parse_args()

    model_keys = [k.strip() for k in args.models.split(",") if k.strip()] or list(comfyui_config.EDIT_MODELS)
    unknown = [k for k in model_keys if k not in comfyui_config.EDIT_MODELS]
    if unknown:
        parser.error(f"unknown edit model keys: {', '.join(unknown)} (known: {', '.join(comfyui_config.EDIT_MODELS)})")

    cache_path = args.instructions or os.path.join(os.path.dirname(__file__), "compiled", "edit_bakeoff_instructions_ru.json")
    instructions = build_instructions(cache_path)

    with open(DATASET, encoding="utf-8") as f:
        cases = json.load(f)

    judge_lm = make_judge_lm()
    judge = dspy.Predict(ImageEditJudge)
    generator = ImageGenerator()

    results = {}
    for model_key in model_keys:
        comfyui_config.DEFAULT_EDIT_MODEL = model_key
        label = comfyui_config.EDIT_MODELS[model_key].label
        rows = []
        for case in cases:
            case_id = case["case_id"]
            instruction = instructions.get(case_id, "")
            t0 = time.time()
            url = asyncio.run(generator.generate_character_in_scene(
                instruction_prompt=instruction,
                character_avatar_url=_view_url(case["avatar_filename"]),
                background_url=_view_url(case["background_filename"]),
                character_description=case["species_desc"],
                filename_prefix=f"editbakeoff/{model_key}_{case_id}",
                width=args.width,
                height=args.height,
                game_id="bakeoff",
                player_id=None,
                turn=0,
                kind="editbakeoff",
            ))
            dt = time.time() - t0
            if not url:
                logger.warning("%s/%s: generation failed", model_key, case_id)
                rows.append({"case": case_id, "score": None, "seconds": round(dt, 1), "feedback": "GENERATION FAILED"})
                continue
            filename = ImageGenerator._extract_filename_from_url(url)
            try:
                with dspy.context(lm=judge_lm):
                    verdict = judge(
                        avatar=dspy.Image(url=_data_url(case["avatar_filename"])),
                        instruction=instruction,
                        image=dspy.Image(url=_data_url(filename)),
                    )
                score = parse_score(verdict.score)
            except Exception:
                logger.warning("%s/%s: judge failed", model_key, case_id, exc_info=True)
                score = None
                verdict = type("V", (), {"feedback": "JUDGE FAILED"})()
            rows.append({"case": case_id, "score": score, "seconds": round(dt, 1),
                         "feedback": str(verdict.feedback)[:160]})
            logger.info("%s/%s -> %s (%.0fs)", model_key, case_id, score, dt)
        scored = [r["score"] for r in rows if r["score"] is not None]
        results[model_key] = {
            "label": label,
            "avg_score": round(sum(scored) / len(scored), 1) if scored else None,
            "avg_seconds": round(sum(r["seconds"] for r in rows) / len(rows), 1),
            "rows": rows,
        }

    print()
    print("════════════ EDIT BAKE-OFF ════════════")
    print()
    print(f"{'model':<28} {'ср. балл':>9} {'ср. время':>10}  кейсы")
    print("─" * 90)
    ranked = sorted(results.items(), key=lambda kv: (kv[1]["avg_score"] is None, -(kv[1]["avg_score"] or 0)))
    for model_key, res in ranked:
        per_case = " ".join(
            f"{r['case']}={r['score'] if r['score'] is not None else 'FAIL'}" for r in res["rows"]
        )
        score_str = f"{res['avg_score']}" if res["avg_score"] is not None else "—"
        print(f"{model_key:<28} {score_str:>9} {res['avg_seconds']:>9}s  {per_case}")
    print()
    best = ranked[0]
    print(f"Победитель: {best[0]} ({best[1]['label']}) — средний балл {best[1]['avg_score']}")
    print(f"Прописать в .env: COMFYUI_EDIT_MODEL={best[0]}")
    print()

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.info("Results saved: %s", args.out)


if __name__ == "__main__":
    main()
