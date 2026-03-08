# Using MCP Libraries for AI Game Development

## Overview

This document explains how to use the MCP (Model Configuration Protocol) libraries to connect your AI game agents to content generation services like Pixelle-MCP and ComfyUI.

## Required Libraries

- `strands-agents` - Main agent framework for LLM generation
- `aiohttp` - HTTP client for Pixelle-MCP API calls
- `asyncio` - Asynchronous programming support

## Current Implementation Status

**NOTE:** MCP protocol integration is NOT currently implemented in this codebase. The system uses direct HTTP API calls to Pixelle-MCP instead. See `game-master-api/comic_generator.py` for the actual implementation pattern.

## HTTP API Integration Pattern

The current implementation uses aiohttp to call Pixelle-MCP endpoints directly:

```python
import aiohttp

async def generate_image(prompt: str) -> Optional[str]:
    """Generate an image using Pixelle-MCP HTTP API"""
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{PIXELLE_MCP_URL}/generate/image",
                json={
                    "prompt": prompt,
                    "workflow": "t2i_flux_pro.json",
                    "output_format": "webp",
                },
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("image_url", result.get("path", ""))
                else:
                    logger.error(f"Image generation failed: {resp.status}")
                    return None
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        return None
```

See `game-master-api/comic_generator.py` for complete implementation examples.

## Key Points About the Correction

1. **Session Management**: The ClientSession is properly managed within the async context manager
2. **Tool Discovery**: The tools are listed and made available to the agent
3. **MCP Client**: The MCPClient correctly wraps the session to provide tools in Strands format
4. **URL**: Updated to use the correct Pixelle-MCP port (9004) instead of generic 8000


## Usage Tips

1. **Server Availability**: Make sure the Pixelle-MCP server is running before making API calls
2. **URL Configuration**: Use `http://pixelle-mcp:9004/pixelle/mcp` as defined in docker-compose.yaml
3. **Error Handling**: Always wrap HTTP requests in try-catch blocks with proper timeouts
4. **Async Operations**: Use aiohttp.ClientSession for async HTTP calls
5. **Logging**: Enable logging to debug connection issues and track generation status

## Troubleshooting

- If you get connection errors, verify that the Pixelle-MCP service is running
- Check the docker-compose logs: `docker-compose logs pixelle-mcp`
- Ensure the URL matches the service configuration in docker-compose (`http://pixelle-mcp:9004/pixelle/mcp`)
- Verify that the network configuration allows communication between services (spark-network)
- Check ComfyUI health status - Pixelle-MCP depends on it being healthy
