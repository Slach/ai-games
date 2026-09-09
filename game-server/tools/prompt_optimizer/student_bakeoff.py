"""Сравнение студенческих моделей на текстовых юзкейсах оптимизатора.

Каждый кандидат прогоняет ОДИН И ТОТ ЖЕ devset через ту же метрику, что и
run_optimize.py (baseline, без демо), с судьёй из JUDGE_MODEL. Код-метрика
combined_outcome без шума — первичный критерий; npc_choice (LLM-судья) —
вторичный. Тайминг даёт грубую оценку скорости генерации.

Запуск с хоста (первая модель в --models считается текущей точкой отсчёта):
    LLM_MODEL=ornith-ai/Ornith-1.5-35B JUDGE_MODEL=unsloth/Qwen3.8-27B-MTP \
    LLM_URL=http://localhost:8090/v1 \
        ../../../.venv/bin/python student_bakeoff.py \
        --models ornith-ai/Ornith-1.5-35B,mradermacher/Gemma-4-Novelist-31B
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("COMFYUI_URL", "http://localhost:8188")

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import time  # noqa: E402

import dspy  # noqa: E402

from language import LANGUAGE_RU  # noqa: E402
from llm import make_judge_lm, make_lm  # noqa: E402
from metrics import METRIC_FACTORIES  # noqa: E402
from run_optimize import STUDENT_PARAMS, build_dataset, preflight_lm  # noqa: E402
from signatures import SIGNATURES  # noqa: E402

logger = logging.getLogger("student_bakeoff")

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))

# (n_train, n_dev): devset — то, что оцениваем; train-часть отбрасывается,
# чтобы датасет совпадал с прогонами run_optimize.py.
SIZES = {
    "combined_outcome": (6, 6),
    "npc_choice": (40, 20),
}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Сравнение студенческих моделей")
    parser.add_argument("--models", required=True, help="Модели через запятую; первая — точка отсчёта")
    parser.add_argument("--use-cases", default="combined_outcome,npc_choice")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    use_cases = [u.strip() for u in args.use_cases.split(",") if u.strip()]
    judge_lm = make_judge_lm()

    devsets = {}
    for use_case in use_cases:
        n_train, n_dev = SIZES[use_case]
        dataset = build_dataset(use_case, LANGUAGE_RU, n_train + n_dev, seed=42)
        devsets[use_case] = dataset[n_train:] if n_dev else dataset
        logger.info("devset %s: %d кейсов", use_case, len(devsets[use_case]))

    results = {}
    for model in models:
        # Preflight на первом юзкейсе: fail fast если модель не отвечает.
        preflight_lm(make_lm(model=model, **STUDENT_PARAMS[use_cases[0]]))
        results[model] = {}
        for use_case in use_cases:
            lm = make_lm(model=model, **STUDENT_PARAMS[use_case])
            metric = METRIC_FACTORIES[use_case](judge_lm)
            program = dspy.Predict(SIGNATURES[(use_case, LANGUAGE_RU)])
            evaluate = dspy.Evaluate(devset=devsets[use_case], metric=metric, num_threads=2, display_progress=True)
            with dspy.context(lm=lm):
                t0 = time.time()
                score = float(evaluate(program).score)
                seconds = round(time.time() - t0, 1)
            results[model][use_case] = {"score": round(score, 1), "seconds": seconds,
                                        "n": len(devsets[use_case])}
            logger.info("%s / %s: %.1f за %.1fs", model, use_case, score, seconds)

    with open(os.path.join(TOOL_DIR, "compiled", "student_bakeoff_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print()
    print("════════════ СРАВНЕНИЕ СТУДЕНТОВ ════════════")
    print()
    header = f"{'модель':<45}" + "".join(f" {uc:>22}" for uc in use_cases)
    print(header)
    print("─" * len(header))
    for model in models:
        cells = []
        for uc in use_cases:
            r = results[model][uc]
            cells.append(f"{r['score']:>6.1f}% ({r['seconds']:>5.1f}s n={r['n']})")
        print(f"{model:<45}" + "".join(f" {c:>22}" for c in cells))
    print()
    print("combined_outcome — код-метрика (шума нет), npc_choice — LLM-судья (шум ±3).")
    print("Результаты: compiled/student_bakeoff_results.json")


if __name__ == "__main__":
    main()
