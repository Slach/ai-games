"""Shared retry utility with exponential backoff for Telegram Bot API calls.

Used by bot.py and push_server.py to retry network-level errors (proxy
timeouts, DNS failures, connection resets) while letting application-level
errors (Telegram API rejections, invalid payloads) propagate immediately.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


async def call_with_retry(
    fn: Callable[[], Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
) -> Any:
    """Call an async function with exponential backoff on network errors.

    Retries on aiohttp.ClientError, TimeoutError, OSError (covers proxy
    timeouts, DNS failures, connection resets). All other errors (e.g.
    Telegram API rejecting a message) are re-raised immediately since
    they won't succeed on retry.

    Args:
        fn: Async callable (no args — use lambda/wrapper).
        max_retries: Number of retries *after* the first attempt.
            Total attempts = max_retries + 1. Default 3.
        base_delay: Initial delay in seconds. Doubled each retry
            (1s → 2s → 4s → 8s). Default 1.0.
        max_delay: Cap on the per-retry delay. Default 10.0.

    Returns:
        Whatever fn() returns on success.

    Raises:
        The last exception if all retries are exhausted.
        Non-retryable exceptions immediately.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except (aiohttp.ClientError, TimeoutError, OSError, asyncio.TimeoutError) as e:
            last_exc = e
            if attempt < max_retries:
                delay = min(base_delay * (2**attempt), max_delay)
                logger.warning(
                    "[RETRY] Attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt + 1,
                    max_retries + 1,
                    e,
                    delay,
                )
                await asyncio.sleep(delay)

    logger.error(
        "[RETRY] All %d attempts failed: %s",
        max_retries + 1,
        last_exc,
    )
    if last_exc is None:
        raise RuntimeError("unreachable: no exception was captured")
    raise last_exc
