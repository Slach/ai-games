
# AI Games

## Technology Stack

- **Python** - Primary backend language for game logic and AI integration
- **TypeScript** - Frontend and client-side development for Telegram Mini App

### Python Code Style

- **All imports must be at the top of the file.** Never place `import` or
  `from ... import` statements inside functions, methods, `if` blocks,
  `try/except` blocks, or any other conditional/local scope. This ensures
  clarity, consistency, and avoids hidden import paths that make code harder
  to read and debug.

- **Use actual UTF-8 characters, not `\uXXXX` escape sequences.** Never write
  `\u041a\u043e\u0440\u043f\u0443\u0441` — write `Корпус` directly.
  Unicode escapes make source files unreadable and harder to maintain.
  Python handles UTF-8 source files natively; there is no technical reason to
  use escape sequences.

### AI Systems

- **OpenAI API** - For model-driven game master functionality. Currently
  implemented and handling game state management, narrative progression,
  NPC dialogue generation, and content prompt generation via OpenAI API.

### Content Generation

- **[ComfyUI](https://github.com/comfyanonymous/ComfyUI)** - GPU-accelerated
  content generation backend for images, videos, and comics.
  Called directly via HTTP API.

## Deployment

- Always use PYTHONDONTWRITEBYTECODE=1 for running python code
- The system will be deployed using docker compose
- ComfyUI running as a service that can be called by the Python code to
  generate content on demand.

## Important Rules

- **LLM prompts go in `prompts.py`.** All system prompts, user prompts,
  onboarding questions, outcome generation, NPC dialogue, and daily
  briefings must be defined in `game-server/prompts.py`. Never embed
  prompt strings in handlers, routers, or other modules.

- **Use `language.py` constants, never raw strings.** Never compare against
  `== 'ru'` or `== 'en'` directly — always import and use `LANGUAGE_RU` /
  `LANGUAGE_EN` from `telegram-bot/language.py` (or `game-server/language.py`
  depending on the service). This keeps locale checks consistent and
  grep-friendly.

- **Fix causes, not symptoms.** Never add workarounds, post-processing, or
  data-cleaning functions that paper over incorrect output from an upstream
  source (LLM, external API, etc.). Instead, fix the upstream — correct the
  prompt, fix the API caller, or adjust the data format at the source. A
  `_clean_*` or `_sanitize_*` shim means you chose to treat the symptom
  instead of the disease.

- **Never swallow exceptions silently.** This includes:
  - `contextlib.suppress(...)` — silently swallows errors and makes debugging impossible.
  - `except Exception: pass` / `except KeyError: pass` — same effect, same problem.
  Always use explicit `try`/`except` blocks with at minimum `logger.warning(..., exc_info=True)`.
  If an exception is genuinely harmless (e.g. `.pop()` on a key that may not exist),
  either check with `.get()` first or add a comment explaining why the log is omitted.

- **Every `logger.error(...)` must include a stacktrace.** Never log an
  error without showing where it came from:
  - Inside `except` blocks: add `exc_info=True` to include the exception traceback.
  - Outside `except` blocks (logical errors, validation failures): add
    `stack_info=True` to print the current call stack.
  - When logging a saved exception after retries: pass `exc_info=<variable>`
    with the saved exception object.

- Avoid over-engineering. Only make changes that are directly requested or clearly necessary. Keep solutions
simple and focused.

- Don't add features, refactor code, or make "improvements" beyond what was asked. A bug fix doesn't need surrounding code cleaned up.
A simple feature doesn't need extra configurability. Don't add docstrings,
comments, or type annotations to code you didn't change. Only add comments where the logic isn't self-
evident.

- Don't add error handling, fallbacks, or validation for scenarios that can't happen. Trust internal code and
framework guarantees. Only validate at system boundaries (user input, external APIs).
Don't use feature flags or backwards-compatibility shims when you can just change the code.

- Don't create helpers, utilities, or abstractions for one-time operations. Don't design for hypothetical
future requirements. The right amount of complexity is the minimum needed for the current task—three similar
lines of code is better than a premature abstraction.

- Avoid backwards-compatibility hacks like renaming unused _vars, re-exporting types, adding // removed
comments for removed code, etc. If you are certain that something is unused, you can delete it completely.

- **llama.cpp is an external service** - Do not add llama.cpp service to
  docker-compose.yaml. It's already running on the spark-network.

- **spark-network is external** - The Docker network `spark-network` is
  created externally. Do not try to create it in docker-compose.

- **Use health checks** - Always use `condition: service_healthy` for
  service dependencies when possible.

- **game-scheduler for debugging** - The `game-scheduler` scheduler can be run
  manually with `docker compose run --rm game-scheduler` for local debugging
  without Telegram bot.

- **Renaming files** - Always use `git mv <old> <new>` instead of `mv` +
  `git rm` to preserve file history.

- **Database schema changes** — All changes to `database.py` must be done
  through the `MIGRATIONS` list. Never add columns directly to `CREATE TABLE`
  statements — only add them via a new migration entry. This keeps existing
  databases in sync with fresh ones. See comment at the `MIGRATIONS`
  definition for the rationale.

- **`game_id` is always passed explicitly, never defaulted.** Never give
  `game_id` a default value — no `game_id: str = "default_game"` (or any
  other default) in a function signature, and no `Query("default_game")`
  default on an endpoint. Every function that takes `game_id` and every
  call site (bot, scheduler, internal) must pass it explicitly,
  positionally or as a keyword argument. A silent `default_game` default
  once routed a player's action to the wrong game's turn: the bot fetched
  the current turn for `default_game` (a stale, completed game) but
  submitted the action for the player's real game — 404 "No active game
  turn". The only legitimate uses of the literal `"default_game"` are
  seeding it in `init_db()` and the global shared bucket for loading/splash
  images (`_generate_loading_images` / `_generate_splash_images`), and even
  there it is passed explicitly as `game_id="default_game"`. When a
  `game_id` parameter would otherwise sit after an optional parameter,
  make it keyword-only (`*, game_id: str`) instead of reordering — this
  keeps existing positional callers valid.

- **Format datetime with `strftime`, never string concatenation.**
  When displaying a datetime with a timezone label, use `dt.strftime("%Y-%m-%d %H:%M %Z")` —
  `%Z` produces `UTC`, `MSK`, etc. directly from the `tzinfo` object.
  Never do `strftime(...) + " UTC"` or any other manual timezone string concatenation —
  it hardcodes a timezone that may be wrong and defeats the purpose of being timezone-aware.

- **Language model.** Server-side logging is always in English. Telegram UI
  messages use the player's stored language preference (`player_store.py`).
  Game content (narrative, NPC dialogue) uses the language set when the game
  was created. There is no "bot language" or "game language" env var —
  language is always determined per-player or per-game.

## Useful Commands

### Apply code changes without wiping data (mutations, business logic, etc.)

Stops only the target containers (preserving DB volumes and ComfyUI outputs),
rebuilds them from the current source, and starts fresh containers.

```bash
docker compose --progress=plain stop telegram-bot game-scheduler game-server --timeout=1 && docker compose --progress=plain up -d --force-recreate telegram-bot game-scheduler game-server
```

### Full wipe — destroy all game data, ComfyUI outputs, and rebuild from scratch

```bash
docker compose down \
  && rm -rfv ./*/*.db \
  && rm -fv ./comfyui/output/*_.png \
  && docker compose up -d --build
```

After running this, you must generate a new turn via Telegram:
`/gm_start <game_id>` (first turn) then `/gm_continue <game_id>`
for subsequent turns, since all sessions/game state is deleted.

### Reading service logs (two sources — use the right one)

Logs live in **two places** with different coverage. When debugging an event,
pick the source that actually contains it:

1. **`docker compose logs`** — container stdout, **only since the last
   container restart**. Fine for live/recent activity, but useless for
   anything older than the last `up --force-recreate` (game creation, NPC
   creation, events from previous days).

   ```bash
   docker compose logs -t > /tmp/compose.logs             # all services, dump to file
   docker compose logs -t telegram-bot | grep cmd_team    # one service, filtered
   ```

2. **Daily `YYYY-MM-DD.log` files** — each service mirrors its full log to a
   date-stamped file that **survives restarts and spans days**. This is where
   the real history lives. Files are written into each service directory:

   - `telegram-bot/YYYY-MM-DD.log`
   - `game-server/YYYY-MM-DD.log`
   - `game-scheduler/YYYY-MM-DD.log`
   - ComfyUI: `comfyui/user/comfyui.log` (current) plus `comfyui.prev.log`,
     `comfyui.prev2.log` (rotated)

   ```bash
   # Search one service's full history for a specific day
   grep -nE "cmd_team|NPC_AVATAR|1553177251" game-server/2026-07-06.log
   # All services, today's files, errors only
   grep -Hn "ERROR" */"$(date +%F)".log
   ```

Rule of thumb: first locate the event's timestamp in the daily `.log` file
(full history), then optionally cross-reference `docker compose logs` for the
current session's detail.

### Run tests

Activate the virtual environment and use `unittest` from `game-server/`:

```bash
cd game-server && ../.venv/bin/python -m unittest discover -s tests
```

Or run specific test modules:

```bash
cd game-server && ../.venv/bin/python -m unittest tests.test_game_rules tests.test_mission_db
```
