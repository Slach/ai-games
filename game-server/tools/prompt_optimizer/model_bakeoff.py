"""Txt2img model bake-off: generate a fixed prompt set with every model in
comfyui_config.MODELS, score each image with the VL judge (Qwen3.8 mmproj),
and print a ranking table to pick COMFYUI_TXT2IMG_MODEL.

Runs against ComfyUI at COMFYUI_URL (default http://localhost:8188) and the
llama.cpp judge at JUDGE_MODEL/LLM_MODEL. GPU-time: ~1-3 min per model.
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
from llm import make_judge_lm  # noqa: E402
from metrics import _data_url  # noqa: E402
from signatures import ImageBakeoffJudge  # noqa: E402

logger = logging.getLogger("model_bakeoff")

# Representative game-style txt2img prompts (English, as the runtime sends).
PROMPTS = [
    ("bg_bridge", "Empty starship bridge interior, cinematic sci-fi, captain's chair and tactical consoles, cool blue ambient lighting, no people, 4K quality"),
    ("bg_planet", "Alien planet surface at dusk, crystalline formations glowing faintly, twin moons on the horizon, dense violet atmosphere, cinematic wide shot, no people"),
    ("avatar_human", "Portrait of a stern human starship captain, 40s, short greying hair, dark uniform with silver insignia, starfield background, cinematic lighting"),
    ("avatar_nonhumanoid", "A floating cluster of luminous crystals forming a sentient being, no limbs, no face, pulsing inner light, dark engineering bay background, cinematic sci-fi concept art"),
    ("bridge_crew", "Cinematic starship bridge scene with five recognizable crew members in action at their stations: a human captain in the command chair, a tall insectoid navigator at the helm, a reptilian science officer by the holographic display, a cyborg engineer at a console and a floating energy being near the viewport, viewpoint at crew level, space opera lighting, no text"),
    ("exterior_ship", "Sleek exploration starship in deep space approaching a swirling amber nebula, dramatic rim lighting, epic scale, cinematic sci-fi"),
    ("splash_epic", "Epic space opera vista: fleet silhouettes against a dying red star, planetary rings cutting the frame, sense of doom and grandeur, cinematic"),
]


def parse_score(raw) -> float | None:
    import re

    match = re.search(r"\d+(?:\.\d+)?", str(raw))
    return float(match.group()) if match else None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Txt2img bake-off across comfyui_config.MODELS")
    parser.add_argument("--out", default="", help="save results JSON here")
    args = parser.parse_args()

    judge_lm = make_judge_lm()
    judge = dspy.Predict(ImageBakeoffJudge)
    generator = ImageGenerator()

    results = {}
    for model_key in comfyui_config.MODELS:
        # resolve_txt2img_model falls back to the module global when the kind
        # has no override — point it at the candidate.
        comfyui_config.DEFAULT_TXT2IMG_MODEL = model_key
        label = comfyui_config.MODELS[model_key].label
        rows = []
        for prompt_id, prompt in PROMPTS:
            t0 = time.time()
            url = asyncio.run(generator.generate_image(
                prompt=prompt,
                filename_prefix=f"bakeoff_{model_key}_{prompt_id}",
                width=1024,
                height=1024,
                max_retries=2,
                game_id="bakeoff",
                player_id=None,
                turn=0,
                kind=None,
            ))
            dt = time.time() - t0
            if not url:
                logger.warning("%s/%s: generation failed", model_key, prompt_id)
                rows.append({"prompt": prompt_id, "score": None, "seconds": round(dt, 1), "feedback": "GENERATION FAILED"})
                continue
            filename = ImageGenerator._extract_filename_from_url(url)
            try:
                with dspy.context(lm=judge_lm):
                    verdict = judge(prompt=prompt, image=dspy.Image(url=_data_url(filename)))
                score = parse_score(verdict.score)
            except Exception:
                logger.warning("%s/%s: judge failed", model_key, prompt_id, exc_info=True)
                score = None
                verdict = type("V", (), {"feedback": "JUDGE FAILED"})()
            rows.append({"prompt": prompt_id, "score": score, "seconds": round(dt, 1),
                         "feedback": str(verdict.feedback)[:160]})
            logger.info("%s/%s -> %s (%.0fs)", model_key, prompt_id, score, dt)
        scored = [r["score"] for r in rows if r["score"] is not None]
        results[model_key] = {
            "label": label,
            "avg_score": round(sum(scored) / len(scored), 2) if scored else None,
            "avg_seconds": round(sum(r["seconds"] for r in rows) / len(rows), 1),
            "rows": rows,
        }

    print()
    print("════════════ TXT2IMG BAKE-OFF ════════════")
    print()
    print(f"{'model':<24} {'ср. балл':>9} {'ср. время':>10}  промпты")
    print("─" * 78)
    ranked = sorted(results.items(), key=lambda kv: (kv[1]["avg_score"] is None, -(kv[1]["avg_score"] or 0)))
    for model_key, res in ranked:
        per_prompt = " ".join(
            f"{r['prompt']}={r['score'] if r['score'] is not None else 'FAIL'}" for r in res["rows"]
        )
        score_str = f"{res['avg_score']}" if res["avg_score"] is not None else "—"
        print(f"{model_key:<24} {score_str:>9} {res['avg_seconds']:>9}s  {per_prompt}")
    print()
    best = ranked[0]
    print(f"Победитель: {best[0]} ({best[1]['label']}) — средний балл {best[1]['avg_score']}")
    print(f"Прописать в .env: COMFYUI_TXT2IMG_MODEL={best[0]}")
    print()

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.info("Results saved: %s", args.out)


if __name__ == "__main__":
    main()
