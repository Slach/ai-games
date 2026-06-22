# Push-Based Briefing Delivery

## Problem

Currently, briefings are delivered via a **polling loop** in `telegram-bot/bot.py` that checks
`/game/poll/{player_id}` every 30 seconds. This causes:

1. **Race condition** — polling delivers turn content to players *before* the
   "game started" / "game continued" messages, and can duplicate content that
   GM commands already send explicitly
2. **30-second latency** for scheduled generation (players wait up to 30s for
   polling to discover new content)
3. **Complex dedup logic** (`_last_sent_briefing_day`, `_restart_suppressed_players`)
   to work around races between polling and explicit delivery
4. **Unnecessary load** from repeated HTTP calls to `/game/poll/{player_id}`

## Goal

Replace polling with **push delivery**: the service that generates content
(`game-server-api`) is also the service that notifies players, by calling
`telegram-bot` directly.

## Architecture

### Current (broken)

```
Scheduler ──POST /admin/generate-day──→ game-server-api
GM cmd    ──POST /admin/start-game───→ game-server-api

                                        polling loop (30s)
telegram-bot ──GET /game/poll/{id}──→ game-server-api
              ← briefing data
              → bot.send_message() (Telegram)
```

### Target

```
Scheduler ──POST /admin/generate-day──→ game-server-api
GM cmd    ──POST /admin/start-game───→ game-server-api

            game-server-api генерирует LLM → ComfyUI → сохраняет в БД
                               ↓
                    POST /push/briefings ── exponential retry ──→ telegram-bot
                                                                  ↓
                                                         bot.send_message() (Telegram)
```

**Ключевой принцип:** `game-server-api` — единственный источник правды.
Он знает, какой контент сгенерирован и кому его слать. После сохранения
в БД он вызывает `telegram-bot` и говорит: "отправь это этим игрокам".

## Компоненты

### 1. telegram-bot: HTTP-сервер для push

Новый файл `telegram-bot/push_server.py`. aiohttp-сервер на порту 9090
(отдельный порт от aiogram).

#### Эндпоинт: `POST /push/briefings`

```jsonc
{
  "game_id": "default_game",
  "day": 1,
  "players": [
    {
      "player_id": 12345,
      "briefing": "Личный брифинг...",
      "choices": [
        {"id": "action_1", "text": "Действие 1", "consequence": "скрыто"},
        {"id": "action_2", "text": "Действие 2", "consequence": "скрыто"}
      ],
      "comic_url": "http://...",       // опционально
      "scene_url": "http://..."        // опционально
    }
  ],
  "bridge_image_url": "http://...",    // опционально — мостик (только при старте/рестарте)
  "mission": {                         // опционально — миссия (только при старте/рестарте)
    "name": "Название",
    "description": "Описание"
  },
  "crew_dialogues": [                  // диалоги NPC для этого дня
    {"npc": "Captain", "dialogue": "..."},
    {"npc": "Engineer", "dialogue": "..."}
  ],
  "is_first_turn": false               // true = это первый ход, нужно отправить bridge+mission
}
```

#### Логика обработки

Для каждого `player_id`:

1. **Dedup**: если `_last_sent_briefing_day[player_id] == day` → пропустить (уже отправлено)
2. Если `is_first_turn`:
   - Скачать `bridge_image_url` → `bot.send_photo()`
   - Отправить текст миссии → `bot.send_message()`
3. Если есть `comic_url` → скачать → `bot.send_photo()`
4. Если есть `scene_url` → скачать → `bot.send_photo()`
5. Построить текст брифинга + клавиатуру с действиями → `bot.send_message()`
6. Запомнить `_last_sent_briefing_day[player_id] = day`
7. Вернуть `{"status": "ok", "sent": [player_ids]}`

#### Идемпотентность

Повторный вызов с теми же `game_id` + `day` + `player_id`:

- Проверяет `_last_sent_briefing_day`
- Если уже отправлено → возвращает `{"status": "ok", "already_sent": true}`
- Ничего не дублирует

#### Где запускать сервер

В `main()` бота, после инициализации `Bot` и `Dispatcher`:

```python
from push_server import start_push_server

push_runner = await start_push_server(bot)
# ...
# При завершении: push_runner.cleanup()
```

### 2. game-server-api: push-клиент с exponential retry

Новый файл `game-server-api/push_client.py`.

```python
# Настройки из окружения
TELEGRAM_BOT_PUSH_URL = os.getenv(
    "TELEGRAM_BOT_PUSH_URL",
    "http://telegram-bot:9090/push/briefings"
)
PUSH_MAX_RETRIES = int(os.getenv("PUSH_MAX_RETRIES", "7"))
PUSH_BASE_DELAY = float(os.getenv("PUSH_BASE_DELAY", "1.0"))  # секунд
```

