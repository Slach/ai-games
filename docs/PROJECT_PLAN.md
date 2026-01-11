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
├─────────────────┬───────────────────────────────────────────────┤
│  Telegram Bot   │            Telegram Mini App                  │
│  (commands,     │  (rich UI, 3D viewer, voting, character       │
│   notifications)│   profiles, story timeline)                   │
└────────┬────────┴─────────────────────┬─────────────────────────┘
         │                              │
         ▼                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      GAME ORCHESTRATOR                          │
│              (strands-agents/sdk-python)                        │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Game Master  │  │ Story Engine │  │ World State  │          │
│  │   Agent      │  │              │  │   Manager    │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└────────────────────────────┬────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  NPC Agents  │    │   ComfyUI    │    │   Database   │
│  (npcpy)     │    │  (MCP Server)│    │  (PostgreSQL)│
└──────────────┘    └──────────────┘    └──────────────┘
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
  ┌────────────┐    ┌────────────┐    ┌────────────┐
  │  Images    │    │   Video    │    │    3D      │
  │ (nunchaku) │    │ (Lightx2v) │    │ (TRELLIS2) │
  └────────────┘    └────────────┘    └────────────┘
```

## Core Components

### 1. Game Master Agent (strands-agents)
- Generation of daily plot
- Reaction to player decisions
- Maintaining world consistency
- Orchestration of NPCs and events

### 2. NPC System (npcpy)
- Unique personalities for crew members
- Autonomous NPC behavior
- Dialogues and interactions

### 3. Content Generation (ComfyUI)
- **Pictures:** scenes, characters, locations (nunchaku)
- **Video:** key moments of the story (Lightx2v)
- **3D:** ship, stations, planets (TRELLIS2)
- **Voices:** character voiceovers (ChatterBox)

### 4. World State
- Ship and crew status
- Event history
- Character relationships
- Resources and inventory

### 5. Telegram Interface
- Bot: commands, notifications
- Mini App: rich UI, 3D viewer, voting, character profiles, story timeline

## Tech Stack

| Layer | Technology |
|-------|------------|
| Bot | Python + aiogram |
| Mini App | TypeScript + React + Three.js |
| Game Engine | strands-agents/sdk-python |
| NPC AI | npcpy |
| Content Gen | ComfyUI (Docker) |
| Database | PostgreSQL + pgvector |
| Queue | Redis |
| Hosting | VPS with GPU / Cloud GPU |

## Daily Gameplay Loop

```
┌─────────────────────────────────────────────────────────────┐
│  08:00  │  Game Master generates the daily episode         │
├─────────┼───────────────────────────────────────────────────┤
│  08:30  │  Players receive notification with the setup     │
├─────────┼───────────────────────────────────────────────────┤
│  08:00- │  Players discuss and vote for actions            │
│  20:00  │  NPCs react to intermediate decisions            │
├─────────┼───────────────────────────────────────────────────┤
│  20:00  │  Vote counting, outcome determination            │
├─────────┼───────────────────────────────────────────────────┤
│  20:30  │  Content generation (pictures, video)            │
├─────────┼───────────────────────────────────────────────────┤
│  21:00  │  Publication of the day's result, teaser for tomorrow │
└─────────┴───────────────────────────────────────────────────┘
```

## Implementation Phases

### Phase 1: Foundation (Months 1-2)
- ComfyUI Docker setup with HuggingFace cache mounting
- Research of strands-agents and npcpy
- Simple Game Master (text only)
- Basic Telegram bot
- Basic story generation and choice system
- Simple Telegram bot integration

### Phase 2: Content Generation (Months 3-4)
- ComfyUI integration via MCP
- Full ComfyUI integration
- Scene picture generation
- NPCs with unique personalities
- Voting system
- Automated content creation pipeline
- Basic multiplayer functionality

### Phase 3: Character AI & Advanced Features (Months 5-6)
- NPCPY integration for dynamic character behaviors
- Telegram Mini App
- Character personality systems
- Character relationship mechanics
- Dialogue generation systems
- Enhanced UI/UX for Telegram Mini App
- Performance optimization

### Phase 4: Rich Experience (Months 7+)
- Telegram Mini App
- Video generation
- 3D scenes
- Character voices
- Multiple ships/groups
- Cross-group events
- Monetization

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