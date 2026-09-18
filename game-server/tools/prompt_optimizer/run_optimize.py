"""Offline prompt optimizer runner.

Pipeline: build/load dataset -> baseline evaluate -> compile with a dspy
optimizer (BootstrapFewShot by default, GEPA optionally) -> re-evaluate ->
save compiled artifacts (instructions + demos) as JSON for export_demos.py.

Runs on the host against llama.cpp (LLM_URL, default http://localhost:8090/v1)
and, for scene_instruction, ComfyUI at localhost:8188. Nothing here is part
of the runtime game-server image.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# image_generator reads COMFYUI_URL at import time; the docker name
# comfyui:8188 only resolves inside the docker network, so default to the
# host-exposed port before any game-server module gets imported.
os.environ.setdefault("COMFYUI_URL", "http://localhost:8188")
os.environ.setdefault("COMFYUI_IMAGE_CONCURRENCY", "2")
# The optimizer shares ComfyUI with the live bot: under evening load several
# jobs queue ahead and a 180s wait times out, re-queues a retry and deepens
# the queue — every such case wastes the student tokens that wrote the prompt.
os.environ.setdefault("COMFYUI_WAIT_TIMEOUT", "600")

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402

import dspy  # noqa: E402
from dspy.teleprompt import GEPA, BootstrapFewShot  # noqa: E402

from language import LANGUAGE_EN, LANGUAGE_RU  # noqa: E402
from llm import make_judge_lm, make_lm  # noqa: E402
from metrics import METRIC_FACTORIES  # noqa: E402
from npc_dataset import build_examples, load_scenarios  # noqa: E402
from outcome_dataset import build_examples as build_outcome_examples  # noqa: E402
from signatures import SIGNATURES  # noqa: E402

logger = logging.getLogger("run_optimize")

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
COMPILED_DIR = os.path.join(TOOL_DIR, "compiled")
DATASETS_DIR = os.path.join(TOOL_DIR, "datasets")

STUDENT_PARAMS = {
    "npc_choice": {"temperature": 0.8, "max_tokens": 1024},
    "scene_instruction": {"temperature": 0.7, "max_tokens": 1024},
    "combined_outcome": {"temperature": 0.7, "max_tokens": 6144},
    "avatar_prompt": {"temperature": 0.7, "max_tokens": 1024},
    "npc_avatar": {"temperature": 0.7, "max_tokens": 1024},
    "bridge_image": {"temperature": 0.7, "max_tokens": 1536},
}

# Manifest-based use cases: dataset lives in datasets/<use_case>_<language>.json,
# inputs are the dspy.Example input fields.
MANIFEST_INPUTS = {
    "scene_instruction": ("action_text", "species_desc", "background_location", "scene_context", "species_category"),
    "avatar_prompt": ("role", "traits", "avatar_description", "species_category"),
    "npc_avatar": ("role_name", "species", "gender_line", "traits"),
    "bridge_image": ("mission_name", "mission_description", "crew_list"),
}

# Every metric call of these use cases generates an image in ComfyUI — serial only.
IMAGE_USE_CASES = {"scene_instruction", "avatar_prompt", "npc_avatar", "bridge_image"}


def load_manifest_examples(use_case: str, language: str, limit: int) -> list[dspy.Example]:
    path = os.path.join(DATASETS_DIR, f"{use_case}_{language}.json")
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    if limit > 0:
        manifest = manifest[:limit]
    return [
        dspy.Example(**entry).with_inputs(*MANIFEST_INPUTS[use_case])
        for entry in manifest
    ]


def build_dataset(use_case: str, language: str, n: int, seed: int) -> list[dspy.Example]:
    if use_case == "npc_choice":
        extra = load_scenarios(language) or []
        return build_examples(language, n, seed=seed, extra_scenarios=extra)
    if use_case == "combined_outcome":
        return build_outcome_examples(language, n, seed=seed)
    return load_manifest_examples(use_case, language, n)


def save_artifacts(program: dspy.Module, use_case: str, language: str, path: str, meta: dict) -> None:
    instructions = {name: pred.signature.instructions for name, pred in program.named_predictors()}
    demos = []
    for name, pred in program.named_predictors():
        for demo in pred.demos:
            demos.append({"predictor": name, **demo.toDict()})
    payload = {
        "use_case": use_case,
        "language": language,
        "instructions": instructions,
        "demos": demos,
        "meta": meta,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def preflight_lm(lm: dspy.LM) -> None:
    """One tiny LM call before the pipeline: fail fast with a clear message
    instead of producing a garbage 0.0-scored artifact when the endpoint is
    unreachable (e.g. a docker-network LLM_URL used from the host)."""
    try:
        lm(messages=[{"role": "user", "content": "ping"}], max_tokens=8)
    except Exception as e:
        logger.error("LLM preflight failed: endpoint %s is unreachable — %s", lm.kwargs.get("api_base", "?"), e, exc_info=True)
        raise SystemExit(
            "LLM endpoint недоступен. Проверь LLM_URL (для запуска с хоста нужен "
            "http://localhost:8090/v1, docker-имя llama.cpp не резолвится) и что "
            "llama.cpp жив: curl http://localhost:8090/health"
        ) from e


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Compile better prompts with dspy (offline)")
    parser.add_argument("--use-case", required=True, choices=list(METRIC_FACTORIES))
    parser.add_argument("--language", default="ru", choices=["ru", "en"])
    parser.add_argument("--optimizer", default="bootstrap", choices=["bootstrap", "gepa"])
    parser.add_argument("--n-train", type=int, default=40)
    parser.add_argument("--n-dev", type=int, default=20)
    parser.add_argument("--demos", type=int, default=4, help="Max bootstrapped few-shot demos")
    parser.add_argument("--pass-threshold", type=float, default=0.7, help="Score needed to accept a bootstrap demo")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-metric-calls", type=int, default=0,
                        help="GEPA only: hard cap on metric calls (rollouts). "
                             "Each rollout is one dev-example eval; image metrics "
                             "cost ~90-140s per rollout, and the default budget of "
                             "420 rollouts runs for ~12h. 160 covers the initial "
                             "candidate pool plus ~8 refinement iterations.")
    parser.add_argument("--out", default="", help="Output JSON path (default compiled/<use_case>_<lang>.json)")
    args = parser.parse_args()

    language = LANGUAGE_RU if args.language == "ru" else LANGUAGE_EN
    signature = SIGNATURES[(args.use_case, language)]
    # image metrics generate one ComfyUI image per call — keep them serial
    threads = 1 if args.use_case in IMAGE_USE_CASES else args.threads

    student_lm = make_lm(**STUDENT_PARAMS[args.use_case])
    judge_lm = make_judge_lm()
    dspy.configure(lm=student_lm)
    preflight_lm(student_lm)

    dataset = build_dataset(args.use_case, language, args.n_train + args.n_dev, args.seed)
    split = max(1, int(len(dataset) * args.n_train / max(1, args.n_train + args.n_dev)))
    trainset, devset = dataset[:split], dataset[split:]
    logger.info("Dataset: %d train / %d dev examples", len(trainset), len(devset))

    metric = METRIC_FACTORIES[args.use_case](judge_lm)
    student = dspy.Predict(signature)

    evaluate = dspy.Evaluate(devset=devset, metric=metric, num_threads=threads, display_progress=True)
    baseline = evaluate(student)
    logger.info("Baseline score: %.3f", float(baseline.score))

    if args.optimizer == "bootstrap":
        def pass_metric(example, pred, trace=None) -> bool:
            return metric(example, pred, trace=trace) >= args.pass_threshold

        optimizer = BootstrapFewShot(
            metric=pass_metric,
            max_bootstrapped_demos=args.demos,
            max_labeled_demos=0,
            max_rounds=1,
        )
        compiled = optimizer.compile(student, trainset=trainset)
    else:
        def gepa_metric(gold, pred, trace=None, pred_name=None, pred_trace=None) -> float:
            # GEPA calls the metric with five args; ours uses the classic three.
            return metric(gold, pred, trace=trace)

        # Reflection honors JUDGE_MODEL too (see llm.make_judge_lm).
        reflection_lm = make_judge_lm(temperature=0.3, max_tokens=4096)
        # GEPA accepts exactly ONE of auto / max_metric_calls / max_full_evals;
        # auto is merely a preset that computes max_metric_calls internally.
        if args.max_metric_calls:
            optimizer = GEPA(
                metric=gepa_metric,
                reflection_lm=reflection_lm,
                max_metric_calls=args.max_metric_calls,
            )
        else:
            optimizer = GEPA(metric=gepa_metric, reflection_lm=reflection_lm, auto="light")
        compiled = optimizer.compile(student, trainset=trainset, valset=devset)

    final = evaluate(compiled)
    n_demos = sum(len(pred.demos) for _, pred in compiled.named_predictors())
    # Evaluate returns an EvaluationResult (Prediction subclass) — its .score
    # is the average metric as a float.
    baseline_score = float(baseline.score)
    final_score = float(final.score)
    logger.info("Compiled score: %.3f (baseline %.3f), %d demos", final_score, baseline_score, n_demos)

    out_path = args.out or os.path.join(COMPILED_DIR, f"{args.use_case}_{args.language}.json")
    save_artifacts(
        compiled,
        args.use_case,
        args.language,
        out_path,
        meta={
            "optimizer": args.optimizer,
            "baseline_score": baseline_score,
            "compiled_score": final_score,
            "n_train": len(trainset),
            "n_dev": len(devset),
            "seed": args.seed,
        },
    )
    logger.info("Artifacts saved: %s", out_path)
    logger.info("Next: review the instruction and run export_demos.py --artifacts %s", out_path)


if __name__ == "__main__":
    main()
