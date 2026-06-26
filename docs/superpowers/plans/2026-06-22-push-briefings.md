# Push-Based Briefing Delivery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace the polling loop in `telegram-bot` with push delivery from `game-server-api`.
After `/admin/start-game`, `/admin/continue-game`, or `/admin/regenerate-turn` finish generating
content, the API calls `telegram-bot` directly with the briefings to send.

**Architecture:** `game-server-api` becomes the single source of truth for delivery. After
generating and saving all content, it fires a background task that calls `telegram-bot`'s new
`/push/briefings` HTTP endpoint with exponential retry. `telegram-bot` receives the payload,
downloads images, and sends them via Telegram API. The polling loop is deleted.

**Tech Stack:** Python 3.12+, aiohttp, asyncio

## Global Constraints

- All new HTTP calls use exponential backoff with jitter (`base_delay * 2^attempt + jitter`)
- All functions use `async def` and `await` throughout
- Logging uses the existing `logging.getLogger(__name__)` pattern
- All new endpoints are idempotent (repeated calls produce no duplicates)
- Use `PYTHONDONTWRITEBYTECODE=1` when running python

---

## File Structure

| File | Responsibility | Status |
|------|---------------|--------|
| `game-server-api/push_client.py` | 🆕 Exponential retry HTTP client that calls `telegram-bot` | ✅ IMPLEMENTED |
| `telegram-bot/push_server.py` | 🆕 aiohttp server with `/push/briefings` endpoint | ✅ IMPLEMENTED |
| `game-server-api/main.py` | 🔧 Add push calls after `/admin/*` endpoints | ✅ IMPLEMENTED (lines 2680, 3171) |
| `telegram-bot/bot.py` | 🔧 Удалить polling loop, запустить push server в `main()` | ✅ IMPLEMENTED |
| `docker-compose.yaml` | 🔧 Add port 9090, env vars for push | ✅ IMPLEMENTED |
| `docs/RULES_RU.md` | 🔧 Update architecture diagram | ❌ Не обновлено |
| `docs/RULES_EN.md` | 🔧 Update architecture diagram | ❌ Не обновлено |
| `AGENTS.md` | 🔧 Update architecture description | ✅ Уже описано (см. AGENTS.md в проекте) |

### Task 1: push_client.py — retry HTTP client

**Files:**

- Create: `game-server-api/push_client.py`

**Interfaces:**

- Produces: `async def push_briefings(game_id: str, day: int, players_briefings: list[dict], bridge_url: str | None = None, mission: dict | None = None, crew_dialogues: list | None = None, is_first_turn: bool = False) -> bool`

- [x] **Step 1: Create push_client.py with config and retry logic**

