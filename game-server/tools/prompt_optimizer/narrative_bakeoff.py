"""Попарное сравнение нарративов outcome двух студенческих моделей.

student_bakeoff.py меряет combined_outcome только код-метрикой схемы JSON и
npc_choice фитингом роли — качество самой прозы там не измеряется. Этот
скрипт закрывает пробел: обе модели генерят outcome_json на ОДНОМ devset,
судьи вслепую сравнивают outcome_narrative попарно, с обменом порядка
против позиционного биаса. Победа засчитывается только при консенсусе
обоих порядков; расхождение или 0 — ничья.

Сравнение идёт последовательными фазами, чтобы в памяти жила максимум
ОДНА модель (bartowski/Altworld_Hemmingway-1 + unsloth/flint + судья
вместе не помещаются): сначала каждый студент генерит все нарративы и
выгружается через POST /models/unload роутера llama.cpp (рестарт не
нужен), затем каждый судья из --judges по очереди оценивает одни и те же
сохранённые тексты и выгружается. Берите судей из разных семейств моделей
(Hemmingway — файнтюн Qwen3.8-27B, так что судья Qwen3.8-27B к нему
сочувствует; вторым судьёй возьмите модель другого семейства) — итоговый
консенсус засчитывает победу, только если сошлись ВСЕ судьи.

Запуск с хоста (первая модель — база):
    LLM_URL=http://localhost:8090/v1 \
        /home/slach/src/github.com/Slach/ai-games/.venv/bin/python narrative_bakeoff.py \
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
import re  # noqa: E402
import time  # noqa: E402

import dspy  # noqa: E402

from language import LANGUAGE_RU  # noqa: E402
from llm import make_judge_lm, make_lm, unload_model  # noqa: E402
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
    parser.add_argument("--judges", default=os.getenv("JUDGE_MODEL", ""),
                        help="Судьи через запятую (default: одна модель из JUDGE_MODEL); "
                             "каждый судья оценивает все пары отдельным проходом")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if len(models) != 2:
        raise SystemExit("--models ждёт ровно две модели")
    judges = [j.strip() for j in args.judges.split(",") if j.strip()]
    if not judges:
        raise SystemExit("не задан судья: --judges или переменная JUDGE_MODEL")
    for judge in judges:
        if judge in models:
            raise SystemExit(f"судья {judge} — одна из сравниваемых моделей (self-preference bias)")
    base, candidate = models

    dataset = build_dataset("combined_outcome", LANGUAGE_RU, N_TRAIN + N_DEV, seed=42)
    devset = dataset[N_TRAIN:]
    logger.info("devset combined_outcome: %d кейсов", len(devset))

    # Фаза 1 — генерация: один студент в памяти, после последнего кейса выгружаем.
    program = dspy.Predict(CombinedOutcomeRU)
    generations = {base: [], candidate: []}
    for model in models:
        lm = make_lm(model=model, **STUDENT_PARAMS["combined_outcome"])
        preflight_lm(lm)
        for example in devset:
            inputs = example.inputs()
            t0 = time.time()
            try:
                with dspy.context(lm=lm):
                    pred = program(**inputs)
            except Exception:
                # dspy выкинет AdapterParseError после своих ретраев (например,
                # flint сжигает весь бюджет на reasoning и возвращает пустой
                # текст) — один битый кейс не должен убивать весь прогон.
                logger.warning("%s / %s: генерация упала", model, example.case_id, exc_info=True)
                pred = None
            seconds = round(time.time() - t0, 1)
            narrative = _extract_narrative(getattr(pred, "outcome_json", "")) if pred else None
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
        unload_model(model)

    # Фаза 2 — судейство: каждый судья проходит по одним и тем же парам, между судьями выгрузка.
    judge_results = []
    for judge_model in judges:
        judge_lm = make_judge_lm(model=judge_model)
        preflight_lm(judge_lm)
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
            for order, (na, nb) in {
                "straight": (gen_base["narrative"], gen_cand["narrative"]),
                "swapped": (gen_cand["narrative"], gen_base["narrative"]),
            }.items():
                try:
                    with dspy.context(lm=judge_lm):
                        pred = judge(turn_context=turn_context, narrative_a=na, narrative_b=nb)
                except Exception:
                    logger.warning("judge call failed for %s/%s", example.case_id, order, exc_info=True)
                    verdicts[order] = None
                    continue
                verdicts[order] = _verdict_value(pred)
                logger.info("judge %s %s/%s -> %s (%s)", judge_model, example.case_id, order, verdicts[order],
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
        judge_results.append({"judge": judge_model, "wins": wins,
                              "ties": ties, "flips": flips, "details": details})
        unload_model(judge_model)

    # Консенсус судей: победа зачисляется, только если все судьи дали win одной модели.
    consensus_wins = {base: 0, candidate: 0}
    consensus_rest = 0
    for i in range(len(devset)):
        case_winners = set()
        for result in judge_results:
            outcome = result["details"][i]["result"]
            if outcome.startswith("win:"):
                case_winners.add(outcome[len("win:"):])
        if len(case_winners) == 1:
            consensus_wins[case_winners.pop()] += 1
        else:
            consensus_rest += 1

    results = {
        "models": models,
        "judges": judges,
        "per_judge": judge_results,
        "consensus": {"wins": consensus_wins, "ties_or_disagreed": consensus_rest},
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
    for result in judge_results:
        print(f"судья {result['judge']}: победы {base}={result['wins'][base]}, "
              f"{candidate}={result['wins'][candidate]}, "
              f"ничьих={result['ties']}, расхождений порядка={result['flips']}")
    print(f"консенсус всех судей: {base}={consensus_wins[base]}, "
          f"{candidate}={consensus_wins[candidate]}, ничьих/разногласий={consensus_rest}")
    print("Тексты: compiled/narrative_bakeoff_results.json")


if __name__ == "__main__":
    main()
