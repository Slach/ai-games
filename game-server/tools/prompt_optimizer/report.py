"""Компактный терминальный отчёт по артефактам компиляции.

Читает JSON-ы из compiled/ (написаны run_optimize.py) и печатает таблицу
baseline vs compiled с вердиктом: УЛУЧШЕНО / БЕЗ ИЗМЕНЕНИЙ / УХУДШЕНО.
Порог вердикта учитывает шум метрики: у код-метрики шума нет, у LLM/VL-судьи
есть (±2-5 пунктов), поэтому там «улучшением» считается только выход за шум.
"""

import argparse
import json
import os
import sys

METRIC_NOTES = {
    "combined_outcome": (
        "код-проверки контракта: entity_id из ростера, обязательства [fatal]/[injury], "
        "голое character_name, дельты, дубли офлайн-систем — шума нет, любое улучшение реально"
    ),
    "npc_choice": (
        "LLM-судья: соответствие выбора личности / роли / полосе лояльности — "
        "шум судьи около ±3 пунктов"
    ),
    "scene_instruction": (
        "VL-судья по картинке из ComfyUI: сохранение формы (нет коллапса в гуманоида), "
        "действие, окружение — шум судьи около ±5 пунктов"
    ),
    "avatar_prompt": (
        "VL-судья по портрету из ComfyUI: контракт анатомии вида, пол, роль, "
        "качество — шум судьи около ±5 пунктов"
    ),
    "npc_avatar": (
        "VL-судья по портрету из ComfyUI: контракт анатомии вида, пол, роль, "
        "качество — шум судьи около ±5 пунктов"
    ),
    "bridge_image": (
        "VL-судья по сцене мостика из ComfyUI: экипаж живые люди в кадре (не схема), "
        "окружение, количество, качество — шум судьи около ±5 пунктов"
    ),
}

NOISE = {
    "combined_outcome": 0.0,
    "npc_choice": 3.0,
    "scene_instruction": 5.0,
    "avatar_prompt": 5.0,
    "npc_avatar": 5.0,
    "bridge_image": 5.0,
}

USE_TTY = sys.stdout.isatty()
GREEN = "\033[32m" if USE_TTY else ""
RED = "\033[31m" if USE_TTY else ""
YELLOW = "\033[33m" if USE_TTY else ""
BOLD = "\033[1m" if USE_TTY else ""
DIM = "\033[2m" if USE_TTY else ""
RESET = "\033[0m" if USE_TTY else ""


def verdict_for(use_case: str, delta: float) -> tuple[str, str]:
    """Return (colored verdict text, machine status: improved/flat/worse)."""
    noise = NOISE.get(use_case, 3.0)
    if delta > noise:
        return f"{GREEN}УЛУЧШЕНО ↑{RESET}", "improved"
    if delta < -noise:
        return f"{RED}УХУДШЕНО ↓{RESET}", "worse"
    return f"{YELLOW}БЕЗ ИЗМЕНЕНИЙ ={RESET}", "flat"


def load_rows(paths: list[str]) -> list[dict]:
    rows = []
    for path in paths:
        if not os.path.exists(path):
            print(f"{RED}нет файла {path}{RESET}", file=sys.stderr)
            continue
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        meta = payload.get("meta", {})
        baseline = meta.get("baseline_score")
        compiled = meta.get("compiled_score")
        if baseline is None or compiled is None:
            print(f"{DIM}пропуск {path}: нет meta с оценками (старый формат?){RESET}", file=sys.stderr)
            continue
        rows.append({
            "label": f"{payload['use_case']}_{payload['language']} ({meta.get('optimizer', '?')})",
            "use_case": payload["use_case"],
            "baseline": float(baseline),
            "compiled": float(compiled),
            "delta": float(compiled) - float(baseline),
            "n_train": meta.get("n_train", "?"),
            "n_dev": meta.get("n_dev", "?"),
            "demos": len(payload.get("demos", [])),
            "path": path,
        })
    return rows


