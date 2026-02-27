# AI Game Master

AI-powered cooperative game delivered through Telegram bot. Each day, an AI generates a unique story, personalized comics, and NPC interactions based on player choices.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Compose Services                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ Telegram     │───▶│ Game Master  │───▶│ Pixelle-MCP  │  │
│  │ Bot          │    │ API          │    │              │  │
│  │ (aiogram)    │    │ (FastAPI)    │    │              │  │
│  └──────────────┘    └──────────────┘    └──────┬───────┘  │
│                                                 │          │
│                                    ┌────────────┴────────┐ │
│                                    │   ComfyUI          │ │
│                                    │   (GPU required)   │ │
│                                    └────────────────────┘ │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Services

### 1. Game Master API (`game-master-api/`)
FastAPI service that orchestrates the game:
- Player onboarding with behavioral testing
- Daily story generation using STRANDS Agents SDK
- NPC dialogue generation
- Personalized comic generation via Pixelle-MCP
- Player action processing

**Ports:** 8000

**Key Endpoints:**
- `POST /onboarding/start` - Start onboarding for a player
- `POST /onboarding/{session_id}/answer` - Submit onboarding answer
- `GET /players/{player_id}/profile` - Get player profile
- `GET /game/current-day` - Get current day's episode
- `POST /game/actions` - Submit player action
- `POST /admin/generate-day` - Generate new daily episode
- `POST /admin/generate-comic/{player_id}` - Generate personalized comic

### 2. Telegram Bot (`telegram-bot/`)
Player interface via Telegram:
- `/start` - Begin onboarding or return to game
- `/profile` - Show player role and traits
- `/today` - View current day episode
- Interactive keyboards for action selection
- Voice message support
- Text chat with Game Master

**Ports:** None (outbound Telegram API only)

### 3. Pixelle-MCP (`pixelle-mcp/`)
Content generation orchestration via MCP protocol.

**Ports:** 9004

### 4. ComfyUI (`comfyui/`)
Image/video/3D generation backend. Requires GPU.

**Ports:** 8188

## Setup

### Prerequisites
- Docker and Docker Compose
- NVIDIA GPU (for ComfyUI)
- NVIDIA Container Toolkit
- Telegram Bot Token

### Configuration

1. Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

2. Edit `.env` and set:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
LLAMA_CPP_URL=http://llama.cpp:8090/v1
PIXELLE_MCP_URL=http://pixelle-mcp:9004/pixelle/mcp
```

### Running the Services

1. Create the Docker network:
```bash
docker network create spark-network
```

2. Build and start services:
```bash
docker-compose up -d
```

3. Check logs:
```bash
docker-compose logs -f game-master-api
docker-compose logs -f telegram-bot
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
```

## API Documentation

Visit `http://localhost:8000/docs` for Swagger UI.

## Troubleshooting

### GPU Not Available
```bash
docker-compose logs comfyui
# Check if NVIDIA runtime is configured
nvidia-smi
```

### Pixelle-MCP Connection Issues
```bash
docker-compose logs pixelle-mcp
# Verify ComfyUI is running first
docker-compose ps
```

### Telegram Bot Not Responding
```bash
# Check if bot token is set
docker-compose exec telegram-bot env | grep TELEGRAM
# Verify API connectivity
docker-compose exec telegram-bot ping game-master-api
```

## Future Enhancements

- [ ] PostgreSQL persistence
- [ ] Redis for task queue
- [ ] Voice message transcription
- [ ] 3D scene generation
- [ ] Video clips for key moments
- [ ] Multi-player cooperation
- [ ] Cross-group events
- [ ] Telegram Mini App integration