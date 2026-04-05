# ComfyUI Integration

## Overview

This document explains how to use the ComfyUI HTTP API to connect your AI game agents to the content generation service.

## Required Libraries

- `strands-agents` - Main agent framework for LLM generation
- `aiohttp` - HTTP client for ComfyUI API calls
- `asyncio` - Asynchronous programming support

## Current Implementation Status

**NOTE:** The system uses direct HTTP API calls to ComfyUI. See `game-master-api/comic_generator.py` for the actual implementation pattern.

## HTTP API Integration Pattern

The current implementation uses aiohttp to call ComfyUI endpoints directly:

```python
import aiohttp

async def generate_image(prompt: str) -> Optional[str]:
    """Generate an image using ComfyUI HTTP API"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{COMFYUI_URL}/prompt",
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

## Key Points

1. **Session Management**: The ClientSession is properly managed within the async context manager
2. **URL Configuration**: Use `http://comfyui:8188` as defined in docker-compose.yaml
3. **Error Handling**: Always wrap HTTP requests in try-catch blocks with proper timeouts
4. **Async Operations**: Use aiohttp.ClientSession for async HTTP calls
5. **Logging**: Enable logging to debug connection issues and track generation status

## Troubleshooting

- If you get connection errors, verify that the ComfyUI service is running
- Check the docker-compose logs: `docker-compose logs comfyui`
- Ensure the URL matches the service configuration in docker-compose (`http://comfyui:8188`)
- Verify that the network configuration allows communication between services (spark-network)
- Check ComfyUI health status