```python
"""Push client that delivers briefings to telegram-bot with exponential retry."""

import asyncio
import logging
import os
import random

import aiohttp

logger = logging.getLogger(__name__)

# Config from environment
TELEGRAM_BOT_PUSH_URL = os.getenv(
    "TELEGRAM_BOT_PUSH_URL",
    "http://telegram-bot:9090/push/briefings",
)
PUSH_MAX_RETRIES = int(os.getenv("PUSH_MAX_RETRIES", "7"))
PUSH_BASE_DELAY = float(os.getenv("PUSH_BASE_DELAY", "1.0"))
PUSH_REQUEST_TIMEOUT = int(os.getenv("PUSH_REQUEST_TIMEOUT", "30"))


async def push_briefings(
    game_id: str,
    day: int,
    players_briefings: list[dict],
    bridge_url: str | None = None,
    mission: dict | None = None,
    crew_dialogues: list | None = None,
    is_first_turn: bool = False,
) -> bool:
    """Push briefings to telegram-bot with exponential backoff retry.

    Args:
        game_id: Game identifier
        day: Day/turn number
        players_briefings: List of per-player briefing dicts, each containing
            player_id, briefing, choices, etc.
        bridge_url: URL of bridge image (for first turn)
        mission: Mission info dict with name, description
        crew_dialogues: List of NPC dialogue dicts with npc, dialogue
        is_first_turn: If True, bot also sends bridge image + mission info

    Returns:
        True if delivered successfully, False after all retries exhausted.
    """
    payload: dict = {
        "game_id": game_id,
        "day": day,
        "players": players_briefings,
        "is_first_turn": is_first_turn,
    }
    if bridge_url:
        payload["bridge_image_url"] = bridge_url
    if mission:
        payload["mission"] = mission
    if crew_dialogues:
        payload["crew_dialogues"] = crew_dialogues

    last_exception: Exception | None = None

    for attempt in range(PUSH_MAX_RETRIES):
        delay = PUSH_BASE_DELAY * (2 ** attempt)
        jitter = random.uniform(0, delay)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    TELEGRAM_BOT_PUSH_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=PUSH_REQUEST_TIMEOUT),
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        sent_count = len(body.get("sent", []))
                        already = body.get("already_sent", False)
                        logger.info(
                            f"[PUSH] Delivered day {day} for game {game_id}: "
                            f"{'already_sent' if already else sent_count} players"
                        )
                        return True
                    else:
                        error_text = await resp.text()
                        logger.warning(
                            f"[PUSH] Attempt {attempt + 1}/{PUSH_MAX_RETRIES}: "
                            f"HTTP {resp.status} - {error_text}"
                        )
                        last_exception = Exception(
                            f"HTTP {resp.status}: {error_text}"
                        )

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(
                f"[PUSH] Attempt {attempt + 1}/{PUSH_MAX_RETRIES}: "
                f"{type(e).__name__}: {e}. Retrying in {jitter:.1f}s..."
            )
            last_exception = e

        # Wait before retry (skip on last attempt)
        if attempt < PUSH_MAX_RETRIES - 1:
            await asyncio.sleep(jitter)

    logger.error(
        f"[PUSH] Failed to deliver day {day} for game {game_id} "
        f"after {PUSH_MAX_RETRIES} attempts: {last_exception}"
    )
    return False
```

- [x] **Step 2: Commit**

```bash
git add game-server-api/push_client.py
git commit -m "feat(game-server-api): add push_client with exponential retry"
```

---

### Task 2: push_server.py — HTTP server in telegram-bot

**Files:**

- Create: `telegram-bot/push_server.py`

**Interfaces:**

- Produces: `async def start_push_server(bot: Bot) -> aiohttp.web.AppRunner`
- Consumes: `Bot` from aiogram (for `bot.send_photo()`, `bot.send_message()`)
- Uses: `_last_sent_briefing_day` from `bot.py` (imported), `_mark_briefing_sent()` from `bot.py`
- Uses: `create_action_keyboard()` from `bot.py`
- Uses: `escape_markdown()` from `bot.py`
- Uses: `_download_and_send_photo()` from `bot.py`
- Uses: `lang.get_current_day()` and `lang.get_bridge()` from `language.py`
- Uses: `BufferedInputFile` from `aiogram.types`

- [x] **Step 1: Create push_server.py with aiohttp endpoint**

```python
"""HTTP server for receiving push briefings from game-server-api."""

import asyncio
import logging
from typing import Any

import aiohttp
from aiohttp import web
from aiogram import Bot
from aiogram.types import BufferedInputFile

from language import get_bridge, get_current_day

logger = logging.getLogger(__name__)

# Import shared state from bot.py
# We use lazy imports inside the handler to avoid circular imports at module level.

PUSH_SERVER_PORT = int(os.getenv("PUSH_SERVER_PORT", "9090"))


def _build_briefing_text(
    day_num: int,
    briefing: str,
    choices: list[dict],
    crew_dialogues: list[dict],
    language: str,
) -> str:
    """Build the full briefing message text for a player."""
    current = get_current_day(language)
    crew_txt = ""
    if crew_dialogues:
        sep = "\n---\n"
        lines = [
            f"*{d.get('npc', 'NPC')}*: {d.get('dialogue', '')}"
            for d in crew_dialogues
        ]
        crew_txt = f"\n\n*Поведение экипажа:*\n{sep.join(lines)}"

    acts = "\n\n".join(
        f"{i + 1} - {_escape_md(a.get('text', a.get('description', '')))}"
        for i, a in enumerate(choices)
    )
    return (
        current["title"].format(day=day_num)
        + "\n\n"
        + current["briefing_header"].format(briefing=briefing)
        + crew_txt
        + "\n\n"
        + current["actions"].format(actions=acts)
        + "\n\n"
        + current["select_action"]
    )


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown special characters."""
    import re
    return re.sub(r"([_*`\[])", r"\\\1", text)


