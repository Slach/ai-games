# AI Game Agents Architecture

## Technology Stack

### Backend Development

- **Python** - Primary backend language for game logic and AI integration
- **TypeScript** - Frontend and client-side development for Telegram Mini App
  (planned)

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

### Character AI Systems

- NPCs use static templates in `game_master.py` for dialogue and behavior.

### Content Generation

- **[ComfyUI](https://github.com/comfyanonymous/ComfyUI)** - GPU-accelerated
  content generation backend for images, videos, and comics. Called directly
  via HTTP API. Comic generation partially implemented in
  `comic_generator.py` with fallback placeholders.

## Architecture Overview

The game will feature a cooperative experience delivered through a Telegram
bot (Telegram Mini App planned). The core gameplay loop involves:

1. **Daily Story Generation** - LLM generates a unique story once per day
   via OpenAI API
2. **Content Generation** - ComfyUI creates comics, images, and other visual
   content
3. **Player Interaction** - Players make choices that advance the narrative
4. **NPC Responses** - NPCs respond based on static templates
5. **Game State Management** - Custom logic manages game state and narrative
   flow

**Briefings are pushed from game-server-api → telegram-bot via HTTP with
exponential retry. No polling loop needed.**

### Current Implementation Status

| System | Status | Notes |
| :--- | :--- | :--- |
| AI Systems (OpenAI) | ✅ Implemented | Game Master agent for story, NPCs, and content prompt generation |
| ComfyUI | ⚠️ Configured | GPU service running, image gen available but not integrated into game flow |
| Telegram Mini App | 📋 Planned | TypeScript/React frontend not started yet |

## Setting

The base setting is a starship crew in a Star Trek universe, but the system
is designed to support any setting. The generative nature of the content
allows for endless story possibilities within the chosen setting.

## Deployment

- Always use PYTHONDONTWRITEBYTECODE=1 for running python code
- The system will be deployed using Docker containers, every service shall
  be run as separate service in docker-compose
- ComfyUI running as a service that can be called by the Python code to
  generate content on demand.

## Important Rules

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

## Current Working Features

✅ **Fully Functional:**

- Daily story generation via OpenAI API
- Player onboarding and profile creation
- Player action selection and recording
- Message handling (text and voice)
- SQLite database persistence
- Daily scheduler for episode generation
- Language support (English/Russian)

⚠️ **Partially Implemented:**

- Comic generation (uses fallback placeholders when generation fails)
- Character/scene image generation (available but not integrated into game
  flow)
- NPC dialogues (static templates, no dynamic personality system)

📋 **Planned for Future:**

- Full content generation pipeline (videos, 3D scenes, voiceovers)
- Telegram Mini App with rich UI
- Multi-player voting and collaboration features
