# AI-Generated Cooperative Game Project Plan
## Improved Version (Combined from AI-Games + Obsidian AI-MMO)

## Project Overview

A cooperative game delivered through a Telegram bot and Telegram Mini App, where an LLM generates a unique story once per day. The system generates comics, videos, 3D scenes, and other content based on the story, while players make choices to progress through the narrative.

### Vision & Uniqueness

**Persistent AI-generated narrative** — the plot develops every day, maintaining memory of past events
**Multi-modal content** — not only text, but also generated pictures, videos, 3D scenes, character voices
**Collaborative gameplay** — decisions are made by a group of players, affecting the overall story
**Asynchronous format** — ideal for busy people, 5-10 minutes per day

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        TELEGRAM LAYER                           │
├─────────────────────────────────────────────────────────────────┤
│  Telegram Bot (aiogram)                                         │
│  - Commands: /start, /profile, /today, /help                    │
│  - Onboarding flow with FSM                                     │
│  - Message handling (text & voice)                              │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    GAME MASTER API                              │
│              (FastAPI + STRANDS Agent)                          │
│                                                                 │
│  ┌─────────────────────┐  ┌──────────────────┐                 │
│  │ Game Master Agent   │  │ Comic Generator  │                 │
│  │ (game_master.py)    │  │ (comic_generator.py)│               │
│  └─────────────────────┘  └──────────────────┘                 │
│                                                                 │
│  REST API Endpoints:                                            │
│  - /onboarding/*                                                │
│  - /players/*                                                   │
│  - /game/*                                                      │
│  - /admin/*                                                     │
└──────────────────────┬──────────────────────────────────────────┘
                       │
               ┌───────┼───────┐
               ▼       ▼
        ┌─────────┐ ┌──────────┐
        │game-master│ │comfyui   │
        │scheduler│ │          │
        │         │ │(GPU gen) │
        └─────────┘ └──────────┘
                       │
               ┌───────┴───────┐
               ▼               ▼
        ┌────────────┐  ┌────────────┐
        │ Images     │  │ Video/3D   │
        │ (nunchaku) │  │ generation │
        └────────────┘  └────────────┘

Database: SQLite (game_master.db)
- Player profiles
- Onboarding sessions
- Game days
- Player actions
- Messages

## Core Components (Current Implementation)

### 1. Game Master Agent (strands-agents) - ✅ IMPLEMENTED
- Generation of daily plot using LLM
- NPC dialogue generation with personality templates
- Content prompt generation for visual assets
- Located in `game-server-api/game_master.py`

### 2. Onboarding System - ✅ IMPLEMENTED
- Multi-question onboarding flow via Telegram bot
- Player profile creation with role and traits
- FSM state management in `telegram-bot/bot.py`

### 3. Story Generation - ✅ IMPLEMENTED
- Daily episode generation at scheduled time
- JSON-formatted story output (setting, conflict, narrative)
- Decision points with action choices
- Language support (English/Russian)

### 4. Player Action System - ✅ IMPLEMENTED
- Action selection via inline keyboard
- Action recording in database
- Default action selection if player doesn't choose
- Consequence tracking

### 5. Message System - ✅ IMPLEMENTED
- Text message handling with Game Master response
- Voice message support (stored, no transcription yet)
- Message history per player
- Located in `telegram-bot/bot.py`

### 6. Content Generation (ComfyUI) - ⏳ PLANNED
- **Pictures:** scenes, characters, locations (nunchaku workflow)
- **Video:** key moments of the story (Lightx2v workflow)
- **3D:** ship, stations, planets (TRELLIS2 workflow)
- **Voices:** character voiceovers (ChatterBox workflow)
- Comic strip generation with multiple panels

### 7. Database - ✅ IMPLEMENTED
- SQLite database (`game_master.db`)
- Player profiles and onboarding sessions
- Game days and player actions
- Message history

### 8. Telegram Interface - ✅ IMPLEMENTED
- Bot commands: /start, /profile, /today, /help
- Onboarding flow with inline keyboards
- Action selection via callback queries
- Language support (English/Russian)

### 9. Scheduler - ✅ IMPLEMENTED
- Daily generation at configured time (default 08:00)
- Single-run mode for testing
- Located in `game-master/game_master.py`

## Tech Stack

| Layer | Technology |
|-------|------------|
| Bot | Python + aiogram |
| Mini App | TypeScript + React + Three.js |
| Game Engine | strands-agents/sdk-python |
| Content Gen | ComfyUI (Docker) |
| Database | PostgreSQL + pgvector |
| Queue | Redis |
| Hosting | VPS with GPU / Cloud GPU |

## Daily Gameplay Loop (Current Flow)

```
┌─────────────────────────────────────────────────────────────┐
│  08:00  │  Game Master Scheduler triggers daily episode    │
├─────────┼───────────────────────────────────────────────────┤
│  08:00+ │  Episode generated via game-server-api           │
│         │  - Story with setting, conflict, narrative       │
│         │  - NPC dialogues with personalities              │
│         │  - Decision points for player actions            │
├─────────┼───────────────────────────────────────────────────┤
│  Anytime│  Players can:                                      │
│         │  - View current day via /today                   │
│         │  - Select action from decision points            │
│         │  - Send text/voice messages to Game Master       │
│         │  - Check profile with /profile                   │
├─────────┼───────────────────────────────────────────────────┤
│  End of │  If no action selected:                          │
│  Day    │  - AI selects default action based on traits     │
│         │  - Action recorded and consequences applied      │
├─────────┼───────────────────────────────────────────────────┤
│  Next   │  New episode generated based on previous actions │
│  Day    │  - Story continues from previous day             │
│         │  - Player choices influence narrative            │
└─────────┴───────────────────────────────────────────────────┘
```

## Implementation Phases

### Phase 1: Foundation (Months 1-2) - MOSTLY COMPLETE ✅

**Implemented:**
- [x] Docker Compose setup with all services
- [x] Game Master Agent with STRANDS SDK integration
- [x] Basic Telegram bot with aiogram
- [x] Story generation system (text only)
- [x] Onboarding flow with player profiles
- [x] Player action selection system
- [x] Message handling (text and voice)
- [x] SQLite database for persistence
- [x] Daily scheduler for episode generation
- [x] Language support (English/Russian)

**Remaining TODOs:**
- [ ] ComfyUI Docker setup with HuggingFace cache mounting
- [ ] ComfyUI integration for content generation
- [ ] Comic strip generation workflow
- [ ] NPC personality system refinement
- [ ] Default action selection logic improvement
- [ ] Error handling and logging improvements

**Status:** Core gameplay loop is functional. Content generation pipeline needs implementation.

### Phase 2: Content Generation (Months 3-4) - PLANNED ⏳

**Planned Features:**
- [ ] ComfyUI integration for content generation
- [ ] Scene picture generation (nunchaku workflow)
- [ ] Character portrait generation
- [ ] Comic strip generation with multiple panels
- [ ] Video generation for key story moments (Lightx2v)
- [ ] 3D scene generation (TRELLIS2)
- [ ] Automated content creation pipeline
- [ ] Content caching and delivery system

**Dependencies:** GPU resources, HuggingFace models, ComfyUI workflows

### Phase 3: Character AI & Advanced Features (Months 5-6) - PLANNED ⏳

**Planned Features:**
- [ ] Telegram Mini App with rich UI
- [ ] Character relationship mechanics
- [ ] Enhanced dialogue generation systems
- [ ] Voice generation for NPCs (ChatterBox)
- [ ] Performance optimization for content generation
- [ ] Multiplayer voting system

**Dependencies:** Telegram Mini App framework

### Phase 4: Rich Experience (Months 7+) - FUTURE ⏳

**Planned Features:**
- [ ] Full video generation pipeline
- [ ] Advanced 3D scene rendering
- [ ] Character voiceovers with emotional range
- [ ] Multiple ships/groups for parallel stories
- [ ] Cross-group events and interactions
- [ ] Monetization options (premium content, subscriptions)
- [ ] Analytics and player engagement tracking

**Dependencies:** Additional GPU resources, CDN infrastructure

## Deployment Strategy

### Local Development
- Docker Compose for local ComfyUI instance
- Local HuggingFace cache mounting
- Development Telegram bot for testing

### Production
- Containerized deployment with GPU acceleration
- CDN for serving generated content
- Scalable backend for handling multiple game sessions
- Monitoring and analytics for player engagement

## Required Components

### ComfyUI Plugins
- **ComfyUI-TRELLIS2** — 3D generation from single images
- **comfy-cli** — Workflow management
- **ComfyUI-nunchaku** — Image and video generation
- **ComfyUI-Lightx2vWrapper** — Fast video generation
- **ComfyUI_Fill-ChatterBox** — Voice generation

### External Services
- **HuggingFace** — Model hosting and caching
- **Telegram Bot API** — Communication platform
- **Cloud Storage** — Content hosting for generated media

## Success Metrics

### Engagement
- Daily active users
- Story completion rates
- Player retention over time
- Social sharing of generated content

### Technical Performance
- Content generation speed
- System uptime
- API response times
- Error rates
- GPU resource management
- Data consistency across game sessions

## Risks & Mitigations

| Risk | Probability | Mitigation |
|------|-------------|------------|
| GPU expensive | High | Model optimization, batching |
| Generation latency | Medium | Async pipeline, pre-generation |
| Plot consistency | High | Memory system, world state |
| Telegram limitations | Medium | Fallback to Mini App |
| GPU resource management | Medium | Docker Compose optimization |
| Content moderation | Low | Automated filters + human review |
| Scalability | Medium | VPS with GPU scaling |
| Platform dependency | Low | Multi-platform strategy |

## Timeline & Budget

### Timeline
- Months 1-2: Foundation (infrastructure + basic story)
- Months 3-4: Content Generation (ComfyUI integration)
- Months 5-6: Character AI & Advanced Features

### Budget Considerations
- GPU resources for content generation
- Cloud storage for generated content
- CDN bandwidth
- Telegram API usage
- Developer time for implementation

## Conclusion

This project represents an innovative approach to gaming that leverages generative AI to create unique, daily experiences for players. The cooperative nature and daily story generation create a sense of community and anticipation that should drive engagement and retention. The modular architecture allows for future expansion to different settings and gameplay mechanics.

**Target:** Q1-Q2 2026
**Time Budget:** Parallel with Flight Reminder Bot, 1-2 hours/week
**Repo:** https://github.com/Slach/ai-games
**Status:** Idea / Early research
