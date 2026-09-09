# Offline prompt optimizer (DSPy)

Компилирует лучшие инструкции и few-shot демо для промптов `prompts.py`.
Полностью офлайновый инструмент: dspy — dev-зависимость хостового `.venv`,
в рантим-образ game-server не попадает, рантим не меняется, пока вы сами
не вставите скомпилированный блок демо в `prompts.py`.

## Как это устроено

```
npc_dataset.py ──► datasets/npc_choice_<lang>.json ─┐
scene manifest   ─► datasets/scene_instruction_... ─┤
                                                    ▼
run_optimize.py ── baseline Evaluate ──► BootstrapFewShot / GEPA ──► compiled/<use_case>_<lang>.json
                                                    │ (LLM llama.cpp; для сцены ещё ComfyUI)
                                                    ▼
export_demos.py ──► оптимизированная инструкция (ревью) + блок демо для вставки в prompts.py
```

- **Датасет npc_choice** — симуляция решений NPC: банк синтетических ситуаций
  (пробоина, неопознанный объект, вирус в жизнеобеспечении, ...) × профили
  NPC × лояльность из всех четырёх полос (`game_rules.loyalty_band`).
  Опционально банк расширяется LLM (`npc_dataset.py --generate N`).
- **Метрика npc_choice** — жёсткие проверки кодом (action_id из предложенных,
  rationale непустой) + LLM-судья рубрикой «соответствие личности/роли/лояльности».
- **Метрика scene_instruction** — прогон инструкции через боевой путь
  `ImageGenerator.generate_character_in_scene` (Qwen-Image-Edit, ComfyUI) и
  VL-судья по паре картинок (аватар ↔ результат): сохранение формы (нет
  коллапса в стоящего человека), изображённое действие, окружение.
- **VS не трогается**: оптимизация идёт по базовым промптам (без VS-обёртки),
  рантимная Verbalized Sampling продолжает работать как раньше.

## Инференс

Всё уже работает на существующих сервисах, ничего докачивать не надо:

- llama.cpp: `http://localhost:8090/v1` (переопределяется `LLM_URL`/`LLM_MODEL`).
  Vision-судья использует тот же endpoint — у игровых моделей уже есть mmproj
  (`llama.cpp.models.ini`), картинки уходят как base64 `image_url`.
- ComfyUI: `http://localhost:8188` (переопределяется `COMFYUI_URL`).

## Запуск

Одна команда: запустить, подождать (~10 минут), прочитать отчёт в конце.

```bash
cd game-server/tools/prompt_optimizer
./run.sh
```

Что происходит: строится датасет → замеряется baseline (промпты без демо) →
bootstrap компилирует демо → повторный замер → таблица. Никакого порядка
запуска соблюдать не нужно — всё внутри.

Остальные команды:

```bash
./run.sh --report                   # отчёт по последним прогонам, без компиляции
./run.sh --use-case scene_instruction   # только один юзкейс (scene медленный: ComfyUI)
```

`run.sh` сам подтягивает `.env`, переводит docker-имена (`llama.cpp`/`comfyui`)
в `localhost`, делает preflight-запрос к LLM (упадёт с внятной ошибкой, если
endpoint недоступен). Пример отчёта:

```
юзкейс                            baseline  compiled      Δ  вердикт
combined_outcome_ru (bootstrap)      95.0%     100.0%  +5.0  УЛУЧШЕНО ↑
npc_choice_ru (bootstrap)            86.0%      85.5%  -0.5  БЕЗ ИЗМЕНЕНИЙ =
```

Вердикт учитывает шум метрики: у код-метрики (`combined_outcome`) шума нет,
у LLM-судьи ±3, у VL-судьи ±5 — «улучшением» считается только выход за шум.
Если улучшения есть — в отчёте готовая команда `export_demos.py` для экспорта.

## Почему bootstrap, а не GEPA

Замеры на этой машине (Ornith-1.5-35B как student, judge и reflection):

| оптимизатор | время | результат |
|---|---|---|
| bootstrap, combined_outcome | ~4 мин | 88.3→100 и 95.0→100 (+5…+11.7) |
| bootstrap, npc_choice | ~5 мин | 86.0→85.5 (насыщена, шум) |
| GEPA, npc_choice | 55 мин | 88.2→89.5 — инструкцию переписать не смог, вернул исходную |

Выводы:

- Наши узкие места — **дисциплина контракта** (entity_id, метки [fatal],
  дельты) — лечатся few-shot демо, это bootstrap.
- GEPA переписывает инструкции и требует запаса для улучшения И сильной
  reflection-модели; когда student = judge = reflection = одна и та же
  локальная модель, его потолок низок, а цена — 30-60 минут.
