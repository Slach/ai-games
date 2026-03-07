# AI Game Master

AI-powered cooperative game delivered through Telegram bot. Each day, an AI generates a unique story, personalized comics, and NPC interactions based on player choices.

## Architecture

```mermaid
graph TD
    A[Telegram API] --> B[telegram-bot]
    B --> C[game-master-api]
    D[game-master] --> C
    C --> E[pixelle-mcp]
    E --> F[comfyui]
    C --> G[llama.cpp<br/>external]

    style B fill:#E1F5FE
    style C fill:#E8F5E9
    style E fill:#FFF3E0
    style F fill:#FFF3E0
    style G fill:#E0E0E0
    style D fill:#F3E5F5
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| game-master-api | 8000 | FastAPI backend with SQLite persistence |
| telegram-bot | N/A | Telegram bot interface |
| pixelle-mcp | 9004 | Content generation orchestration |
| comfyui | 8188 | Image/Video generation backend |
| game-master | N/A | Daily generation scheduler (run manually for debugging) |

### Game Master API (`game-master-api/`)
FastAPI service that orchestrates the game:
- Player onboarding with behavioral testing
- Daily story generation using STRANDS Agents SDK
- NPC dialogue generation
- Personalized comic generation via Pixelle-MCP
- Player action processing
- Text/voice message handling
- SQLite persistence for game state

**Ports:** 8000

**Key Endpoints:**
- `POST /onboarding/start` - Start onboarding for a player
- `POST /onboarding/{session_id}/answer` - Submit onboarding answer
- `GET /players/{player_id}/profile` - Get player profile
- `GET /game/current-day` - Get current day's episode
- `POST /game/actions` - Submit player action
- `POST /game/messages` - Send message to game master
- `POST /admin/generate-day` - Generate new daily episode
- `POST /admin/generate-comic/{player_id}` - Generate personalized comic

### Telegram Bot (`telegram-bot/`)
Player interface via Telegram:
- `/start` - Begin onboarding or return to game
- `/profile` - Show player role and traits
- `/today` - View current day episode
- `/help` - Show help information
- Interactive keyboards for action selection
- Voice message support
- Text chat with Game Master

**Ports:** None (outbound Telegram API only)

### Game Master Scheduler (`game-master/`)
Scheduled task runner that triggers daily episode generation. Can be run manually for debugging.

**Usage:**
```bash
# Run single generation cycle for testing
GAME_MASTER_MODE=single docker compose run --rm game-master
```

**Ports:** None

### Pixelle-MCP (`pixelle-mcp/`)
Content generation orchestration via MCP protocol.

**Ports:** 9004

### ComfyUI (`comfyui/`)
Image/video/3D generation backend. Requires GPU.

**Ports:** 8188

## Setup

### Prerequisites
- Docker and Docker Compose
- NVIDIA GPU (for ComfyUI)
- NVIDIA Container Toolkit
- Telegram Bot Token (get from @BotFather)
- External `spark-network` Docker network
- External `llama.cpp` service running on port 8090

### Configuration

1. Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

2. Edit `.env` and set your Telegram bot token:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

### Running the Services

1. Build and start services:
```bash
docker compose up -d
```

2. Check logs:
```bash
docker compose logs -f game-master-api
docker compose logs -f telegram-bot
```

3. Run single generation cycle (for testing):
```bash
GAME_MASTER_MODE=single docker compose run --rm game-master
```

## Onboarding Flow

1. Player sends `/start` to the bot
2. Bot creates onboarding session
3. Player answers 5 behavioral questions:
   - Response to unknown signals
   - Handling risky plans
   - Moral dilemmas
   - Specialization preference
   - Conflict resolution style
4. System generates player profile:
   - Role (Chief Engineer, XO, Science Officer)
   - Personality traits
   - Avatar description

## Daily Game Loop

```
08:00  - Game Master generates daily episode
08:30  - Players receive notification with setup
08:00-20:00 - Players vote on actions
20:00  - Outcome determination
20:30  - Content generation (comics, images)
21:00  - Publish results and teaser for tomorrow
```

## NPC System

The system generates NPC teams based on player role:
- **Captain** - Always present, leads the crew
- **Pilot** - Navigation and flight operations
- **Engineer** - Technical systems maintenance
- **Communications** - External contact and diplomacy
- **Science Officer** - Research and analysis
- **Security Chief** - Safety and threat assessment

NPCs have distinct personalities and speech styles.

## Content Generation

### Personalized Comics
- Generated per player based on their role and traits
- 4-6 panel format
- Includes player character prominently
- Speech bubbles with NPC dialogue

### Image Generation
- Scene images for story settings
- Character portraits
- 3D scene descriptions

## Development

### Running Locally

```bash
# Game Master API
cd game-master-api
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Telegram Bot
cd telegram-bot
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=your_token
python bot.py

# Game Master (for debugging)
cd game-master
pip install -r requirements.txt
GAME_MASTER_MODE=single python game_master.py
```

## API Documentation

Visit `http://localhost:8000/docs` for Swagger UI.

## Troubleshooting

### API Connection Failed

Check game-master-api health:
```bash
curl http://localhost:8000/health
docker compose logs game-master-api
```

### GPU Not Available
```bash
docker compose logs comfyui
# Check if NVIDIA runtime is configured
nvidia-smi
```

### Pixelle-MCP Connection Issues
```bash
docker compose logs pixelle-mcp
# Verify ComfyUI is running first
docker compose ps
```

### Telegram Bot Not Responding
```bash
# Check if bot token is set
docker compose exec telegram-bot env | grep TELEGRAM
# Verify API connectivity
docker compose exec telegram-bot ping game-master-api
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| LLM_URL | http://llama.cpp:8090/v1 | LLM endpoint |
| LLM_API_KEY | placeholder-key-for-llama-cpp | Required by OpenAI client |
| PIXELLE_MCP_URL | http://pixelle-mcp:9004/pixelle/mcp | Content gen endpoint |
| COMFYUI_URL | http://comfyui:8188 | Image gen endpoint |
| TELEGRAM_BOT_TOKEN | (required) | Telegram bot token |
| GAME_SCHEDULE_TIME | 08:00 | Daily generation time |
| GAME_MASTER_MODE | scheduled | single/simulation/scheduled |
| GAME_LANGUAGE | ru | en or ru |

## Future Enhancements

- [ ] SQLite persistence (in progress)
- [ ] Redis for task queue
- [ ] Voice message transcription
- [ ] 3D scene generation
- [ ] Video clips for key moments
- [ ] Multi-player cooperation
- [ ] Cross-group events
- [ ] Telegram Mini App integration