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
docker-compose logs -f game-master-api
docker-compose logs -f telegram-bot
```

## 5. Service Modes

### Game Master API (game-master-api)

Runs as a REST API server:
```bash
# In Docker
docker-compose logs game-master-api

# Locally for testing
cd game-master-api
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

### Game Master Scheduler (game-master)

This service triggers generation daily at 08:00 (container time).

**Modes:**
- `scheduled` (default) - daily generation on schedule
- `single` - single generation for testing

```bash
# Test single generation
docker compose run --rm game-master GAME_MASTER_MODE=single python game_master.py
```

## 6. API Testing

### Health Check
```bash
curl http://localhost:8000/health
```

### Start Onboarding
```bash
curl -X POST "http://localhost:8000/onboarding/start?player_id=123"
```

### Generate Daily Episode
```bash
curl -X POST "http://localhost:8000/admin/generate-day"
```

### Generate Comic for Player
```bash
curl -X POST "http://localhost:8000/admin/generate-comic/123"
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

### Pixelle-MCP Not Connecting
```bash
docker-compose logs pixelle-mcp
docker-compose logs comfyui
```

### Telegram Bot Not Responding
```bash
# Check token
docker-compose exec telegram-bot env | grep TELEGRAM

# Restart bot
docker-compose restart telegram-bot
```

## 9. Service Architecture

```
┌─────────────────┐
│  telegram-bot   │  ← Player interface
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ game-master-api │  ← REST API, AI generation
└────────┬────────┘
         ▲
    ┌────┴────┐
    ▼         ▼
┌─────────┐ ┌──────────┐
│pixelle- │ │game-master│
│ mcp     │ │ scheduler │
└────┬────┘ └──────────┘
     ▼
┌─────────┐
│ comfyui │  ← GPU generation
└─────────┘
```

## 9. Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot authentication token | Required |
| `LLM_URL` | LLM provider endpoint | `http://llama.cpp:8090/v1` |
| `PIXELLE_MCP_URL` | Pixelle-MCP server endpoint | `http://pixelle-mcp:9004/pixelle/mcp` |
| `GAME_MASTER_API_URL` | Game Master API endpoint | `http://game-master-api:8000` |
| `GAME_SCHEDULE_TIME` | Daily generation time (24h format) | `08:00` |
| `GAME_MASTER_MODE` | Scheduler mode: `scheduled` or `single` | `scheduled` |