- Остальные оптимизаторы dspy 3.3.1: Bootstrap+RandomSearch/Optuna (подбор
  подмножества демо, дороже, выигрыш небольшой), MIPROv2/SIMBA/COPRO
  (переписывают инструкции, предложения пишут по-английски — портит RU-промпты),
  KNN/LabeledFewShot (нужны золотые разметки, их нет).

Практическое правило: `./run.sh` (bootstrap). Если юзкейс стоит на месте
при низкой baseline и видно, что проблема в формулировке — точечно:

```bash
../../../.venv/bin/python run_optimize.py --use-case <юзкейс> --language ru --optimizer gepa
```

### Ручной пошаговый вариант (если нужно)

```bash
# 0. зависимости (один раз)
uv pip install --python ../../.venv/bin/python -r requirements.txt

# 1. датасет npc_choice (быстро, без LLM — банк сценариев детерминирован)
../../.venv/bin/python npc_dataset.py --language ru --n 60
#    расширить банк сценариев LLM (медленнее):
../../.venv/bin/python npc_dataset.py --language ru --n 60 --generate 6

# 2. компиляция npc_choice (bootstrap; десятки-сотни вызовов llama.cpp)
../../.venv/bin/python run_optimize.py --use-case npc_choice --language ru \
    --n-train 40 --n-dev 20 --optimizer bootstrap

# 3. экспорт
../../.venv/bin/python export_demos.py --artifacts compiled/npc_choice_ru.json
#    → ревью оптимизированной инструкции (заменить ею system-промпт в prompts.py,
#      при необходимости перевести), затем вставить присвоение
#      NPC_DECISION_DEMOS[LANGUAGE_RU] = """...""" рядом со словарём демо.

# 4. тесты рантима
cd ../../ && ../.venv/bin/python -m unittest discover -s tests
```

### scene_instruction (VL-критика)

Сначала подготовьте манифест `datasets/scene_instruction_ru.json` — список
кейсов (аватары и фоны берутся из `comfyui/files/`):

```json
[
  {
    "case_id": "crystal_engineer_1",
    "action_text": "Чинит кристаллический резонатор реактора",
    "species_desc": "Кластер парящих кристаллов без конечностей и лица",
    "background_location": "engineering",
    "scene_context": "Корабль в опасности, реактор перегружен",
    "species_category": "non_humanoid",
    "avatar_filename": "avatar_00123_.png",
    "background_filename": "background_engineering_00001_.png"
  }
]
```

Затем тот же `run_optimize.py --use-case scene_instruction`. Метрика
генерирует картинку на каждый вызов — держите `--n-train/--n-dev` малыми
(10-20 кейсов) и закладывайте время ComfyUI на каждый скор.

## Оптимизаторы

- `--optimizer bootstrap` (по умолчанию) — BootstrapFewShot: учитель (та же
  модель) прогоняет trainset, успешные трассы становятся демо. Дёшево и
  стабильно; порог приёма — `--pass-threshold 0.7`.
- `--optimizer gepa` — GEPA (`auto=light`): итеративно переписывает инструкцию
  по фидбеку судьи. Дороже по GPU-времени, затрагивает и инструкцию, и демо.

## Добавление нового юзкейса

1. Класс-сигнатура в `signatures.py` (входы/выходы = контракт схемы из
   `game_server.py`, докстринг = текущий system-промпт из `prompts.py`).
2. Метрика в `metrics.py` + запись в `METRIC_FACTORIES`.
3. Датасет-билдер в `run_optimize.py::build_dataset`.
4. Константа демо в `prompts.py` по образцу `NPC_DECISION_DEMOS`.

## Отдельный judge / reflection (JUDGE_MODEL)

По умолчанию студент, судья и reflection — одна модель (`LLM_MODEL`).
Вынести судью в другую (меньше самосогласованных оценок, другой голос
reflection для GEPA):

```bash
# 1. Разрешить роутеру держать две модели (nvidia-spark, у него сейчас
#    --models-max 1 — каждая смена модели = ~15с перезагрузка, будет трэш):
#    поправь запуск llama.cpp на --models-max 2 и перезапусти контейнер.
#    RAM хватает с запасом: 128GB unified, веса моделей 16-21GB.
# 2. Указать судью:
export JUDGE_MODEL=unsloth/Qwen3.8-27B-MTP   # dense, с mmproj → и VL-судья
./run.sh
```

## Каталоги

- `datasets/` — датасеты и манифесты (коммитятся: детерминированный воспроизводимый trainset).
- `compiled/` — артефакты компиляции (тоже коммитятся — история улучшений промптов).
