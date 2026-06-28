# AI Game Master Setup

## 1. Prerequisites

- Docker and Docker Compose
- NVIDIA GPU (for ComfyUI)
- NVIDIA Container Toolkit
- Telegram Bot Token

## 2. Create Docker Network

```bash
docker network create spark-network
```

## 3. Configure Environment Variables

```bash
# Copy and edit .env file
cp .env.example .env
```

Make sure to set `TELEGRAM_BOT_TOKEN` in `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

## 4. Start Services

### Full Stack (all services)

```bash
docker-compose up -d
```

### Verify Startup

```bash
docker-compose ps
docker-compose logs -f game-server
docker-compose logs -f telegram-bot
```

## 5. Service Modes

### Game Master API (game-server)

Runs as a REST API server:

```bash
# In Docker
docker-compose logs game-server

# Locally for testing
cd game-server
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

API available at: `http://localhost:8000`
Swagger UI: `http://localhost:8000/docs`

### Telegram Bot (telegram-bot)

Starts automatically with `docker-compose up`.

For local testing:

```bash
cd telegram-bot
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=your_token
python bot.py
```

### Game Master Scheduler (game-scheduler)

This service triggers generation on schedule. It calls the game-server endpoints to generate content.

**Modes:**

- `scheduled` (default) - runs daily generation on schedule based on GAME_SCHEDULE_TIME
- `single` - single generation for testing/debugging

```bash
# Test single generation
docker compose run --rm game-scheduler GAME_SCHEDULER_MODE=single python game_server.py

```

**Admin Endpoints (called by scheduler):**

- `POST /admin/generate-turn` - Generate new turn episode
- `POST /admin/generate-comic/{player_id}` - Generate personalized comic for a player

## 6. API Testing

### Health Check

```bash
curl http://localhost:8000/health
```

### Get Game State

```bash
curl http://localhost:8000/game/state
```

### Start Onboarding

```bash
curl -X POST "http://localhost:8000/onboarding/start?player_id=123"
```

### Generate Daily Episode

```bash
# With language parameter (en or ru)
curl -X POST "http://localhost:8000/admin/generate-turn?language=en"
```

### Generate Comic for Player

```bash
# For specific turn (optional)
curl -X POST "http://localhost:8000/admin/generate-comic/123?turn=5"

# For current turn
curl -X POST "http://localhost:8000/admin/generate-comic/123"
```

### Submit Player Message

```bash
curl -X POST "http://localhost:8000/game/messages" \
  -H "Content-Type: application/json" \
  -d '{"player_id": 123, "message": "Hello Game Master", "message_type": "text"}'
```

### Get Player Messages

```bash
curl http://localhost:8000/game/messages/123?limit=10
```

## 7. Stop Services

```bash
docker-compose down
```

For complete cleanup (including volumes):

```bash
docker-compose down -v
```

## 8. Troubleshooting

### NVIDIA GPU Not Available

```bash
# Check NVIDIA runtime
docker info | grep -i nvidia

# Recreate network with proper settings
docker network rm spark-network
docker network create spark-network
```

### ComfyUI Not Running

```bash
docker-compose logs comfyui
docker-compose ps
```

### Telegram Bot Not Responding

```bash
# Check token
docker-compose exec telegram-bot env | grep TELEGRAM

# Restart bot
docker-compose restart telegram-bot
```

## 9. Service Architecture

```text
┌─────────────────┐
│  telegram-bot   │  ← Player interface (aiogram)
└────────┬────────┘
         │
         ▼
┌───────────────────────────┐
│    game-server        │  ← REST API, AI generation, database
│  (FastAPI + OpenAI Agent) │
└────────┬──────────────────┘
         ▲
    ┌────┴────┐
    ▼         ▼
┌─────────┐ ┌──────────┐
│comfyui  │ │game-scheduler│
│         │ │ scheduler │
│(GPU gen)│ │           │
└────┬────┘ └──────────┘
```

**Service Descriptions:**

- **telegram-bot**: Player interface via Telegram commands and inline keyboards
- **game-server**: FastAPI REST API with OpenAI-based Game Master agent, handles story generation, player profiles, actions, and messages
- **game-scheduler**: Scheduler service that triggers daily episode generation (runs at configured time or manually)
- **comfyui**: GPU-accelerated content generation backend with HuggingFace models

## 10. Environment Variables

| Variable | Description | Default |
| ---------- | ------------- | --------- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot authentication token | Required |
| `LLM_URL` | LLM provider endpoint (llama.cpp) | `http://llama.cpp:8090/v1` |
| `LLM_API_KEY` | API key for LLM (any value for llama.cpp) | `placeholder-key-for-llama-cpp` |
| `LLM_MODEL` | LLM model name | `unsloth/Qwen3.5-27B` |
| `COMFYUI_URL` | ComfyUI backend endpoint | `http://comfyui:8188` |
| `GAME_SERVER_URL` | Game Master API endpoint | `http://game-server:8000` |
| `GAME_SCHEDULE_TIME` | Turn generation time (24h format) | `08:00` |
| `GAME_SCHEDULER_MODE` | Scheduler mode: `scheduled` or `single` | `scheduled` |
