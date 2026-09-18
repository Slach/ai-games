#!/usr/bin/env bash
# Офлайн-оптимизатор промптов: запустить, подождать, прочитать отчёт.
#
#   ./run.sh               полный прогон (~10 мин на llama.cpp): код-метрика
#                         combined_outcome + судья npc_choice, в конце таблица
#                         «есть улучшения или нет»
#   ./run.sh --report      показать отчёт по последним артефактам (без прогона)
#   ./run.sh --list-usecases  список юзкейсов оптимизатора и статус демо
#   ./run.sh --use-case X  только один юзкейс:
#                         combined_outcome | npc_choice | scene_instruction
#                         | avatar_prompt | npc_avatar | bridge_image
#                         (кроме первых двух — медленные: каждая оценка =
#                         картинка в ComfyUI + VL-судья)
#
# Оптимизатор — bootstrap (демо). GEPA (переписывание инструкций, 30-60 мин)
# не опция по умолчанию: на насыщенных метриках он не обходит текущие промпты,
# см. README «Почему bootstrap, а не GEPA». Если нужен — запускай вручную:
#   ../../../.venv/bin/python run_optimize.py --use-case X --language ru --optimizer gepa
set -euo pipefail
cd "$(dirname "$0")"

VENV_PY=../../../.venv/bin/python
if [ ! -x "$VENV_PY" ]; then
    echo "ERROR: нет python из .venv: $VENV_PY" >&2
    exit 1
fi

# Подтянуть LLM_MODEL / LLM_URL из .env проекта.
if [ -f ../../../.env ]; then
    set -a
    # shellcheck disable=SC1091
    . ../../../.env
    set +a
fi

# .env написан для docker-сети: имена llama.cpp / comfyui с хоста не
# резолвятся, а порты опубликованы на 127.0.0.1 — переводим в localhost.
LLM_URL="${LLM_URL:-http://localhost:8090/v1}"
LLM_URL="${LLM_URL//llama.cpp/localhost}"
COMFYUI_URL="${COMFYUI_URL:-http://comfyui:8188}"
COMFYUI_URL="${COMFYUI_URL//comfyui/localhost}"
export LLM_URL COMFYUI_URL

USE_CASES=()
while [ $# -gt 0 ]; do
    case "$1" in
        --report)         exec "$VENV_PY" report.py compiled/*.json ;;
        --list-usecases)  exec "$VENV_PY" report.py --list ;;
        --use-case)       USE_CASES+=("$2"); shift ;;
        -h|--help)        awk 'NR>1 && $0 !~ /^#/ {exit} NR>1 {print}' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)                echo "неизвестный флаг: $1 (доступны --report, --use-case, --list-usecases)" >&2; exit 1 ;;
    esac
    shift
done

if [ "${#USE_CASES[@]}" -eq 0 ]; then
    USE_CASES=(combined_outcome npc_choice)
fi

ARTIFACTS=()

run_case() { # use_case n_train n_dev extra-args...
    local uc=$1 nt=$2 nd=$3
    shift 3
    echo ""
    echo "▸ Компиляция: $uc (language=ru, train=$nt, dev=$nd)"
    "$VENV_PY" run_optimize.py --use-case "$uc" --language ru \
        --n-train "$nt" --n-dev "$nd" "$@" \
        2>&1 | grep -vE "LiteLLM|Wrapper:|Average Metric|it/s\]|openai._base" || true
    ARTIFACTS+=("compiled/${uc}_ru.json")
}

for uc in "${USE_CASES[@]}"; do
    case "$uc" in
        combined_outcome)
            # Код-метрика: только вызовы студента, самый быстрый и точный прогон.
            run_case combined_outcome 6 6 --demos 3 --pass-threshold 1.0
            ;;
        npc_choice)
            run_case npc_choice 40 20
            ;;
        scene_instruction)
            # Каждая оценка метрики = картинка в ComfyUI + VL-судья.
            run_case scene_instruction 8 6 --demos 4
            ;;
        avatar_prompt)
            # Портрет игрока: контракт анатомии вида + VL-судья по портрету.
            run_case avatar_prompt 8 4 --demos 4
            ;;
        npc_avatar)
            # Портрет NPC: контракт пола для людей + VL-судья по портрету.
            # 22 кейса в манифесте, половина — energy/non_humanoid/symbiotic
            # (historic-слабое место: коллапс чужих в гуманоида).
            run_case npc_avatar 12 10 --demos 4
            ;;
        bridge_image)
            # Сцена мостика: экипаж живые люди в кадре + VL-судья по сцене.
            run_case bridge_image 6 4 --demos 4
            ;;
        *)
            echo "неизвестный юзкейс: $uc (combined_outcome|npc_choice|scene_instruction|avatar_prompt|npc_avatar|bridge_image)" >&2
            exit 1
            ;;
    esac
done

echo ""
"$VENV_PY" report.py "${ARTIFACTS[@]}"
