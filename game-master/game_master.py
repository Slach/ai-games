"""
Game Master Agent for AI Game implementing the Daily Game Play Loop

This agent implements the complete Daily Game Play Loop as specified in PROJECT_PLAN.md,
using strands-agents/sdk-python for orchestration and npcpy for NPC character generation.
All LLM interactions use the LLAMA_CPP_URL environment variable.
"""

from strands import Agent
from strands.models.llamacpp import LlamaCppModel
from strands.tools.mcp import MCPClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import asyncio
import json
import logging
import os
from datetime import datetime
from npcpy import NPC, NPCManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get LLM provider URL from environment, with default
LLAMA_CPP_URL = os.getenv("LLAMA_CPP_URL", "http://llama.cpp:8090/v1")


class GameMasterAgent:
    """
    A game master agent that implements the Daily Game Play Loop for an AI-generated cooperative game.
    Orchestrates NPC interactions, player voting, and content generation using Pixelle-MCP.
    """

    def __init__(self, mcp_server_url: str = "http://pixelle-mcp:9004/pixelle/mcp"):
        self.mcp_server_url = mcp_server_url or os.getenv(
            "PIXELLE_MCP_URL", "http://pixelle-mcp:9004/pixelle/mcp"
        )
        self.agent = None
        self.npc_manager = None
        self.llm_model = None
        self.world_state = {
            "day": 1,
            "story_summary": "",
            "player_decisions": [],
            "npc_interactions": [],
            "generated_content": [],
        }

    async def initialize(self):
        """Initialize the agent with MCP tools and NPC manager."""
        logger.info(f"Connecting to MCP server at {self.mcp_server_url}")
        logger.info(f"Using LLM provider at {LLAMA_CPP_URL}")

        # Initialize Llama.cpp model for Strands agent
        try:
            self.llm_model = LlamaCppModel(
                base_url=LLAMA_CPP_URL,
                model_id="default",
                params={
                    "max_tokens": 2000,
                    "temperature": 0.7,
                    "repeat_penalty": 1.1,
                    "cache_prompt": True,
                },
            )
            logger.info("LlamaCppModel initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize LlamaCppModel: {e}")
            raise

        # Initialize NPC manager with custom provider configuration
        try:
            # For npcpy, we need to configure it to use the llama.cpp server
            # npcpy supports 'litellm' provider which can work with llama.cpp endpoints
            self.npc_manager = NPCManager()

            # Create some initial NPCs for the game
            captain_npc = NPC(
                name="Captain Eva Rodriguez",
                primary_directive="You are the captain of a deep space exploration vessel. You are decisive, caring, and responsible for your crew's safety.",
                model="default",
                provider="openai",  # Using openai-compatible endpoint
                api_base=LLAMA_CPP_URL,
            )

            engineer_npc = NPC(
                name="Chief Engineer Marcus Chen",
                primary_directive="You are the ship's chief engineer. You are brilliant, pragmatic, and fascinated by alien technology.",
                model="default",
                provider="openai",  # Using openai-compatible endpoint
                api_base=LLAMA_CPP_URL,
            )

            self.npc_manager.add_npc(captain_npc)
            self.npc_manager.add_npc(engineer_npc)
            logger.info("NPC manager initialized with Captain and Engineer NPCs")
        except Exception as e:
            logger.error(f"Failed to initialize NPC manager: {e}")
            # Continue without NPCs if initialization fails
            self.npc_manager = None

        # Initialize MCP connection if available
        try:
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

                    # Create the agent with the LlamaCppModel and MCP tools
                    self.agent = Agent(model=self.llm_model, tools=strand_tools)
        except Exception as e:
            logger.warning(f"Failed to connect to MCP server: {e}")
            # Create agent without MCP tools if connection fails
            self.agent = Agent(model=self.llm_model)

        logger.info("Game Master Agent initialized successfully")

    async def generate_daily_episode(self):
        """Phase 1: Generate the daily episode at 08:00"""
        logger.info("Starting Phase 1: Generate daily episode (08:00)")

        # Create a prompt for the daily story generation
        prompt = f"""
        Generate a daily episode for an AI cooperative game with the following context:
        - Day: {self.world_state["day"]}
        - Previous story summary: {self.world_state["story_summary"]}
        
        Create a compelling narrative that includes:
        1. A setting (space station, alien planet, spaceship interior)
        2. A central conflict or mystery
        3. 3-5 key decision points for players
        4. Introduce or develop at least 2 NPC characters with distinct personalities
        
        Keep the story concise (under 300 words) and engaging for players to vote on.
        """

        # Use strands agent to generate the story
        if self.agent:
            story = self.agent(prompt)
        else:
            # Fallback story generation if agent is not available
            story = f"Day {self.world_state['day']}: The crew discovers an ancient alien artifact floating in deep space. Strange energy readings emanate from it, and the ship's systems begin acting mysteriously. The crew must decide whether to investigate further or maintain a safe distance."
        self.world_state["story_summary"] = story
        logger.info(f"Generated daily episode: {story}")

        # Generate NPC dialogues using npcpy
        npc_dialogues = []
        if self.npc_manager and hasattr(self.npc_manager, "npcs"):
            for npc in self.npc_manager.npcs:
                try:
                    dialogue = npc.get_llm_response(
                        f"Context: {story}\n\nGenerate a short, {npc.name}'s reaction to this situation. Keep it brief and in character."
                    )
                    npc_dialogues.append(
                        {
                            "npc": npc.name,
                            "dialogue": dialogue.get(
                                "response", "No response generated"
                            ),
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to generate dialogue for {npc.name}: {e}")
                    npc_dialogues.append(
                        {
                            "npc": npc.name,
                            "dialogue": f"{npc.name} remains silent, contemplating the situation.",
                        }
                    )
        else:
            # Fallback dialogues if NPC manager is not available
            npc_dialogues = [
                {
                    "npc": "Captain Eva Rodriguez",
                    "dialogue": "We need to assess the situation carefully before proceeding.",
                },
                {
                    "npc": "Chief Engineer Marcus Chen",
                    "dialogue": "The ship's systems are holding steady, but I'm detecting unusual energy patterns.",
                },
            ]

        self.world_state["npc_interactions"] = npc_dialogues
        logger.info(f"Generated NPC dialogues: {npc_dialogues}")

        return story, npc_dialogues

    async def notify_players_and_collect_votes(self):
        """Phase 2-3: Notify players and collect votes (08:30-20:00)"""
        logger.info(
            "Starting Phase 2-3: Notify players and collect votes (08:30-20:00)"
        )

        # In a real implementation, this would trigger Telegram notifications
        # For this simulation, we'll assume votes are collected over 12 hours
        logger.info("Sending notifications to players via Telegram...")
        logger.info("Waiting for player votes (simulating 12-hour voting period)")

        # Simulate player voting - in reality this would come from Telegram Mini App
        # For now, we'll generate some sample votes based on the story
        sample_votes = [
            "Choose the risky path through the asteroid field",
            "Trust the mysterious alien artifact",
            "Send a distress signal to the nearest station",
        ]

        # Simulate player decisions (in real system, these would be collected from users)
        self.world_state["player_decisions"] = sample_votes
        logger.info(f"Collected player votes: {sample_votes}")

        return sample_votes

    async def determine_outcome_and_generate_content(self):
        """Phase 4-5: Determine outcome and generate content (20:00-21:00)"""
        logger.info(
            "Starting Phase 4-5: Determine outcome and generate content (20:00-21:00)"
        )

        # Combine story, NPC dialogues, and player decisions to determine outcome
        outcome_prompt = f"""
        Based on the following:
        - Daily story: {self.world_state["story_summary"]}
        - NPC dialogues: {self.world_state["npc_interactions"]}
        - Player decisions: {self.world_state["player_decisions"]}
        
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
        if self.agent:
            content_result = self.agent(outcome_prompt)
        else:
            # Fallback content generation
            content_result = json.dumps(
                {
                    "outcome": "The crew's collective decision leads to a breakthrough in understanding the alien technology.",
                    "content_prompts": {
                        "image": "A dramatic scene of the crew examining a glowing alien artifact on the bridge of their spaceship",
                        "video": "The alien artifact activates, projecting holographic star maps into the ship's bridge",
                        "3d_scene": "A detailed 3D model of the alien artifact with intricate geometric patterns",
                        "voiceover": "Captain's log: We've made first contact with an intelligence beyond our comprehension",
                    },
                }
            )
        logger.info(f"Generated content prompts: {content_result}")

        # Extract content prompts (in real implementation, parse JSON)
        image_prompt = "Generate an image of the key scene from the story"
        video_prompt = "Generate a short video clip of the dynamic moment"
        scene3d_prompt = "Generate a 3D scene of the setting"
        voiceover_prompt = "Generate a voiceover for the NPC speaking the key line"

        # Store generated content prompts
        self.world_state["generated_content"] = {
            "image": image_prompt,
            "video": video_prompt,
            "3d_scene": scene3d_prompt,
            "voiceover": voiceover_prompt,
        }

        # Trigger content generation via MCP tools
        logger.info("Triggering content generation via MCP tools...")

        # In a real implementation, these would be called as MCP tools
        # For now, we'll just log them
        for content_type, prompt in self.world_state["generated_content"].items():
            logger.info(f"Generating {content_type} with prompt: {prompt}")

        return content_result

    async def publish_result(self):
        """Phase 6: Publish result and teaser for tomorrow (21:00)"""
        logger.info("Starting Phase 6: Publish result and teaser for tomorrow (21:00)")

        # Generate teaser for tomorrow's episode
        teaser_prompt = f"""
        Based on today's story: {self.world_state["story_summary"]}
        and player decisions: {self.world_state["player_decisions"]}
        
        Create a short, intriguing teaser (1-2 sentences) for tomorrow's episode.
        Hint at a new development or mystery without revealing too much.
        """

        if self.agent:
            teaser = self.agent(teaser_prompt)
        else:
            # Fallback teaser
            teaser = f"Tomorrow, a new mystery unfolds as the alien artifact reveals its true purpose..."
        logger.info(f"Generated teaser for tomorrow: {teaser}")

        # In a real implementation, this would publish to Telegram
        logger.info("Publishing today's result to Telegram bot and Mini App...")
        logger.info(f"Story: {self.world_state['story_summary']}")
        logger.info(f"NPC Dialogues: {self.world_state['npc_interactions']}")
        logger.info(f"Player Decisions: {self.world_state['player_decisions']}")
        logger.info(f"Generated Content: {self.world_state['generated_content']}")
        logger.info(f"Teaser: {teaser}")

        # Increment day for next cycle
        self.world_state["day"] += 1

        return teaser

    async def run_daily_game_loop(self):
        """Execute the complete Daily Game Play Loop"""
        logger.info("Starting Daily Game Play Loop")
        
        try:
            # Phase 1: Generate daily episode (08:00)
            logger.info("Phase 1: Generating daily episode (08:00)")
            story, npc_dialogues = await self.generate_daily_episode()
            
            # Phase 2-3: Notify players and collect votes (08:30-20:00)
            logger.info("Phase 2-3: Notifying players and collecting votes (08:30-20:00)")
            player_decisions = await self.notify_players_and_collect_votes()
            
            # Phase 4-5: Determine outcome and generate content (20:00-21:00)
            logger.info("Phase 4-5: Determining outcome and generating content (20:00-21:00)")
            content_result = await self.determine_outcome_and_generate_content()
            
            # Phase 6: Publish result and teaser for tomorrow (21:00)
            logger.info("Phase 6: Publishing result and teaser for tomorrow (21:00)")
            teaser = await self.publish_result()
            
            logger.info("Daily Game Play Loop completed successfully")
            
            return {
                "story": story,
                "npc_dialogues": npc_dialogues,
                "player_decisions": player_decisions,
                "content_result": content_result,
                "teaser": teaser,
                "day": self.world_state['day'],
                "status": "completed",
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error in Daily Game Play Loop: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "day": self.world_state['day'],
                "timestamp": datetime.now().isoformat()
             }
    
    async def run_scheduled_daily_loop(self):
        """Run the daily game loop on a continuous schedule"""
        logger.info("Starting scheduled Daily Game Play Loop")
        
        while True:
            try:
                # Get current time
                now = datetime.now()
                
                # Calculate time until next 8:00 AM
                next_run = now.replace(hour=8, minute=0, second=0, microsecond=0)
                if next_run <= now:
                    next_run = next_run.replace(day=next_run.day + 1)
                
                wait_seconds = (next_run - now).total_seconds()
                logger.info(f"Next daily game loop scheduled for {next_run.isoformat()} (in {wait_seconds/3600:.1f} hours)")
                
                # Wait until next run time
                await asyncio.sleep(wait_seconds)
                
                # Run the daily loop
                result = await self.run_daily_game_loop()
                logger.info(f"Daily loop result: {result.get('status', 'unknown')}")
                
            except Exception as e:
                logger.error(f"Error in scheduled loop: {e}")
                # Wait 1 hour before retrying on error
                await asyncio.sleep(3600)

    async def run_timed_simulation(self):
        """Run a simulated version of the daily loop with compressed timing for testing"""
        logger.info("Starting timed simulation of Daily Game Play Loop")
        
        while True:
            try:
                # Simulate the full cycle in compressed time
                logger.info("Starting simulated daily cycle")
                
                # Phase 1: Generate daily episode (instant in simulation)
                story, npc_dialogues = await self.generate_daily_episode()
                await asyncio.sleep(1)  # Simulate processing time
                
                # Phase 2-3: Notify players and collect votes (30 seconds in simulation)
                player_decisions = await self.notify_players_and_collect_votes()
                logger.info("Waiting 30 seconds to simulate player voting period...")
                await asyncio.sleep(30)
                
                # Phase 4-5: Determine outcome and generate content (10 seconds in simulation)
                content_result = await self.determine_outcome_and_generate_content()
                logger.info("Generating content (10 seconds)...")
                await asyncio.sleep(10)
                
                # Phase 6: Publish result and teaser (instant in simulation)
                teaser = await self.publish_result()
                
                logger.info(f"Completed day {self.world_state['day'] - 1} cycle")
                logger.info(f"Waiting 60 seconds before starting next day's cycle...")
                
                # Wait 60 seconds before next cycle
                await asyncio.sleep(60)
                
            except Exception as e:
                logger.error(f"Error in timed simulation: {e}")
                await asyncio.sleep(10)

    def run_task(self, prompt: str):
        """Run a task using the agent with the provided prompt."""
        if not self.agent:
            logger.warning("Agent not initialized. Using fallback response.")
            return f"Fallback response to: {prompt}"

        logger.info(f"Running task: {prompt}")
        response = self.agent(prompt)
        logger.info(f"Response: {response}")
        return response


async def create_game_master_agent(
    mcp_server_url: str = "http://pixelle-mcp:9004/pixelle/mcp",
) -> GameMasterAgent:
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
    try:
        logger.info("Starting Game Master Agent")
        logger.info(f"LLAMA_CPP_URL: {LLAMA_CPP_URL}")
        
        # Create agent connected to Pixelle-MCP server
        agent = await create_game_master_agent()

        # Check if we should run in simulation mode or normal mode
        mode = os.getenv("GAME_MASTER_MODE", "single").lower()
        
        if mode == "simulation":
            logger.info("Running in simulation mode (timed cycles)")
            await agent.run_timed_simulation()
        elif mode == "scheduled":
            logger.info("Running in scheduled mode (daily at 8:00 AM)")
            await agent.run_scheduled_daily_loop()
        else:
            logger.info("Running in single mode (one cycle)")
            # Run the daily game loop once
            result = await agent.run_daily_game_loop()
            
            if result.get('status') == 'completed':
                logger.info("Daily Game Play Loop Results:")
                logger.info(f"Day: {result['day']}")
                logger.info(f"Story: {result['story']}")
                logger.info(f"NPC Dialogues: {result['npc_dialogues']}")
                logger.info(f"Teaser: {result['teaser']}")
                logger.info(f"Player Decisions: {result['player_decisions']}")
                logger.info(f"Content Generated: {result['content_result']}")
            else:
                logger.error(f"Daily Game Play Loop failed: {result.get('error')}")

    except Exception as e:
        logger.error(f"Fatal error in Game Master Agent: {e}")
        raise
    finally:
        logger.info("Game Master Agent shutting down")


if __name__ == "__main__":
    # Set up environment variables if not provided
    if not os.getenv("LLAMA_CPP_URL"):
        logger.warning("LLAMA_CPP_URL not set, using default: http://llama.cpp:8090/v1")
    
    if not os.getenv("GAME_MASTER_MODE"):
        logger.info("GAME_MASTER_MODE not set, using default: single")
        logger.info("Available modes:")
        logger.info("  - single: Run one daily cycle")
        logger.info("  - simulation: Run continuous cycles with timing for testing")
        logger.info("  - scheduled: Run on daily schedule at 8:00 AM")
    
    asyncio.run(main())
