"""
Game Master Agent for AI Game using Pixelle-MCP as MCP Server

This agent integrates with Pixelle-MCP to generate graphics, video, and audio content
for the AI-generated cooperative game.
"""

from strands import Agent
from strands.tools.mcp import MCPClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import asyncio
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GameMasterAgent:
    """
    A game master agent that uses Pixelle-MCP as an MCP server for generating multimedia content for the AI game.
    """

    def __init__(self, mcp_server_url: str = None):
        self.mcp_server_url = mcp_server_url or os.getenv('PIXELLE_MCP_URL', 'http://pixelle-mcp:9004/pixelle/mcp')
        self.agent = None
        
    async def initialize(self):
        """Initialize the agent with MCP tools."""
        logger.info(f"Connecting to MCP server at {self.mcp_server_url}")

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

        logger.info("Agent initialized successfully with MCP tools")

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
    Example usage of the GameMasterAgent.
    Demonstrates how to connect to Pixelle-MCP and use it for content generation.
    """
    # Create agent connected to Pixelle-MCP server
    agent = await create_game_master_agent()

    # Example tasks that would use Pixelle-MCP for content generation
    tasks = [
        "Generate an image of a futuristic spaceship interior",
        "Create a short video clip showing a space battle scene",
        "Generate audio for a character speaking in a mysterious tone"
    ]

    for task in tasks:
        try:
            response = agent.run_task(task)
            print(f"Task: {task}")
            print(f"Response: {response}")
            print("-" * 50)
        except Exception as e:
            logger.error(f"Error running task '{task}': {e}")


if __name__ == "__main__":
    asyncio.run(main())
