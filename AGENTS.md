
## Technology Stack

- **Python** - Primary backend language for game logic and AI integration
- **TypeScript** - Frontend and client-side development for Telegram Mini App

#### Python Code Style

- **All imports must be at the top of the file.** Never place `import` or
  `from ... import` statements inside functions, methods, `if` blocks,
  `try/except` blocks, or any other conditional/local scope. This ensures
  clarity, consistency, and avoids hidden import paths that make code harder
  to read and debug.

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

- **Fix causes, not symptoms.** Never add workarounds, post-processing, or data-cleaning functions that paper over incorrect output from an upstream source (LLM, external API, etc.). Instead, fix the upstream — correct the prompt, fix the API caller, or adjust the data format at the source. A `_clean_*` or `_sanitize_*` shim means you chose to treat the symptom instead of the disease.

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

- **game-master for debugging** - The `game-master` scheduler can be run
  manually with `docker compose run --rm game-master` for local debugging
  without Telegram bot.

- **Renaming files** - Always use `git mv <old> <new>` instead of `mv` +
  `git rm` to preserve file history.

- **Database schema changes** — All changes to `database.py` must be done
  through the `MIGRATIONS` list. Never add columns directly to `CREATE TABLE`
  statements — only add them via a new migration entry. This keeps existing
  databases in sync with fresh ones. See comment at the `MIGRATIONS`
  definition for the rationale.

## Useful Commands

### Apply code changes without wiping data (mutations, business logic, etc.)

Stops only the target containers (preserving DB volumes and ComfyUI outputs),
rebuilds them from the current source, and starts fresh containers.

```bash
docker compose up -d --force-recreate telegram-bot game-master game-server-api
```

### Full wipe — destroy all game data, ComfyUI outputs, and rebuild from scratch

```bash
docker compose down \
  && rm -rfv ./*/*.db \
  && rm -fv ./comfyui/output/*_.png \
  && docker compose up -d --build
```

After running this, you must generate a new turn via Telegram:
`/gm_start_game <game_id>` (first turn) then `/gm_continue_game <game_id>`
for subsequent turns, since all sessions/game state is deleted.

### Dump all service logs for analysis (e.g. attach to an LLM)

```bash
docker compose logs -t > /tmp/compose.logs
```
