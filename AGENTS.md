# AI Game Agents Architecture

## Technology Stack

### Backend Development
- **Python** - Primary backend language for game logic and AI integration
- **TypeScript** - Frontend and client-side development for Telegram Mini App (planned)

### AI and Game Master Systems
- **[STRANDS Agents SDK Python](https://github.com/strands-agents/sdk-python)** - For model-driven game master functionality. Currently implemented and handling game state management, narrative progression, NPC dialogue generation, and content prompt generation via LLM calls.

### Character AI Systems
- **[NPCPY](https://github.com/NPC-Worldwide/npcpy)** - Planned for generating character behaviors and responses. Not yet integrated. Current implementation uses static NPC templates in `game_master.py`.

### Content Generation
- **[Pixelle-MCP](https://github.com/AIDC-AI/Pixelle-MCP)** - MCP server for generating video, audio, and images. Currently configured in Docker but using direct HTTP API calls (not MCP protocol). Comic generation partially implemented in `comic_generator.py` with fallback placeholders.
- **[ComfyUI](https://github.com/comfyanonymous/ComfyUI)** - Configured as GPU-accelerated content generation backend. Integrated with Pixelle-MCP via HTTP API. Image generation workflows available but not fully integrated into game flow.

## Architecture Overview

The game will feature a cooperative experience delivered through a Telegram bot (Telegram Mini App planned). The core gameplay loop involves:

1. **Daily Story Generation** - LLM generates a unique story once per day via STRANDS Agent
2. **Content Generation** - Pixelle-MCP/ComfyUI creates comics, videos, 3D scenes (partially implemented)
3. **Player Interaction** - Players make choices that advance the narrative
4. **Dynamic Characters** - NPCs respond based on personalities (static templates currently, NPCPY planned)
5. **Game State Management** - STRANDS Agents SDK manages game state and narrative flow

### Current Implementation Status

| System | Status | Notes |
|--------|--------|-------|
| STRANDS Agents SDK | ✅ Implemented | Game Master agent in `game_master.py`, handles story generation, NPC dialogues, content prompts |
| Pixelle-MCP | ⚠️ Partially Integrated | HTTP API calls working, MCP protocol not used. Comic generation has fallback placeholders |
| ComfyUI | ⚠️ Configured | GPU service running, image generation available but not fully integrated into game flow |
| NPCPY | 📋 Planned | Not yet integrated. Current NPCs use static templates in `game_master.py` |
| Telegram Mini App | 📋 Planned | TypeScript/React frontend not implemented yet |


## Setting

The base setting is a starship crew in a Star Trek universe, but the system is designed to support any setting. The generative nature of the content allows for endless story possibilities within the chosen setting.

## Deployment

- Always use PYTHONDONTWRITEBYTECODE=1 for running python code
- The system will be deployed using Docker containers, every service shall be run as separate service in docker-compose
- Pixelle-MCP and ComfyUI running as services that can be called by the Python code to generate content on demand.

## Important Rules

- **llama.cpp is an external service** - Do not add llama.cpp service to docker-compose.yaml. It's already running on the spark-network.
- **spark-network is external** - The Docker network `spark-network` is created externally. Do not try to create it in docker-compose.
- **Use health checks** - Always use `condition: service_healthy` for service dependencies when possible.
- **game-master for debugging** - The `game-master` scheduler can be run manually with `docker compose run --rm game-master` for local debugging without Telegram bot.

## Current Working Features

✅ **Fully Functional:**
- Daily story generation via STRANDS Agent + LLM
- Player onboarding and profile creation
- Player action selection and recording
- Message handling (text and voice)
- SQLite database persistence
- Daily scheduler for episode generation
- Language support (English/Russian)

⚠️ **Partially Implemented:**
- Comic generation (uses fallback placeholders when generation fails)
- Character/scene image generation (available but not integrated into game flow)
- NPC dialogues (static templates, no dynamic personality system)

📋 **Planned for Future:**
- Full content generation pipeline (videos, 3D scenes, voiceovers)
- NPCPY integration for dynamic character behaviors
- Telegram Mini App with rich UI
- Multi-player voting and collaboration features