```python
async def push_briefings(
    game_id: str,
    day: int,
    players_briefings: list[dict],
    bridge_url: str | None = None,
    mission: dict | None = None,
    crew_dialogues: list | None = None,
    is_first_turn: bool = False,
) -> bool
```

#### Exponential backoff

```
attempt  delay
  1       1.0s
  2       2.0s
  3       4.0s
  4       8.0s
  5      16.0s
  6      32.0s
  7      60.0s
  max    123s total
```

Jitter: `random.uniform(0, delay)` на каждую попытку.
Таймаут на HTTP-вызов: 30 секунд.

#### Куда вставить push

После того, как сгенерирован и сохранён весь контент для дня:

| Эндпоинт | Когда вызывать push |
|----------|-------------------|
| `/admin/start-game` | После создания game_day + briefings, fire-and-forget |
| `/admin/continue-game` | После создания game_day + briefings, fire-and-forget |
| `/admin/regenerate-turn` | После регенерации, fire-and-forget |
| `/admin/generate-day` (планировщик) | После создания дня, fire-and-forget |

Fire-and-forget = `asyncio.create_task(push_briefings(...))` с try/except.

### 3. Удалить polling loop из telegram-bot

Из `bot.py` удаляются:

| Функция/переменная | Причина |
|-------------------|---------|
| `polling_loop()` | Заменён push-сервером |
| `poll_game_updates()` | Больше не нужен |
| `_process_game_update_for_player()` | Логика переехала в push_server.py |
| `_restart_suppressed_players` | Гонки больше нет |
| `_send_game_briefings()` | Заменён вызовом push из API |
| `_send_bridge_and_mission()` | Логика переехала в push_server.py |

Остаются:

- `_last_sent_briefing_day` — dedup в push-сервере
- `_mark_briefing_sent()` — используется push-сервером

### 4. Обновить GM команды

`cmd_gm_start_game`, `cmd_gm_continue_game`, `cmd_gm_restart_game`:

- Убрать явную отправку `_send_game_briefings()` и `_send_bridge_and_mission()`
- Убрать очистку pending_updates
- Убрать `_restart_suppressed_players`
- После API просто показать GM сообщение "Игра запущена" — брифинги разошлются через push

### 5. docker-compose.yaml

Добавить для `telegram-bot`:

```yaml
telegram-bot:
  # ... существующие настройки ...
  ports:
    - "127.0.0.1:9090:9090"   # push-эндпоинт
```

Добавить для `game-server-api`:

```yaml
game-server-api:
  # ... существующие настройки ...
  environment:
    # ... существующие ...
    - TELEGRAM_BOT_PUSH_URL=http://telegram-bot:9090/push/briefings
    - PUSH_MAX_RETRIES=7
    - PUSH_BASE_DELAY=1.0
```

## Порядок доставки при старте игры

После `/admin/start-game` API:

1. Генерирует NPC, миссию, bridge image
2. Генерирует global_circumstances и briefings для всех участников (игроки + NPC)
3. Сохраняет всё в БД
4. Вызывает `push_briefings(game_id, day, players, bridge_url, mission, is_first_turn=True)`
5. Бот получает push и отправляет:
   - bridge image + mission → всем (bridge + миссия)
   - comic/scene → каждому игроку (картинка хода)
   - briefing text + keyboard → каждому игроку (текст хода + кнопки)

Игрок видит (правильный порядок):

```
1. ✅ Игра запущена! (сообщение от GM)
2. [картинка мостика] "Мостик корабля" (bridge)
3. "Название миссии, описание, цели" (mission info)
4. [картинка хода] (turn image)
5. "Ход 1: брифинг + кнопки" (briefing + choices)
```

## Файлы для изменений

| Файл | Изменение |
|------|-----------|
| **telegram-bot/push_server.py** | 🆕 Новый файл: aiohttp-сервер с `/push/briefings` |
| **telegram-bot/bot.py** | Удалить polling loop, `_restart_suppressed_players`, `_send_game_briefings`, `_send_bridge_and_mission`. Запустить push-сервер в `main()` |
| **game-server-api/push_client.py** | 🆕 Новый файл: клиент с exponential retry |
| **game-server-api/main.py** | Добавить вызовы `push_briefings()` в `/admin/start-game`, `/admin/continue-game`, `/admin/regenerate-turn`, `/admin/generate-day` |
| **docker-compose.yaml** | Добавить порт 9090 для telegram-bot, переменные окружения для push |
| **docs/RULES_RU.md** | Обновить архитектурную диаграмму — убрать polling, добавить push |
| **docs/RULES_EN.md** | То же самое |
| **AGENTS.md** | Обновить раздел архитектуры |

## Размерность

- push_server.py: ~120 строк
- push_client.py: ~80 строк
- bot.py: убирается ~200 строк, добавляется ~20 строк (запуск push-сервера)
- main.py: добавляется ~20 строк (вызовы push)
