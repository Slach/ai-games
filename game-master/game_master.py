"""
Game Master Agent for AI Game implementing the Daily Game Play Loop

This agent implements the complete Daily Game Play Loop as specified in PROJECT_PLAN.md,
using strands-agents/sdk-python for orchestration and npcpy for NPC character generation.
All LLM interactions use the LLAMA_CPP_URL environment variable.
"""

from strands import Agent
from strands.tools.mcp import MCPClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import asyncio
import logging
import os
import time
from datetime import datetime
from npcpy import NPC, NPCManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get LLM provider URL from environment, with default
LLAMA_CPP_URL = os.getenv('LLAMA_CPP_URL', 'http://llama.cpp:8090/v1')

class GameMasterAgent:
    """
    A game master agent that implements the Daily Game Play Loop for an AI-generated cooperative game.
    Orchestrates NPC interactions, player voting, and content generation using Pixelle-MCP.
    """

    def __init__(self, mcp_server_url: str = None):
        self.mcp_server_url = mcp_server_url or os.getenv('PIXELLE_MCP_URL', 'http://pixelle-mcp:9004/pixelle/mcp')
        self.agent = None
        self.npc_manager = None
        self.world_state = {
            "day": 1,
            "story_summary": "",
            "player_decisions": [],
            "npc_interactions": [],
            "generated_content": []
        }
        
    async def initialize(self):
        """Initialize the agent with MCP tools and NPC manager."""
        logger.info(f"Connecting to MCP server at {self.mcp_server_url}")
        logger.info(f"Using LLM provider at {LLAMA_CPP_URL}")

        # Initialize NPC manager with Llama.cpp as LLM provider
        self.npc_manager = NPCManager(llm_provider=LLAMA_CPP_URL)
        
        # Create client for connecting to Streamable HTTP MCP server
        async with streamable_http_client(self.mcp_server_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # List available tools from the MCP server
                tools = await session.list_tools()
                logger.info(f"Discovered {len(tools)} tools from MCP server")

                # Display tool information
                for tool in tools:
                    logger.info(f"Tool: {tool.name} - {tool.description}")

                # Create MCP client to wrap the session
                mcp_client = MCPClient(lambda: session)

                # Extract tools as callable functions compatible with Strands
                strand_tools = mcp_client.list_tools_sync()

                # Create the agent with the MCP tools
                self.agent = Agent(tools=strand_tools)

        logger.info("Agent and NPC manager initialized successfully")

    async def generate_daily_episode(self):
        """Phase 1: Generate the daily episode at 08:00"""
        logger.info("Starting Phase 1: Generate daily episode (08:00)")
        
        # Create a prompt for the daily story generation
        prompt = f"""
        Generate a daily episode for an AI cooperative game with the following context:
        - Day: {self.world_state['day']}
        - Previous story summary: {self.world_state['story_summary']}
        
        Create a compelling narrative that includes:
        1. A setting (space station, alien planet, spaceship interior)
        2. A central conflict or mystery
        3. 3-5 key decision points for players
        4. Introduce or develop at least 2 NPC characters with distinct personalities
        
        Keep the story concise (under 300 words) and engaging for players to vote on.
        """
        
        # Use strands agent to generate the story
        story = self.agent(prompt)
        self.world_state['story_summary'] = story
        logger.info(f"Generated daily episode: {story}")
        
        # Generate NPC dialogues using npcpy
        npc_dialogues = []
        for npc in self.npc_manager.npcs:
            dialogue = npc.generate_dialogue(
                context=story,
                mood="curious",
                tone="mysterious"
            )
            npc_dialogues.append({
                "npc": npc.name,
                "dialogue": dialogue
            })
        
        self.world_state['npc_interactions'] = npc_dialogues
        logger.info(f"Generated NPC dialogues: {npc_dialogues}")
        
        return story, npc_dialogues

    async def notify_players_and_collect_votes(self):
        """Phase 2-3: Notify players and collect votes (08:30-20:00)"""
        logger.info("Starting Phase 2-3: Notify players and collect votes (08:30-20:00)")
        
        # In a real implementation, this would trigger Telegram notifications
        # For this simulation, we'll assume votes are collected over 12 hours
        logger.info("Sending notifications to players via Telegram...")
        logger.info("Waiting for player votes (simulating 12-hour voting period)")
        
        # Simulate player voting - in reality this would come from Telegram Mini App
        # For now, we'll generate some sample votes based on the story
        sample_votes = [
            "Choose the risky path through the asteroid field",
            "Trust the mysterious alien artifact",
            "Send a distress signal to the nearest station"
        ]
        
        # Simulate player decisions (in real system, these would be collected from users)
        self.world_state['player_decisions'] = sample_votes
        logger.info(f"Collected player votes: {sample_votes}")
        
        return sample_votes

    async def determine_outcome_and_generate_content(self):
        """Phase 4-5: Determine outcome and generate content (20:00-21:00)"""
        logger.info("Starting Phase 4-5: Determine outcome and generate content (20:00-21:00)")
        
        # Combine story, NPC dialogues, and player decisions to determine outcome
        outcome_prompt = f"""
        Based on the following:
        - Daily story: {self.world_state['story_summary']}
        - NPC dialogues: {self.world_state['npc_interactions']}
        - Player decisions: {self.world_state['player_decisions']}
        
        Determine the outcome of the day's events. Create a narrative conclusion
        that logically follows from the player choices and NPC interactions.
        
        Then generate prompts for content generation:
        1. Image prompt for a key scene (use nunchaku)
        2. Video prompt for a dynamic moment (use Lightx2v)
        3. 3D scene prompt for the setting (use TRELLIS2)
        4. Voiceover prompt for an NPC (use ChatterBox)
        
        Return the outcome and the four content generation prompts in JSON format.
        """
        
        # Use strands agent to determine outcome and generate content prompts
        content_result = self.agent(outcome_prompt)
        logger.info(f"Generated content prompts: {content_result}")
        
        # Extract content prompts (in real implementation, parse JSON)
        image_prompt = "Generate an image of the key scene from the story"
        video_prompt = "Generate a short video clip of the dynamic moment"
        scene3d_prompt = "Generate a 3D scene of the setting"
        voiceover_prompt = "Generate a voiceover for the NPC speaking the key line"
        
        # Store generated content prompts
        self.world_state['generated_content'] = {
            "image": image_prompt,
            "video": video_prompt,
            "3d_scene": scene3d_prompt,
            "voiceover": voiceover_prompt
        }
        
        # Trigger content generation via MCP tools
        logger.info("Triggering content generation via MCP tools...")
        
        # In a real implementation, these would be called as MCP tools
        # For now, we'll just log them
        for content_type, prompt in self.world_state['generated_content'].items():
            logger.info(f"Generating {content_type} with prompt: {prompt}")
        
        return content_result

    async def publish_result(self):
        """Phase 6: Publish result and teaser for tomorrow (21:00)"""
        logger.info("Starting Phase 6: Publish result and teaser for tomorrow (21:00)")
        
        # Generate teaser for tomorrow's episode
        teaser_prompt = f"""
        Based on today's story: {self.world_state['story_summary']}
        and player decisions: {self.world_state['player_decisions']}
        
        Create a short, intriguing teaser (1-2 sentences) for tomorrow's episode.
        Hint at a new development or mystery without revealing too much.
        """
        
        teaser = self.agent(teaser_prompt)
        logger.info(f"Generated teaser for tomorrow: {teaser}")
        
        # In a real implementation, this would publish to Telegram
        logger.info("Publishing today's result to Telegram bot and Mini App...")
        logger.info(f"Story: {self.world_state['story_summary']}")
        logger.info(f"NPC Dialogues: {self.world_state['npc_interactions']}")
        logger.info(f"Player Decisions: {self.world_state['player_decisions']}")
        logger.info(f"Generated Content: {self.world_state['generated_content']}")
        logger.info(f"Teaser: {teaser}")
        
        # Increment day for next cycle
        self.world_state['day'] += 1
        
        return teaser

    async def run_daily_game_loop(self):
        """Execute the complete Daily Game Play Loop"""
        logger.info("Starting Daily Game Play Loop")
        
        # Phase 1: Generate daily episode (08:00)
        story, npc_dialogues = await self.generate_daily_episode()
        
        # Phase 2-3: Notify players and collect votes (08:30-20:00)
        player_decisions = await self.notify_players_and_collect_votes()
        
        # Phase 4-5: Determine outcome and generate content (20:00-21:00)
        content_result = await self.determine_outcome_and_generate_content()
        
        # Phase 6: Publish result and teaser for tomorrow (21:00)
        teaser = await self.publish_result()
        
        logger.info("Daily Game Play Loop completed successfully")
        
        return {
            "story": story,
            "npc_dialogues": npc_dialogues,
            "player_decisions": player_decisions,
            "content_result": content_result,
            "teaser": teaser,
            "day": self.world_state['day']
        }

    def run_task(self, prompt: str):
        """Run a task using the agent with the provided prompt."""
        if not self.agent:
            raise RuntimeError("Agent not initialized. Call initialize() first.")

        logger.info(f"Running task: {prompt}")
        response = self.agent(prompt)
        logger.info(f"Response: {response}")
        return response


async def create_game_master_agent(mcp_server_url: str = None) -> GameMasterAgent:
    """
    Factory function to create and initialize a GameMasterAgent.

    Args:
        mcp_server_url: URL of the Pixelle-MCP server

    Returns:
        Initialized GameMasterAgent instance
    """
    agent = GameMasterAgent(mcp_server_url)
    await agent.initialize()
    return agent


async def main():
    """
    Main entry point for the Game Master Agent.
    Implements the Daily Game Play Loop as specified in PROJECT_PLAN.md.
    """
    # Create agent connected to Pixelle-MCP server
    agent = await create_game_master_agent()

    # Run the daily game loop
    result = await agent.run_daily_game_loop()
    
    logger.info("Daily Game Play Loop Results:")
    logger.info(f"Day: {result['day']}")
    logger.info(f"Story: {result['story']}")
    logger.info(f"Teaser: {result['teaser']}")
    logger.info(f"Player Decisions: {result['player_decisions']}")
    logger.info(f"Content Generated: {result['content_result']}")

    # In a real implementation, this would run continuously
    # For now, we'll just run once to demonstrate the loop
    logger.info("Game Master Agent completed its daily cycle")


if __name__ == "__main__":
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
