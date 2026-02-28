"""
Game Master Agent - Scheduler that calls game-master-api

This service runs on a schedule and triggers the game-master-api to:
- Generate daily episodes
- Generate personalized comics for players
- Process player actions

It does NOT do the actual AI generation - that's handled by game-master-api.
"""

import asyncio
import logging
import os
from datetime import datetime

import aiohttp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Get configuration from environment
GAME_MASTER_API_URL = os.getenv("GAME_MASTER_API_URL", "http://game-master-api:8000")
GAME_SCHEDULE_TIME = os.getenv("GAME_SCHEDULE_TIME", "08:00")  # 24h format
GAME_LANGUAGE = os.getenv("GAME_LANGUAGE", "en")  # "en" or "ru"


class GameMasterScheduler:
    """
    Scheduler that calls game-master-api to generate daily content.
    """

    def __init__(self):
        self.api_url = GAME_MASTER_API_URL
        self.language = GAME_LANGUAGE
        self.last_generation = None

    async def generate_daily_episode(self) -> dict:
        """Call game-master-api to generate a new daily episode"""
        logger.info(f"Calling {self.api_url}/admin/generate-day to generate daily episode (language: {self.language})")

        try:
            async with aiohttp.ClientSession() as session:
                # Pass language as query parameter
                async with session.post(f"{self.api_url}/admin/generate-day?language={self.language}") as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"API error: {resp.status} - {error_text}")
                        raise Exception(f"API error: {resp.status}")

                    result = await resp.json()
                    self.last_generation = datetime.now()
                    logger.info(f"Daily episode generated successfully: Day {result.get('day')}")
                    return result

        except Exception as e:
            logger.error(f"Failed to generate daily episode: {e}")
            raise

    async def generate_comic_for_player(self, player_id: int, day: int | None = None) -> dict:
        """Call game-master-api to generate a personalized comic for a player"""
        logger.info(f"Calling API to generate comic for player {player_id}")

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.api_url}/admin/generate-comic/{player_id}"
                if day:
                    url += f"?day={day}"

                async with session.post(url) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"API error: {resp.status} - {error_text}")
                        raise Exception(f"API error: {resp.status}")

                    result = await resp.json()
                    logger.info(f"Comic generated for player {player_id}: {result.get('comic_url')}")
                    return result

        except Exception as e:
            logger.error(f"Failed to generate comic for player {player_id}: {e}")
            raise

    async def get_game_state(self) -> dict:
        """Get current game state from API"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/game/state") as resp:
                    if resp.status != 200:
                        raise Exception(f"API error: {resp.status}")
                    return await resp.json()
        except Exception as e:
            logger.error(f"Failed to get game state: {e}")
            raise

    def get_next_run_time(self) -> datetime:
        """Calculate next run time based on schedule"""
        now = datetime.now()
        schedule_hour, schedule_minute = map(int, GAME_SCHEDULE_TIME.split(":"))

        next_run = now.replace(hour=schedule_hour, minute=schedule_minute, second=0, microsecond=0)

        if next_run <= now:
            from datetime import timedelta
            next_run = next_run + timedelta(days=1)

        return next_run

    async def run_scheduled_loop(self):
        """Run the daily generation on a schedule"""
        logger.info(f"Starting scheduled loop. Daily generation at {GAME_SCHEDULE_TIME}")

        while True:
            try:
                # Calculate time until next run
                next_run = self.get_next_run_time()
                wait_seconds = (next_run - datetime.now()).total_seconds()

                logger.info(f"Next generation scheduled for {next_run.isoformat()} (in {wait_seconds/3600:.1f} hours)")

                # Wait until next run time
                await asyncio.sleep(wait_seconds)

                # Generate daily episode
                result = await self.generate_daily_episode()
                logger.info(f"Generation completed: {result.get('status', 'unknown')}")

            except Exception as e:
                logger.error(f"Error in scheduled loop: {e}")
                # Wait 1 hour before retrying on error
                await asyncio.sleep(3600)

    async def run_single_generation(self):
        """Run a single generation cycle (for testing)"""
        logger.info("Running single generation cycle")
        result = await self.generate_daily_episode()
        logger.info(f"Result: {result}")
        return result


async def main():
    """Main entry point"""
    logger.info("Starting Game Master Scheduler")
    logger.info(f"GAME_MASTER_API_URL: {GAME_MASTER_API_URL}")
    logger.info(f"GAME_SCHEDULE_TIME: {GAME_SCHEDULE_TIME}")
    logger.info(f"GAME_LANGUAGE: {GAME_LANGUAGE}")

    scheduler = GameMasterScheduler()

    # Check if we should run in single mode or scheduled mode
    mode = os.getenv("GAME_MASTER_MODE", "scheduled").lower()

    if mode == "single":
        logger.info("Running in single mode (one generation)")
        await scheduler.run_single_generation()
    else:
        logger.info("Running in scheduled mode")
        await scheduler.run_scheduled_loop()


if __name__ == "__main__":
    asyncio.run(main())