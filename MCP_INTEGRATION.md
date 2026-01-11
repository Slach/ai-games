# Using MCP Libraries for AI Game Development

## Overview

This document explains how to use the MCP (Model Configuration Protocol) libraries to connect your AI game agents to content generation services like Pixelle-MCP and ComfyUI.

## Required Libraries

- `strands-agents` - Main agent framework
- `mcp` - MCP protocol implementation
- `asyncio` - Asynchronous programming support

## Corrected Example Code

Here's the corrected version of the example code you provided:

```python
from strands import Agent
from strands.tools.mcp import MCPClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import asyncio

async def create_agent_with_mcp_tools(mcp_server_url: str) -> Agent:
    """
    Creates an agent connected to an MCP server with available tools.
    
    Args:
        mcp_server_url: URL of the MCP server (e.g., "http://localhost:9000/mcp")
        
    Returns:
        Agent instance with MCP tools integrated
    """
    # Create client for connecting to Streamable HTTP MCP server
    async with streamable_http_client(mcp_server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # List available tools from the MCP server
            tools = await session.list_tools()
            
            # Print available tools for debugging
            print(f"Available tools: {[tool.name for tool in tools]}")
            
            # Create MCP client to wrap the session
            mcp_client = MCPClient(lambda: session)
            
            # Extract tools as callable functions compatible with Strands
            strand_tools = mcp_client.list_tools_sync()
    
    return Agent(tools=strand_tools)

# Example usage
async def main():
    # Connect to Pixelle-MCP server
    agent = await create_agent_with_mcp_tools("http://localhost:9004/mcp")
    
    # Use the agent to generate content
    response = agent("Generate an image of a futuristic spaceship")
    print(response)

if __name__ == "__main__":
    asyncio.run(main())
```

## Key Points About the Correction

1. **Session Management**: The ClientSession is properly managed within the async context manager
2. **Tool Discovery**: The tools are listed and made available to the agent
3. **MCP Client**: The MCPClient correctly wraps the session to provide tools in Strands format
4. **URL**: Updated to use the correct Pixelle-MCP port (9004) instead of generic 8000


## Usage Tips

1. **Server Availability**: Make sure the Pixelle-MCP server is running before connecting
2. **Port Configuration**: Use port 9004 for Pixelle-MCP as defined in the docker-compose
3. **Error Handling**: Always wrap MCP connections in try-catch blocks
4. **Async Operations**: Remember to use asyncio.run() for the main function
5. **Logging**: Enable logging to debug connection issues

## Troubleshooting

- If you get connection errors, verify that the Pixelle-MCP service is running
- Check the docker-compose logs: `docker-compose logs pixelle-mcp`
- Ensure the URL matches the service configuration in docker-compose
- Verify that the network configuration allows communication between services