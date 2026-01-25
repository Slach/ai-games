"""
Game Master Module for AI Game

This module implements the complete Daily Game Play Loop as specified in PROJECT_PLAN.md,
using strands-agents/sdk-python for orchestration and npcpy for NPC character generation.
"""

from .game_master import GameMasterAgent, create_game_master_agent

__all__ = ["GameMasterAgent", "create_game_master_agent"]
