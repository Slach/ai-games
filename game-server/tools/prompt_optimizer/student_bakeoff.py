"""Сравнение студенческих моделей на текстовых юзкейсах оптимизатора.

Каждый кандидат прогоняет ОДИН И ТОТ ЖЕ devset через ту же метрику, что и
run_optimize.py (baseline, без демо). Код-метрика combined_outcome без
шума — первичный критерий; npc_choice (LLM-судья) — вторичный. Тайминг
даёт грубую оценку скорости генерации.

Прогон разбит на последовательные фазы, чтобы в памяти жила максимум ОДНА
модель (bartowski/Altworld_Hemmingway-1 + unsloth/flint + судья вместе не
помещаются): фаза 1 — каждый студент генерит все предсказания и
выгружается (POST /models/unload роутера llama.cpp, рестарт не нужен);
фаза 2 — каждый судья из --judges по очереди оценивает одни и те же
сохранённые предсказания и выгружается. Судьи из --judges НЕ должны
совпадать со студентами (self-preference bias); для картинок (scene_*,
avatar_*, bridge_*) судья обязан быть VL (mmproj в llama.cpp.models.ini).

Запуск с хоста (первая модель в --models считается текущей точкой отсчёта):
    LLM_URL=http://localhost:8090/v1 \
        ../../../.venv/bin/python student_bakeoff.py \
        --models bartowski/Altworld_Hemmingway-1,unsloth/flint \
        --judges unsloth/Qwen3.8-27B-MTP,ornith-ai/Ornith-1.5-35B
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
from llm import make_judge_lm, make_lm, unload_model  # noqa: E402
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
    parser.add_argument("--judges", default=os.getenv("JUDGE_MODEL") or os.getenv("LLM_MODEL") or "unsloth/Qwen3.5-27B",
                        help="Судьи через запятую; каждый судья оценивает сохранённые "
                             "предсказания отдельным проходом (default: JUDGE_MODEL/LLM_MODEL)")
    parser.add_argument("--use-cases", default="combined_outcome,npc_choice")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    use_cases = [u.strip() for u in args.use_cases.split(",") if u.strip()]
    judges = [j.strip() for j in args.judges.split(",") if j.strip()]
    for judge in judges:
        if judge in models:
            raise SystemExit(f"судья {judge} — одна из сравниваемых моделей (self-preference bias)")

    devsets = {}
    for use_case in use_cases:
        n_train, n_dev = SIZES[use_case]
        dataset = build_dataset(use_case, LANGUAGE_RU, n_train + n_dev, seed=42)
        devsets[use_case] = dataset[n_train:] if n_dev else dataset
        logger.info("devset %s: %d кейсов", use_case, len(devsets[use_case]))

    # Фаза 1 — генерация: один студент в памяти, после последнего юзкейса выгружаем.
    # Предсказания сохраняем на диск после каждой модели — краш на второй модели
    # не теряет генерации первой.
    predictions_path = os.path.join(TOOL_DIR, "compiled", "student_bakeoff_predictions.json")
    predictions = {}
    for model in models:
        # Preflight на первом юзкейсе: fail fast если модель не отвечает.
        preflight_lm(make_lm(model=model, **STUDENT_PARAMS[use_cases[0]]))
        predictions[model] = {}
        for use_case in use_cases:
            lm = make_lm(model=model, **STUDENT_PARAMS[use_case])
            program = dspy.Predict(SIGNATURES[(use_case, LANGUAGE_RU)])
            rows = []
            for i, example in enumerate(devsets[use_case]):
                inputs = example.inputs()
                t0 = time.time()
                try:
                    with dspy.context(lm=lm):
                        pred = program(**inputs)
                except Exception:
                    # как в narrative_bakeoff: dspy AdapterParseError после
                    # ретраев не должен убивать весь прогон
                    logger.warning("%s / %s / case %d: генерация упала", model, use_case, i, exc_info=True)
                    pred = None
                seconds = round(time.time() - t0, 1)
                case_id = str(getattr(example, "case_id", f"case{i}"))
                outputs = {k: str(v) for k, v in pred.toDict().items()} if pred else {}
                rows.append({"case_id": case_id, "seconds": seconds, "outputs": outputs})
                logger.info("%s / %s / %s: сгенерировано за %.1fs", model, use_case, case_id, seconds)
            predictions[model][use_case] = rows
        unload_model(model)
        with open(predictions_path, "w", encoding="utf-8") as f:
            json.dump({"models": models, "predictions": predictions}, f, ensure_ascii=False, indent=2)
        logger.info("generations saved: %s", predictions_path)

    # Фаза 2 — судейство: каждый судья проходит по одним и тем же предсказаниям,
    # между судьями выгрузка. Код-метрики (combined_outcome) судью игнорируют —
    # их оценки у всех судей совпадают.
    results = {model: {uc: {} for uc in use_cases} for model in models}
    for judge_model in judges:
        judge_lm = make_judge_lm(model=judge_model)
        preflight_lm(judge_lm)
        for model in models:
            for use_case in use_cases:
                metric = METRIC_FACTORIES[use_case](judge_lm)
                preds = [dspy.Prediction(**row["outputs"]) for row in predictions[model][use_case]]
                t0 = time.time()
                scores = [metric(example, pred) for example, pred in zip(devsets[use_case], preds)]
                results[model][use_case][judge_model] = {
                    "score": round(100.0 * sum(scores) / len(scores), 1),
                    "seconds": round(time.time() - t0, 1),
                    "n": len(scores),
                }
                logger.info("judge %s / %s / %s: %.1f за %.1fs",
                            judge_model, model, use_case,
                            results[model][use_case][judge_model]["score"],
                            results[model][use_case][judge_model]["seconds"])
        unload_model(judge_model)

    with open(os.path.join(TOOL_DIR, "compiled", "student_bakeoff_results.json"), "w", encoding="utf-8") as f:
        json.dump({"models": models, "judges": judges, "results": results,
                   "predictions": predictions}, f, ensure_ascii=False, indent=2)

    print()
    print("════════════ СРАВНЕНИЕ СТУДЕНТОВ ════════════")
    print()
    columns = [(uc, j) for uc in use_cases for j in judges]
    labels = [f"{uc}@{j.rsplit('/', 1)[-1]}" for uc, j in columns]
    header = f"{'модель':<45}" + "".join(f" {label:>24}" for label in labels) + f" {'ген.,с':>8}"
    print(header)
    print("─" * len(header))
    for model in models:
        cells = []
        for uc, j in columns:
            r = results[model][uc][j]
            cells.append(f"{r['score']:>11.1f}% ({r['n']:>2})")
        gen_total = sum(row["seconds"] for uc in predictions[model] for row in predictions[model][uc])
        print(f"{model:<45}" + "".join(f" {c:>24}" for c in cells) + f" {gen_total:>8.0f}")
    print()
    print("combined_outcome — код-метрика (у всех судей одинакова), npc_choice — LLM-судьи.")
    print("Результаты: compiled/student_bakeoff_results.json")


if __name__ == "__main__":
    main()
