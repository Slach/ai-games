# AI-Generated Cooperative Game Project Plan
## Improved Version (Combined from AI-Games + Obsidian AI-MMO)

## Project Overview

A cooperative game delivered through a Telegram bot and Telegram Mini App, where an LLM generates a unique story once per day. The system generates comics, videos, 3D scenes, and other content based on the story, while players make choices to progress through the narrative.

### Vision & Uniqueness

**Persistent AI-generated narrative** — сюжет развивается каждый день, сохраняя память о прошлых событиях
**Multi-modal content** — не только текст, но и генерируемые картинки, видео, 3D сцены, голоса персонажей
**Collaborative gameplay** — решения принимаются группой игроков, влияя на общую историю
**Asynchronous format** — идеально для занятых людей, 5-10 минут в день

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
- Генерация ежедневного сюжета
- Реакция на решения игроков
- Поддержание консистентности мира
- Оркестрация NPC и событий

### 2. NPC System (npcpy)
- Уникальные личности для членов экипажа
- Автономное поведение NPC
- Диалоги и взаимодействия

### 3. Content Generation (ComfyUI)
- **Картинки:** сцены, персонажи, локации (nunchaku)
- **Видео:** ключевые моменты истории (Lightx2v)
- **3D:** корабль, станции, планеты (TRELLIS2)
- **Голоса:** озвучка персонажей (ChatterBox)

### 4. World State
- Состояние корабля и экипажа
- История событий
- Отношения между персонажами
- Ресурсы и инвентарь

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
| Hosting | VPS с GPU / Cloud GPU |

## Daily Gameplay Loop

```
┌─────────────────────────────────────────────────────────────┐
│  08:00  │  Game Master генерирует дневной эпизод           │
├─────────┼───────────────────────────────────────────────────┤
│  08:30  │  Игроки получают уведомление с завязкой          │
├─────────┼───────────────────────────────────────────────────┤
│  08:00- │  Игроки обсуждают и голосуют за действия         │
│  20:00  │  NPC реагируют на промежуточные решения          │
├─────────┼───────────────────────────────────────────────────┤
│  20:00  │  Подсчёт голосов, определение исхода             │
├─────────┼───────────────────────────────────────────────────┤
│  20:30  │  Генерация контента (картинки, видео)            │
├─────────┼───────────────────────────────────────────────────┤
│  21:00  │  Публикация результата дня, тизер завтра         │
└─────────┴───────────────────────────────────────────────────┘
```

## Implementation Phases

### Phase 1: Foundation (Months 1-2)
- ComfyUI Docker setup с HuggingFace cache mounting
- Исследование strands-agents и npcpy
- Простой Game Master (text only)
- Базовый Telegram бот
- Basic story generation and choice system
- Simple Telegram bot integration

### Phase 2: Content Generation (Months 3-4)
- ComfyUI интеграция через MCP
- Full ComfyUI integration
- Генерация картинок для сцен
- NPC с уникальными личностями
- Система голосования
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
- Видео генерация
- 3D сцены
- Голоса персонажей
- Множество кораблей/групп
- Кросс-групповые события
- Монетизация

## Deployment Strategy

### Local Development
- Docker Compose для локального ComfyUI instance
- Local HuggingFace cache mounting
- Development Telegram bot для testing

### Production
- Containerized deployment с GPU acceleration
- CDN для serving generated content
- Scalable backend для handling multiple game sessions
- Monitoring and analytics для player engagement

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

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| GPU дорого | High | Оптимизация моделей, batching |
| Latency генерации | Medium | Async pipeline, pre-generation |
| Консистентность сюжета | High | Memory система, world state |
| Telegram ограничения | Medium | Fallback на Mini App |
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
**Бюджет времени:** Параллельно с Flight Reminder Bot, 1-2 часа/неделю
**Репо:** https://github.com/Slach/AI-MMO (TBD)
**Status:** Идея / Раннее исследование