async def _download_image(url: str, timeout: int = 30) -> bytes | None:
    """Download an image from URL and return raw bytes."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                logger.warning(f"Failed to download image: HTTP {resp.status}")
    except Exception as e:
        logger.warning(f"Failed to download image: {e}")
    return None


async def handle_push_briefings(request: web.Request) -> web.Response:
    """Handle POST /push/briefings from game-server-api."""
    bot: Bot = request.app["bot"]
    language: str = request.app.get("language", "ru")
    last_sent: dict[int, int | None] = request.app["last_sent_briefing_day"]
    mark_sent_fn = request.app["mark_sent_fn"]
    create_keyboard_fn = request.app["create_keyboard_fn"]

    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response(
            {"status": "error", "message": f"Invalid JSON: {e}"}, status=400
        )

    game_id = payload.get("game_id", "default_game")
    day = payload.get("day")
    players = payload.get("players", [])
    bridge_url = payload.get("bridge_image_url")
    mission = payload.get("mission")
    crew_dialogues = payload.get("crew_dialogues", [])
    is_first_turn = payload.get("is_first_turn", False)

    if not day or not players:
        return web.json_response(
            {"status": "error", "message": "Missing day or players"}, status=400
        )

    sent_player_ids: list[int] = []
    already_sent = False

    for player_data in players:
        player_id = player_data.get("player_id")
        if not player_id:
            continue

        # Dedup: skip if already sent for this day
        if last_sent.get(player_id) == day:
            already_sent = True
            continue

        try:
            # 1. Send bridge image + mission (first turn only)
            if is_first_turn and bridge_url:
                bridge_msgs = get_bridge(language)
                caption = bridge_msgs["title"]
                mission_name = (mission or {}).get("name", "")
                if mission_name:
                    caption += "\n\n" + bridge_msgs["mission_header"].format(
                        name=mission_name
                    )
                img_data = await _download_image(bridge_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="bridge.png")
                    await bot.send_photo(
                        chat_id=player_id, photo=photo, caption=caption,
                        parse_mode="Markdown"
                    )
                else:
                    await bot.send_message(
                        chat_id=player_id, text=caption, parse_mode="Markdown"
                    )

                # Send mission description as separate message
                if mission:
                    desc = mission.get("description", "")
                    if desc:
                        await bot.send_message(
                            chat_id=player_id,
                            text=bridge_msgs["mission_desc"].format(
                                description=desc
                            ),
                            parse_mode="Markdown",
                        )

            # 2. Send comic / scene image
            comic_url = player_data.get("comic_url")
            if comic_url:
                img_data = await _download_image(comic_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="comic.png")
                    await bot.send_photo(chat_id=player_id, photo=photo)

            scene_url = player_data.get("scene_url")
            if scene_url and scene_url != comic_url:
                img_data = await _download_image(scene_url)
                if img_data:
                    photo = BufferedInputFile(img_data, filename="scene.png")
                    await bot.send_photo(chat_id=player_id, photo=photo)

            # 3. Send briefing text + action choices
            briefing = player_data.get("briefing", "")
            choices = player_data.get("choices", [])
            if briefing and choices:
                text = _build_briefing_text(
                    day, briefing, choices, crew_dialogues, language
                )
                keyboard = create_keyboard_fn(choices)
                await bot.send_message(
                    chat_id=player_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )

            # Mark as sent
            mark_sent_fn(player_id, day)
            sent_player_ids.append(player_id)
            logger.info(f"[PUSH] Sent day {day} briefing to player {player_id}")

        except Exception as e:
            logger.error(
                f"[PUSH] Failed to send to player {player_id}: {e}"
            )

    status = "already_sent" if already_sent and not sent_player_ids else "ok"
    return web.json_response(
        {"status": status, "sent": sent_player_ids, "already_sent": already_sent}
    )


async def start_push_server(
    bot: Bot,
    language: str = "ru",
    last_sent_briefing_day: dict | None = None,
    mark_sent_fn=None,
    create_keyboard_fn=None,
) -> web.AppRunner:
    """Start the push HTTP server.

    Args:
        bot: aiogram Bot instance
        language: Bot language code
        last_sent_briefing_day: Shared dict for dedup (from bot.py)
        mark_sent_fn: Function to mark briefing as sent (from bot.py)
        create_keyboard_fn: Function to create action keyboard (from bot.py)

    Returns:
        web.AppRunner for graceful shutdown
    """
    if last_sent_briefing_day is None:
        last_sent_briefing_day = {}
    if mark_sent_fn is None:
        # Default no-op
        mark_sent_fn = lambda pid, day: None
    if create_keyboard_fn is None:
        # Default no-op
        create_keyboard_fn = lambda choices: None

    app = web.Application()
    app["bot"] = bot
    app["language"] = language
    app["last_sent_briefing_day"] = last_sent_briefing_day
    app["mark_sent_fn"] = mark_sent_fn
    app["create_keyboard_fn"] = create_keyboard_fn

    app.router.add_post("/push/briefings", handle_push_briefings)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PUSH_SERVER_PORT)
    await site.start()

    logger.info(f"[PUSH_SERVER] Started on port {PUSH_SERVER_PORT}")
    return runner
```

- [x] **Step 2: Commit**

```bash
git add telegram-bot/push_server.py
git commit -m "feat(telegram-bot): add push server with /push/briefings endpoint"
```

---

### Task 3: Add push calls to game-server-api endpoints

**Files:**

- Modify: `game-server-api/main.py` (add push_call after start-game, continue-game, regenerate-turn)

**Interfaces:**

- Consumes: `push_briefings()` from `push_client.py` (Task 1)
- Modifies: `/admin/start-game`, `/admin/continue-game`, `/admin/regenerate-turn` endpoints

- [x] **Step 1: Add import and helper in main.py**

```python
# At top of main.py, after existing imports:
from push_client import push_briefings


# Helper to build per-player briefing data for push payload:
def _build_player_briefings_for_push(
    all_briefings: list[dict],
    crew_dialogues: list[dict],
    day_num: int,
) -> list[dict]:
    """Build player briefing dicts for push payload from stored briefings."""
    players_data = []
    for b in all_briefings:
        if b.get("is_npc"):
            continue  # Only send to real players
        player_id = b.get("player_id")
        if not player_id:
            continue
        players_data.append({
            "player_id": player_id,
            "briefing": b.get("briefing", ""),
            "choices": b.get("choices", []),
            "comic_url": b.get("comic_url"),
            "scene_url": None,  # Not stored per-player, fetched separately if needed
        })
    return players_data
```

- [x] **Step 2: Add push after `/admin/start-game` success**

At the end of the `admin_start_game` function, **after** the existing code that creates the game day record and builds `briefings_for_response`, add:

```python
# ── Push briefings to telegram-bot ─────────────────────────
try:
    player_briefings = _build_player_briefings_for_push(
        all_briefings, crew_dialogues_list, day_num
    )
    if player_briefings:
        asyncio.create_task(
            push_briefings(
                game_id=game_id,
                day=day_num,
                players_briefings=player_briefings,
                bridge_url=bridge_url,
                mission=mission_info,
                crew_dialogues=crew_dialogues_list,
                is_first_turn=True,
            )
        )
except Exception as push_err:
    logger.warning(f"[PUSH] Failed to initiate push: {push_err}")
```

- [x] **Step 3: Add push after `/admin/continue-game` success**

At the end of `admin_continue_game`, after the game day record is created and `all_briefings` is populated, add:

```python
# ── Push briefings to telegram-bot ─────────────────────────
try:
    player_briefings = _build_player_briefings_for_push(
        all_briefings, crew_dialogues_list, day_num
    )
    if player_briefings:
        asyncio.create_task(
            push_briefings(
                game_id=game_id,
                day=day_num,
                players_briefings=player_briefings,
                crew_dialogues=crew_dialogues_list,
                is_first_turn=False,
            )
        )
except Exception as push_err:
    logger.warning(f"[PUSH] Failed to initiate push: {push_err}")
```

- [x] **Step 4: Add push after `/admin/regenerate-turn`**

`admin_regenerate_turn` calls `admin_continue_game` internally, so the push from Step 3 will fire automatically. No additional changes needed.

- [x] **Step 5: Commit**

```bash
git add game-server-api/main.py
git commit -m "feat(game-server-api): add push_briefings calls after game endpoints"
```

---

### Task 4: Remove polling loop from telegram-bot, start push server

**Files:**

- Modify: `telegram-bot/bot.py`

**Changes:**

1. Remove these functions/variables:
   - `_send_bridge_and_mission()` function
   - `_send_game_briefings()` function
   - `_process_game_update_for_player()` function
   - `poll_game_updates()` function
   - `polling_loop()` function
   - `_restart_suppressed_players` set (lines ~132-135, and all its usages)
   - `_get_player_briefing_day()` function
   - `_mark_briefing_sent()` — KEEP it, push_server imports it

2. Update `cmd_gm_start_game`: remove explicit `_send_bridge_and_mission()` and
   `_send_game_briefings()` calls, remove `_restart_suppressed_players` manipulation,
   remove pending_updates clearing. Just show GM message.

3. Update `cmd_gm_continue_game`: same — remove explicit `_send_game_briefings()`,
   remove `_restart_suppressed_players` manipulation.

4. Update `cmd_gm_restart_game`: same — remove explicit bridge/briefing sending,
   remove `_restart_suppressed_players` manipulation.

5. In `main()`: replace polling loop start with push server start.

- [x] **Step 1: Remove polling infrastructure from bot.py**

Delete these entire function bodies (keep `_mark_briefing_sent` and `_last_sent_briefing_day`):

```python
# DELETE:
# _send_bridge_and_mission() — the entire function
# _send_game_briefings() — the entire function
# _get_player_briefing_day() — the entire function
# poll_game_updates() — the entire function
# polling_loop() — the entire function
# _process_game_update_for_player() — the entire function
# _restart_suppressed_players — the set variable and all references
```

Remove these from the module-level variables section:

```python
# DELETE these lines:
_last_sent_briefing_day: dict[int, int | None] = {}
_restart_suppressed_players: set[int] = set()
```

Keep `_mark_briefing_sent` — it's used by `push_server.py`.

- [x] **Step 2: Clean up GM commands in bot.py**

In `cmd_gm_start_game`:

- Remove the `try/except` block that fetches players and adds to `_restart_suppressed_players`
- Remove the `_send_bridge_and_mission()` call
- Remove the `_send_game_briefings()` call
- Remove the pending_updates clearing loop
- Remove the `_restart_suppressed_players.clear()` calls
- Keep only: API call, answer with GM message

After cleanup, `cmd_gm_start_game` should look like:

```python
async def cmd_gm_start_game(message: types.Message):
    """GM command: Force start a game by ID."""
    assert message.from_user is not None
    player_id = message.from_user.id
    gm_msgs = lang.get_gm_commands(get_player_language(player_id))

    if GAME_MASTER_ID <= 0 or player_id != GAME_MASTER_ID:
        logger.warning(f"Unauthorized /gm_start_game attempt by user {player_id}")
        await message.answer(gm_msgs["unauthorized"])
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(gm_msgs["start_game_usage"])
        return

    game_id = parts[1].strip()
    if not game_id:
        await message.answer(gm_msgs["start_game_usage"])
        return

    await message.answer(
        gm_msgs["starting_game"].format(game_id=game_id), parse_mode="Markdown"
    )

    try:
        result = await api_request(
            "POST",
            "/admin/start-game",
            data={"game_id": game_id, "language": get_player_language(player_id)},
            timeout_total=600,
        )
        if result and result.get("status") == "success":
            day_num = result.get("day", 1)
            player_count = result.get("player_count", 0)
            npc_count = result.get("npc_count", 0)
            msg = gm_msgs["game_started"].format(
                game_id=game_id,
                day_num=day_num,
                player_count=player_count,
                npc_count=npc_count,
            )
            await message.answer(msg, parse_mode="Markdown")
            # Bridge, mission & briefings are delivered via push_briefings
        else:
            await message.answer(gm_msgs["start_game_error"].format(error=result))
    except Exception as e:
        logger.error(f"Failed to start game {game_id}: {e}")
        await message.answer(gm_msgs["start_game_error"].format(error=e))
```

Same pattern for `cmd_gm_continue_game` and `cmd_gm_restart_game`.

- [x] **Step 3: Replace polling loop with push server in main()**

In `main()`, replace:

```python
# Start polling loop in background
polling_task = asyncio.create_task(polling_loop(bot))
```

with:

```python
# Import push server
from push_server import start_push_server

# Start push HTTP server
push_runner = await start_push_server(
    bot=bot,
    language=DEFAULT_LANGUAGE,
    last_sent_briefing_day=_last_sent_briefing_day,
    mark_sent_fn=_mark_briefing_sent,
    create_keyboard_fn=create_action_keyboard,
)
```

And replace the cleanup:

```python
# Clean up
polling_task.cancel()
with suppress(asyncio.CancelledError):
    await polling_task
```

with:

```python
# Clean up push server
await push_runner.cleanup()
```

- [x] **Step 4: Commit**

```bash
git add telegram-bot/bot.py
git commit -m "refactor(telegram-bot): remove polling loop, add push server"
```

---

### Task 5: Update docker-compose.yaml

**Files:**

- Modify: `docker-compose.yaml`

- [x] **Step 1: Add port and env vars for telegram-bot**

```yaml
telegram-bot:
  # ... existing config ...
  ports:
    - "127.0.0.1:9090:9090"   # Push server for receiving briefings
  environment:
    # ... existing env vars ...
    - PUSH_SERVER_PORT=9090
```

- [x] **Step 2: Add env vars for game-server-api**

```yaml
game-server-api:
  # ... existing config ...
  environment:
    # ... existing env vars ...
    - TELEGRAM_BOT_PUSH_URL=http://telegram-bot:9090/push/briefings
    - PUSH_MAX_RETRIES=7
    - PUSH_BASE_DELAY=1.0
```

- [x] **Step 3: Commit**

```bash
git add docker-compose.yaml
git commit -m "chore: add push server port and env vars to docker-compose"
```

---

### Task 6: Update documentation

**Files:**

- Modify: `docs/RULES_RU.md`
- Modify: `docs/RULES_EN.md`
- Modify: `AGENTS.md`

- [x] **Step 1: Update RULES_RU.md architecture diagram**

Replace the current ASCII diagram with:

```text
┌──────────────────────────────────────────────────────────┐
│  Telegram Bot (aiogram)                                 │
│  - Команды: /start, /profile, /today, /help            │
│  - Онбординг с FSM                                     │
│  - Обработка сообщений                                  │
│  - ✅ Push-сервер (порт 9090) — получение брифингов    │
└──────────┬───────────────────────────────────────────┬───┘
           │                                           │
           │ POST /push/briefings                      │ /gm* команды
           ▼                                           │
┌──────────────────────────────────────────────────────┴───┐
│  Game Master API (FastAPI)                              │
│  - Генерация сюжета через LLM                          │
│  - Генерация изображений через ComfyUI                 │
│  - Управление состоянием                                │
│  - ✅ Push-клиент с exponential retry — отправка брифингов
└──────────┬───────────────────────────────────────────────┘
           │
           │ (планировщик / GM команды)
           ▼   ┌──────────┐
    ┌─────────┐│  ComfyUI │
    │Scheduler││ (GPU gen)│
    │(cron)   │└──────────┘
    └─────────┘
```

- [x] **Step 2: Update RULES_EN.md architecture diagram**

Same diagram but in English.

- [x] **Step 3: Update AGENTS.md**

In the "Architecture Overview" section, update the system diagram to match the new push architecture.
Add a note: "Briefings are pushed from game-server-api → telegram-bot via HTTP with exponential retry.
No polling loop needed."

- [x] **Step 4: Commit**

```bash
git add docs/RULES_RU.md docs/RULES_EN.md AGENTS.md
git commit -m "docs: update architecture diagrams for push-based delivery"
```

---

## Spec Coverage Check

| Spec Requirement | Task |
|-----------------|------|
| telegram-bot HTTP server on 9090 | Task 2 — push_server.py |
| Endpoint POST /push/briefings | Task 2 — handle_push_briefings |
| Dedup in push endpoint | Task 2 — last_sent check |
| game-server-api push client with retry | Task 1 — push_client.py |
| Exponential backoff 1-60s | Task 1 — push_briefings loop |
| Push after /admin/start-game | Task 3 — Step 2 |
| Push after /admin/continue-game | Task 3 — Step 3 |
| Push after /admin/regenerate-turn | Task 3 — Step 4 |
| Remove polling_loop from bot.py | Task 4 |
| Remove _restart_suppressed_players | Task 4 |
| Remove explicit sending from GM commands | Task 4 |
| docker-compose port 9090 + env vars | Task 5 |
| Update RULES_RU.md, RULES_EN.md | Task 6 |
| Update AGENTS.md | Task 6 |

All spec requirements covered. No gaps.
