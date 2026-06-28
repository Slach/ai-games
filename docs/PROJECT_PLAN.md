# AI-Generated Cooperative Game — Project Plan

## Project Overview

A cooperative game delivered through a Telegram bot, where an LLM generates a unique
story turn by turn. The system generates comics and visual content based on the story,
while players make individual choices to progress through the narrative.

### Vision & Uniqueness

- **Persistent AI-generated narrative** — the plot develops turn by turn, maintaining
  memory of past events and player decisions
- **Multi-modal content** — not only text, but also generated comics (images) via
  ComfyUI + Pixelle MCP
- **Cooperative gameplay** — each player receives a personal briefing and makes
  independent choices that affect the overall story
- **Asynchronous format** — ideal for busy people, 5–10 minutes per turn
- **Auto-action for absent players** — the system auto-selects a plausible action for
  unresponsive players so the game never stalls

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM LAYER                              │
├─────────────────────────────────────────────────────────────────┤
│  telegram-bot (aiogram)                                         │
│  - Commands: /start, /profile, /turn, /help                     │
│  - Onboarding flow with FSM                                      │
│  - Message handling (text & voice)                               │
│  - Push server (receives briefings from game-server)         │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                  GAME SERVER API (FastAPI)                       │
│                                                                  │
│  ┌──────────────────────┐  ┌───────────────────────┐           │
│  │ Game Master Agent    │  │ Image Generator       │           │
│  │ (game_server.py)     │  │ (image_generator.py)  │           │
│  │ - Story generation   │  │ - Comic strip gen     │           │
│  │ - NPC decisions      │  │ - Avatar generation   │           │
│  │ - Player briefings   │  │ - ComfyUI integration │           │
│  │ - Outcome analysis   │  │ - Pixelle MCP         │           │
│  │ - Mission generation │  │                       │           │
│  │ - Auto-action select │  │                       │           │
│  └──────────────────────┘  └───────────────────────┘           │
│                                                                  │
│  REST API Endpoints:                                             │
│  - /onboarding/*                                                 │
│  - /players/*                                                    │
│  - /game/*                                                       │
│  - /admin/*                                                      │
└──────────────────────┬──────────────────────────────────────────┘
                       │
               ┌───────┼───────┐
               ▼               ▼
        ┌─────────────┐  ┌──────────┐
        │ game-scheduler  │  │ comfyui   │
        │ (scheduler)  │  │ (GPU gen) │
        │              │  │ + Pixelle │
        └─────────────┘  └──────────┘
                               │
                               ▼
                        ┌────────────┐
                        │ Images     │
                        │ (comics,   │
                        │  avatars)  │
                        └────────────┘

Database: SQLite (game_server.db per service)
- Player profiles with species/gender/role
- Onboarding sessions with score history
- Game turns (story, circumstances, outcomes)
- Player actions (per turn, per player)
- Player briefings (personal per-turn narrative)
- NPC profiles and decisions
- Missions (active and completed)
- Messages and notifications
- Kicked/banned player tracking
```

## Current Stack

| Layer | Technology |
|-------|------------|
| Bot | Python + aiogram |
| API | Python + FastAPI |
| LLM | llama.cpp (OpenAI-compatible endpoint) |
| Content Gen | ComfyUI (Docker, GPU) + Pixelle MCP |
| Database | SQLite (per-service) |
| Scheduler | Python + asyncio (standalone service) |
| Deployment | Docker Compose, external spark-network |
| LLM Model | Qwen/Qwen3.5-35B-FP8 (configurable) |

## Turn Gameplay Loop

```
┌────────────────────────────────────────────────────────────────┐
│  Scheduler triggers next turn (configurable interval: 8h,      │
│  30m, or daily at HH:MM)                                       │
├────────────────────────────────────────────────────────────────┤
│  PREVIOUS TURN CLEANUP:                                         │
│  - Auto-select actions for players who didn't choose            │
│  - LLM chooses plausible action considering player profile,     │
│    personal briefing, and available actions                     │
├────────────────────────────────────────────────────────────────┤
│  NEXT TURN GENERATION (via /admin/continue-game):               │
│  1. Analyze previous turn outcomes (all decisions now in)       │
│  2. Generate global circumstances (setting, conflict, narrative)│
│  3. Generate per-player personal briefings                      │
│  4. Generate NPC decisions and dialogues                        │
│  5. Generate mission seeds for the new turn                     │
│  6. Push briefings to players via Telegram bot push server      │
├────────────────────────────────────────────────────────────────┤
│  DURING THE TURN:                                               │
│  - Players view /turn to see current story and circumstances   │
│  - Each player gets a personal briefing with action choices     │
│  - Players select an action (or the system auto-selects later)  │
│  - Players can send text/voice messages to Game Master          │
│  - Check /profile to view character                             │
├────────────────────────────────────────────────────────────────┤
│  TURN TRANSITION:                                               │
│  - Scheduler waits for next interval                            │
│  - Any player who hasn't chosen gets auto-action                │
│  - All actions feed into next turn's outcome analysis           │
│  - Story branches based on cumulative player choices            │
└────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Game Master Agent (LLM) — `game-server/game_server.py`

- Turn story generation using LLM (llama.cpp via OpenAI-compatible API)
- NPC decision generation with personalities
- Per-player personal briefing generation
- Outcome analysis (consequences of player actions)
- Mission generation with seeds
- Auto-action selection for unresponsive players
- Game-over detection and ending generation
- Content prompt generation for visual assets
- Language support (English/Russian)

### 2. Game Server API — `game-server/main.py`

- FastAPI service for game orchestration
- Onboarding endpoints (questions, profile creation)
- Player management (join, leave, kick, ban)
- Game lifecycle (start, continue, end, reset)
- Turn management (create turn, get story, briefings, actions)
- Admin endpoints (continue-game, generate-comic, notify-player)
- Push client for Telegram bot notifications
- Mission system (create, complete, track)

### 3. Telegram Bot — `telegram-bot/bot.py`

- aiogram-based bot with FSM onboarding
- Commands: /start, /profile, /turn, /help
- Admin commands: /start_game, /continue_game, /kick
- Inline keyboards for action selection and onboarding
- Text and voice message handling
- Push server (`push_server.py`) for receiving briefings from API
- Language support (per-player preference)

### 4. Game Master Scheduler — `game-scheduler/game_server.py`

- Standalone async service that calls game-server
- Configurable schedule: interval (8h, 30m, 30s) or daily at HH:MM
- Modes: `scheduled` (loop) or `single` (one-shot for testing)
- Turn lifecycle orchestration:
  1. Auto-select actions for unresponsive players (previous turn)
  2. Trigger next turn via `/admin/continue-game`
- Runtime: `docker compose run --rm game-scheduler` for debugging

### 5. Image Generator — `game-server/image_generator.py`

- Comic strip generation for players via ComfyUI
- Avatar generation for player profiles
- Pixelle MCP integration for image generation
- Image storage and retrieval

### 6. Database — `game-server/database.py`

- SQLite database (`game_server.db`)
- Player profiles (species, gender, role, traits, name)
- Onboarding sessions with score history
- Game turns (global circumstances, story, outcomes)
- Player actions per turn
- Player briefings (personal narrative, available actions)
- NPC profiles and decisions
- Missions (active and completed)
- Game messages and notifications
- Schema migrations via `MIGRATIONS` list

### 7. Game Rules — `game-server/game_rules.py`

- Ship role definitions and constraints
- Mission normalization and seed selection
- Species and gender definitions for onboarding

### 8. Prompts — `game-server/prompts.py`

- All LLM prompts: onboarding, story, briefings, NPC decisions,
  outcomes, missions, game-over, auto-action, content generation
- JSON schema definitions for structured LLM output

## Deployment

### Services (docker-compose.yaml)

| Service | Description | GPU |
|---------|-------------|-----|
| `comfyui` | Image generation backend (ComfyUI) | Yes |
| `game-server` | FastAPI game orchestration | No |
| `telegram-bot` | aiogram bot + push server | No |
| `game-scheduler` | Turn scheduler | No |

All services run on the external `spark-network`. llama.cpp is also an external
service on that network.

### Commands

**Apply code changes without wiping data:**

```bash
docker compose --progress=plain stop telegram-bot game-scheduler game-server --timeout=1 \
  && docker compose --progress=plain up -d --force-recreate telegram-bot game-scheduler game-server
```

**Full wipe and rebuild:**

```bash
docker compose down \
  && rm -rfv ./*/*.db \
  && rm -fv ./comfyui/output/*_.png \
  && docker compose up -d --build
```

**Manual turn trigger (for debugging):**

```bash
docker compose run --rm game-scheduler
```

**Run tests:**

```bash
cd game-server && ../.venv/bin/python -m unittest discover -s tests
```

## Implementation Status

### Completed ✅

- [x] Game Master LLM agent (story, briefings, NPCs, outcomes, missions)
- [x] FastAPI game server with full REST API
- [x] Telegram bot with FSM onboarding and action selection
- [x] Turn scheduler with configurable interval
- [x] Player profiles (species, gender, role, traits)
- [x] Onboarding flow with dynamic questions
- [x] Player action system with auto-selection
- [x] NPC system (decisions, dialogues, role-filling)
- [x] Mission system
- [x] Push server for Telegram notifications
- [x] Language support (English/Russian)
- [x] Game-over detection and ending generation
- [x] Comic generation via ComfyUI + Pixelle MCP
- [x] Avatar generation
- [x] SQLite persistence with migration system
- [x] Docker Compose deployment
- [x] Admin commands (/start_game, /continue_game, /kick)

### Planned ⏳

- [ ] Video generation (ComfyUI Lightx2v workflow)
- [ ] 3D scene generation (ComfyUI TRELLIS2 workflow)
- [ ] Voice generation for NPCs (ComfyUI ChatterBox)
- [ ] Multiple parallel game instances
- [ ] Analytics and player engagement tracking
- [ ] Performance optimization for content generation
- [ ] Telegram Mini App (TypeScript + React) — not yet implemented, bot-only for now

## Risks & Mitigations

| Risk | Probability | Mitigation |
|------|-------------|------------|
| GPU expensive | High | Model optimization, batching |
| Generation latency | Medium | Async pipeline, concurrent LLM calls |
| Plot consistency | High | Memory system, world state per turn |
| Telegram limitations | Medium | Push server, retry logic |
| GPU resource management | Medium | Docker Compose resource reservations |
| Content moderation | Low | Automated filters + human review |

## Conclusion

This project leverages generative AI (llama.cpp + ComfyUI) to create unique,
persistent cooperative game experiences delivered through Telegram. The
turn-based architecture with auto-action for absent players ensures the game
never stalls, while the modular Docker Compose setup allows easy local
development and deployment.
