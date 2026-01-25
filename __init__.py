"""
AI Games Package

A cooperative game delivered through a Telegram bot and Telegram Mini App, where an LLM generates a unique story once per day.
"""

__version__ = "0.1.0"

# Import game master module directly to make it accessible
from .game_master.game_master import GameMasterAgent, create_game_master_agent

__all__ = ["GameMasterAgent", "create_game_master_agent"]
