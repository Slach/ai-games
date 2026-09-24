"""Попарное сравнение нарративов outcome двух студенческих моделей.

student_bakeoff.py меряет combined_outcome только код-метрикой схемы JSON и
npc_choice фитингом роли — качество самой прозы там не измеряется. Этот
скрипт закрывает пробел: обе модели генерят outcome_json на ОДНОМ devset,
судья (JUDGE_MODEL) вслепую сравнивает outcome_narrative попарно, с обменом
порядка против позиционного биаса. Победа засчитывается только при
консенсусе обоих порядков; расхождение или 0 — ничья.

ПРЕДУПРЕЖДЕНИЕ: судья той же моделью, что и один из кандидатов, даёт
self-preference bias — верь код-метрике из student_bakeoff и собственным
глазам (тексты сохраняются в compiled/narrative_bakeoff_results.json).

Запуск с хоста (первая модель — база):
    LLM_URL=http://localhost:8090/v1 JUDGE_MODEL=bartowski/Altworld_Hemmingway-1 \
        /home/slach/src/github.com/Slach/ai-games/.venv/bin/python narrative_bakeoff.py \
        --models ornith-ai/Ornith-1.5-35B,bartowski/Altworld_Hemmingway-1
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("COMFYUI_URL", "http://localhost:8188")

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402

import dspy  # noqa: E402

from language import LANGUAGE_RU  # noqa: E402
from llm import make_judge_lm, make_lm  # noqa: E402
from run_optimize import STUDENT_PARAMS, build_dataset, preflight_lm  # noqa: E402
from signatures import CombinedOutcomeRU, NarrativePairJudge  # noqa: E402

logger = logging.getLogger("narrative_bakeoff")

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))

# Как в student_bakeoff: train-часть отбрасываем, devset совпадает с его прогонами.
N_TRAIN, N_DEV = 6, 6

_VERDICT_RE = re.compile(r"\b([012])\b")


def _extract_narrative(raw: str) -> str | None:
    try:
        data = json.loads(str(raw).strip())
    except json.JSONDecodeError:
        return None
    narrative = str(data.get("outcome_narrative", "") or "").strip()
    return narrative or None


def _verdict_value(pred) -> int | None:
    match = _VERDICT_RE.search(str(getattr(pred, "verdict", "") or "").strip())
    return int(match.group(1)) if match else None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Попарное сравнение нарративов")
    parser.add_argument("--models", required=True, help="Модели через запятую; первая — база")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if len(models) != 2:
        raise SystemExit("--models ждёт ровно две модели")
    base, candidate = models

    dataset = build_dataset("combined_outcome", LANGUAGE_RU, N_TRAIN + N_DEV, seed=42)
    devset = dataset[N_TRAIN:]
    logger.info("devset combined_outcome: %d кейсов", len(devset))

    program = dspy.Predict(CombinedOutcomeRU)
    generations = {base: [], candidate: []}
    for model in models:
        lm = make_lm(model=model, **STUDENT_PARAMS["combined_outcome"])
        preflight_lm(lm)
        for example in devset:
            inputs = example.inputs()
            with dspy.context(lm=lm):
                t0 = time.time()
                pred = program(**inputs)
                seconds = round(time.time() - t0, 1)
            narrative = _extract_narrative(getattr(pred, "outcome_json", ""))
            generations[model].append({
                "case_id": example.case_id,
                "narrative": narrative,
                "words": len(narrative.split()) if narrative else 0,
                "seconds": seconds,
            })
            logger.info("%s / %s: %s (%d слов, %.1fs%s)",
                        model, example.case_id,
                        "OK" if narrative else "JSON FAIL",
                        len(narrative.split()) if narrative else 0,
                        seconds,
                        "" if narrative else f" raw={str(getattr(pred, 'outcome_json', ''))[:120]!r}")

    judge_lm = make_judge_lm()
    judge = dspy.Predict(NarrativePairJudge)
    wins = {base: 0, candidate: 0}
    ties = flips = 0
    details = []
    for i, example in enumerate(devset):
        gen_base = generations[base][i]
        gen_cand = generations[candidate][i]
        if not gen_base["narrative"] or not gen_cand["narrative"]:
            logger.info("case %s: пропуск — нет валидного нарратива (base=%s, cand=%s)",
                        example.case_id,
                        bool(gen_base["narrative"]), bool(gen_cand["narrative"]))
            details.append({"case_id": example.case_id, "result": "skipped",
                            "base": gen_base, "candidate": gen_cand})
            continue
        turn_context = (
            f"Setting: {example.setting}\nConflict: {example.conflict}\n"
            f"Decisions this turn:\n{example.decisions_text}"
        )
        verdicts = {}
        for order, (na, nb, label) in {
            "straight": (gen_base["narrative"], gen_cand["narrative"], base),
            "swapped": (gen_cand["narrative"], gen_base["narrative"], candidate),
        }.items():
            try:
                with dspy.context(lm=judge_lm):
                    pred = judge(turn_context=turn_context, narrative_a=na, narrative_b=nb)
            except Exception:
                logger.warning("judge call failed for %s/%s", example.case_id, order, exc_info=True)
                verdicts[order] = None
                continue
            verdicts[order] = _verdict_value(pred)
            logger.info("judge %s/%s -> %s (%s)", example.case_id, order, verdicts[order],
                        getattr(pred, "feedback", ""))
        straight, swapped = verdicts.get("straight"), verdicts.get("swapped")
        # straight: 1=base, 2=candidate; swapped: 1=candidate, 2=base.
        straight_model = base if straight == 1 else candidate if straight == 2 else None
        swapped_model = candidate if swapped == 1 else base if swapped == 2 else None
        if straight_model is None or swapped_model is None or straight_model != swapped_model:
            result = "flip" if straight_model and swapped_model else "tie"
            ties += result == "tie"
            flips += result == "flip"
        else:
            result = f"win:{straight_model}"
            wins[straight_model] += 1
        details.append({"case_id": example.case_id, "result": result,
                        "verdicts": verdicts, "base": gen_base, "candidate": gen_cand})

    results = {
        "models": models,
        "wins": wins,
        "ties": ties,
        "flips": flips,
        "details": details,
    }
    out_path = os.path.join(TOOL_DIR, "compiled", "narrative_bakeoff_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print()
    print("════════════ ПОПАРНОЕ СРАВНЕНИЕ НАРРАТИВОВ ════════════")
    print()
    for model in models:
        gens = [g for g in generations[model]]
        ok = [g for g in gens if g["narrative"]]
        avg_words = sum(g["words"] for g in ok) / len(ok) if ok else 0
        avg_sec = sum(g["seconds"] for g in gens) / len(gens) if gens else 0
        print(f"{model}: {len(ok)}/{len(gens)} валидного JSON, "
              f"в среднем {avg_words:.0f} слов за {avg_sec:.0f}с")
    print()
    print(f"победы: {base}={wins[base]}, {candidate}={wins[candidate]}, "
          f"ничьих={ties}, расхождений порядка={flips}")
    print("Тексты: compiled/narrative_bakeoff_results.json")


if __name__ == "__main__":
    main()