def print_report(rows: list[dict]) -> int:
    print()
    print(f"{BOLD}════════════ ИТОГИ КОМПИЛЯЦИИ ПРОМПТОВ ════════════{RESET}")
    print()
    header = f"{'юзкейс':<42} {'baseline':>9} {'compiled':>9} {'Δ':>7}  {'вердикт':<16}"
    print(header)
    print("─" * len(header) + "────")
    for row in rows:
        verdict, status = verdict_for(row["use_case"], row["delta"])
        delta_color = {"improved": GREEN, "worse": RED, "flat": YELLOW}[status]
        print(
            f"{row['label']:<42} {row['baseline']:>8.1f}% {row['compiled']:>8.1f}% "
            f"{delta_color}{row['delta']:>+6.1f}{RESET}  {verdict}"
        )
        note = METRIC_NOTES.get(row["use_case"])
        if note:
            print(f"{DIM}    метрика: {note}{RESET}")
        print(
            f"{DIM}    train={row['n_train']} dev={row['n_dev']} демо={row['demos']}"
            f"  ·  артефакт: {row['path']}{RESET}"
        )
    print()
    improved = [r for r in rows if verdict_for(r["use_case"], r["delta"])[1] == "improved"]
    if improved:
        print(f"{GREEN}Улучшения есть{RESET} в: " + ", ".join(r["label"] for r in improved))
        print("Следующий шаг — перенести в игру:")
        for row in improved:
            print(f"  ../../../.venv/bin/python export_demos.py --artifacts {row['path']}")
        print("…затем ревью текста и вставка блока в game-server/prompts.py.")
    else:
        print(
            f"{YELLOW}Улучшений за пределами шума нет{RESET} — текущие промпты уже насыщены "
            "для этих метрик. Расширяй датасет (npc_dataset.py / outcome_dataset.py) "
            "или целься в юзкейс с реальными отказами."
        )
    print()
    return 0


def print_usecase_list() -> int:
    """Список юзкейсов оптимизатора из реестров + статус демо в prompts.py."""
    from export_demos import DEMO_CONSTANT
    from metrics import METRIC_FACTORIES
    from signatures import SIGNATURES

    import prompts

    metric_short = {
        "make_combined_outcome_metric": "код (без шума)",
        "make_npc_choice_metric": "LLM-судья",
        "make_scene_vl_metric": "VL-судья (ComfyUI)",
        "make_avatar_vl_metric": "VL-судья (ComfyUI)",
        "make_npc_avatar_vl_metric": "VL-судья (ComfyUI)",
        "make_bridge_vl_metric": "VL-судья (ComfyUI)",
    }
    by_case: dict[str, list[str]] = {}
    for use_case, language in SIGNATURES:
        by_case.setdefault(use_case, []).append(language)

    print()
    print(f"{BOLD}Юзкейсы оптимизатора (./run.sh --use-case X):{RESET}")
    print()
    for use_case, languages in by_case.items():
        factory = METRIC_FACTORIES[use_case].__name__
        demo_attr = DEMO_CONSTANT[use_case]
        pasted = [lang for lang in ("ru", "en") if prompts.__dict__[demo_attr].get(lang)]
        pasted_str = ",".join(pasted) if pasted else "нет"
        print(
            f"  {use_case:<20} {','.join(sorted(languages)):<7} "
            f"метрика: {metric_short.get(factory, factory):<20} "
            f"демо в prompts.py: {pasted_str}"
        )

    from llm_config import DEFAULT_USE_CASES

    uncovered = sorted(set(DEFAULT_USE_CASES) - set(by_case))
    print()
    print(
        f"{DIM}Ещё в рантиме без оптимизатора ({len(uncovered)}): "
        + ", ".join(uncovered) + f"{RESET}"
    )
    print(f"{DIM}Рецепт нового юзкейса — README «Добавление нового юзкейса».{RESET}")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Отчёт по compiled/*.json оптимизатора")
    parser.add_argument("--list", action="store_true", help="показать юзкейсы оптимизатора")
    parser.add_argument("artifacts", nargs="*", help="JSON-файлы из compiled/")
    args = parser.parse_args()
    if args.list:
        return print_usecase_list()
    if not args.artifacts:
        parser.error("укажи артефакты или --list")
    rows = load_rows(args.artifacts)
    if not rows:
        print(f"{RED}нет валидных артефактов — сначала запусти run.sh{RESET}", file=sys.stderr)
        return 1
    return print_report(rows)


if __name__ == "__main__":
    raise SystemExit(main())
