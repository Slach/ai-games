# AI Game Agents Architecture

## Technology Stack

### Backend Development
- **Python** - Primary backend language for game logic and AI integration
- **TypeScript** - Frontend and client-side development for Telegram Mini App

### AI and Game Master Systems
- **[STRANDS Agents SDK Python](https://github.com/strands-agents/sdk-python)** - For model-driven game master functionality. This tool will handle the game state management and narrative progression through MCP (Model Configuration Protocol) to call necessary services.

### Character AI Systems
- **[NPCPY](https://github.com/NPC-Worldwide/npcpy)** - For generating character behaviors and responses. This library will be used to create dynamic, believable non-player characters with their own personalities and decision-making capabilities.

### Content Generation
- **[Pixelle-MCP](https://github.com/AIDC-AI/Pixelle-MCP)** - As an MCP server for generating video, audio, and images. This will be the primary tool for creating visual content for the game including comics, videos, and 3D scenes based on the daily generated storylines.
- **[ComfyUI](https://github.com/comfyanonymous/ComfyUI)** - Integrated with Pixelle-MCP for advanced content generation workflows.

## Architecture Overview

The game will feature a cooperative experience delivered through a Telegram bot and Telegram Mini App. The core gameplay loop involves:

1. **Daily Story Generation** - An LLM generates a unique story once per day
2. **Content Generation** - Pixelle-MCP/ComfyUI creates comics, videos, 3D scenes, and other content based on the story
3. **Player Interaction** - Players make choices that advance the narrative
4. **Dynamic Characters** - NPCs respond based on their personalities generated with NPCPY
5. **Game State Management** - STRANDS Agents SDK manages the game state and narrative flow


## Setting

The base setting is a starship crew in a Star Trek universe, but the system is designed to support any setting. The generative nature of the content allows for endless story possibilities within the chosen setting.

## Deployment

- Always use PYTHONDONTWRITEBYTECODE=1 for running python code
- The system will be deployed using Docker containers, every service shall be run as separate service in docker-compose
- Pixelle-MCP and ComfyUI running as services that can be called by the paython code to generate content on demand.