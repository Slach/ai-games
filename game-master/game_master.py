"""
Game Master Agent - Scheduler that calls game-master-api

This service runs on a schedule and triggers the game-master-api to:
- Generate daily episodes with game state validation
- Generate personalized comics for each player
- Process player actions and auto-select if needed
- Track team assembly over 3 days
- Integrate npcpy for dynamic NPCs (when available)

It does NOT do the actual AI generation - that's handled by game-master-api.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

import aiohttp

# Try to import npcpy for dynamic NPC behaviors
try:
    import npcpy
    NPCPY_AVAILABLE = True
except ImportError:
    NPCPY_AVAILABLE = False

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
AUTO_ACTION_TIMEOUT_HOURS = int(os.getenv("AUTO_ACTION_TIMEOUT_HOURS", "24"))  # Hours before auto-selection


class GameMasterScheduler:
    """
    Scheduler that calls game-master-api to generate daily content with full game loop support.
    """

    def __init__(self):
        self.api_url = GAME_MASTER_API_URL
        self.language = GAME_LANGUAGE
        self.last_generation = None

    async def check_game_state(self) -> Dict[str, Any]:
        """Check current game state and verify ship/crew are alive"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/game/state") as resp:
                    if resp.status != 200:
                        raise Exception(f"API error: {resp.status}")
                    return await resp.json()
        except Exception as e:
            logger.error(f"Failed to get game state: {e}")
            raise

    async def validate_game_active(self) -> bool:
        """Validate that game is still active (ship and crew alive)"""
        try:
            state = await self.check_game_state()
            
            if not state.get("ship_alive", False):
                logger.warning("Game ended - ship destroyed, stopping generation")
                return False
            
            if state.get("crew_health", 100) <= 0:
                logger.warning("Game ended - crew health depleted, stopping generation")
                return False
            
            if state.get("status") != "active":
                logger.warning(f"Game status is {state.get('status')}, not active")
                return False
            
            return True
        except Exception as e:
            logger.error(f"Failed to validate game state: {e}")
            return False

    async def get_previous_day_actions(self, day: int) -> List[Dict[str, Any]]:
        """Get all player actions from previous day with consequences"""
        try:
            async with aiohttp.ClientSession() as session:
                # Get game state to find current day
                state = await self.check_game_state()
                current_day = state.get("day", 1)
                
                # Get previous day (current - 1)
                prev_day = current_day - 1
                
                if prev_day <= 0:
                    return []
                
                # Fetch previous day data from API
                async with session.get(f"{self.api_url}/game/day/{prev_day}") as resp:
                    if resp.status != 200:
                        logger.warning(f"Could not fetch previous day {prev_day}")
                        return []
                    
                    day_data = await resp.json()
                    return day_data.get("player_actions", [])
        except Exception as e:
            logger.error(f"Failed to get previous day actions: {e}")
            return []

    async def get_players_in_game(self, game_id: str = "default_game") -> List[int]:
        """Get list of player IDs in the current game"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/players/{game_id}/players") as resp:
                    if resp.status != 200:
                        # Fallback to getting all players from profile endpoint
                        return []
                    result = await resp.json()
                    return result.get("player_ids", [])
        except Exception as e:
            logger.error(f"Failed to get players in game: {e}")
            return []

    async def generate_personalized_comics(self, day_data: Dict[str, Any], game_id: str = "default_game") -> List[Dict[str, Any]]:
        """Generate personalized comics for all players in the game"""
        if not NPCPY_AVAILABLE:
            logger.info("NPCPY not available, using static comic generation")
        
        player_ids = await self.get_players_in_game(game_id)
        comics_generated = []
        
        for player_id in player_ids:
            try:
                logger.info(f"Generating personalized comic for player {player_id}")
                
                async with aiohttp.ClientSession() as session:
                    url = f"{self.api_url}/admin/generate-comic/{player_id}"
                    if day_data.get("day"):
                        url += f"?day={day_data['day']}"
                    
                    async with session.post(url) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            comics_generated.append({
                                "player_id": player_id,
                                "comic_url": result.get("comic_url"),
                                "role": result.get("role")
                            })
                            logger.info(f"Comic generated for player {player_id}: {result.get('comic_url')}")
                        else:
                            error_text = await resp.text()
                            logger.error(f"Failed to generate comic for player {player_id}: {resp.status} - {error_text}")
            except Exception as e:
                logger.error(f"Error generating comic for player {player_id}: {e}")
        
        return comics_generated

    async def select_auto_action(self, player_id: int, day: int) -> Optional[Dict[str, Any]]:
        """Select default action for player who hasn't chosen"""
        try:
            logger.info(f"Auto-selecting action for player {player_id} on day {day}")
            
            async with aiohttp.ClientSession() as session:
                # Get player profile to determine personality
                async with session.get(f"{self.api_url}/players/{player_id}/profile") as resp:
                    if resp.status != 200:
                        logger.warning(f"Could not get profile for player {player_id}")
                        return None
                    
                    profile = await resp.json()
                    traits = profile.get("personality_traits", [])
                
                # Get current day to see available actions
                async with session.get(f"{self.api_url}/game/current-day") as resp:
                    if resp.status != 200:
                        logger.warning(f"Could not get current day")
                        return None
                    
                    day_data = await resp.json()
                    actions = day_data.get("player_actions", [])
                
                # Select action based on traits
                selected_action = None
                
                if "логичный" in traits or "аналитический" in traits:
                    selected_action = actions[0] if len(actions) > 0 else None
                elif "смелый" in traits or "решительный" in traits:
                    selected_action = actions[1] if len(actions) > 1 else (actions[0] if len(actions) > 0 else None)
                else:
                    selected_action = actions[2] if len(actions) > 2 else (actions[1] if len(actions) > 1 else (actions[0] if len(actions) > 0 else None))
                
                if selected_action:
                    # Submit auto-selected action
                    async with session.post(f"{self.api_url}/game/actions", json={
                        "player_id": player_id,
                        "day": day,
                        "action_id": selected_action.get("id"),
                        "choice": "auto_selected"
                    }) as resp:
                        if resp.status == 200:
                            logger.info(f"Auto-selected action {selected_action.get('id')} for player {player_id}")
                            return selected_action
                        else:
                            logger.error(f"Failed to submit auto-selected action")
                            return None
                
                return None
        except Exception as e:
            logger.error(f"Failed to select auto action for player {player_id}: {e}")
            return None

    async def check_and_auto_select_actions(self, day: int):
        """Check for players who haven't selected actions and auto-select for them"""
        try:
            # Get all players in game
            player_ids = await self.get_players_in_game()
            
            for player_id in player_ids:
                # Check if player has already selected action
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{self.api_url}/game/actions/{player_id}/{day}") as resp:
                        if resp.status == 200:
                            actions = await resp.json()
                            if actions.get("has_action"):
                                continue  # Player already selected
                        
                        # No action found, auto-select
                        await self.select_auto_action(player_id, day)
        except Exception as e:
            logger.error(f"Failed to check and auto-select actions: {e}")

    async def get_team_assembly_status(self, game_id: str = "default_game") -> Dict[str, Any]:
        """Track team assembly over 3 days from first crew member"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/game/team-status/{game_id}") as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        return {
                            "days_since_first": 0,
                            "team_assembled": False,
                            "bot_npcs_needed": []
                        }
        except Exception as e:
            logger.error(f"Failed to get team assembly status: {e}")
            return {
                "days_since_first": 0,
                "team_assembled": False,
                "bot_npcs_needed": []
            }

    async def generate_daily_episode(self) -> dict:
        """Generate new daily episode with full game loop validation"""
        logger.info(f"=== DAILY EPISODE GENERATION STARTED ===")
        logger.info(f"Language: {self.language}")

        # Step 1: Validate game state
        is_active = await self.validate_game_active()
        if not is_active:
            logger.warning("Game ended - stopping generation")
            return {"status": "game_ended", "message": "Game has ended, no new episode generated"}

        # Step 2: Get current game state and previous day info
        state = await self.check_game_state()
        current_day = state.get("day", 1)
        
        logger.info(f"Generating Day {current_day}")
        
        # Step 3: Get previous day actions for story consistency
        previous_actions = await self.get_previous_day_actions(current_day - 1)
        previous_summary = ""
        if previous_actions:
            consequences = [a.get("consequence_result", {}) for a in previous_actions]
            previous_summary = f"Previous day consequences: {consequences}"
            logger.info(f"Incorporating {len(previous_actions)} previous actions into story")

        # Step 4: Check team assembly status
        team_status = await self.get_team_assembly_status()
        if not team_status.get("team_assembled"):
            bot_npcs = team_status.get("bot_npcs_needed", [])
            logger.info(f"Team not fully assembled, adding {len(bot_npcs)} NPC bots")

        # Step 5: Call API to generate daily episode with previous actions context
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "language": self.language,
                    "previous_actions": previous_actions,
                    "previous_summary": previous_summary,
                    "team_status": team_status
                }
                
                async with session.post(f"{self.api_url}/admin/generate-day", json=payload) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"API error: {resp.status} - {error_text}")
                        raise Exception(f"API error: {resp.status}")

                    result = await resp.json()
                    self.last_generation = datetime.now()
                    
                    # Step 6: Generate personalized comics for all players
                    if result.get("day"):
                        logger.info("Generating personalized comics for all players")
                        comics = await self.generate_personalized_comics(result)
                        result["comics_generated"] = comics
                    
                    # Step 7: Check and auto-select actions for inactive players
                    logger.info("Checking for players who need auto-action selection")
                    await self.check_and_auto_select_actions(current_day)
                    
                    logger.info(f"=== DAILY EPISODE GENERATION COMPLETED ===")
                    logger.info(f"Day {current_day} generated with {len(previous_actions)} previous actions incorporated")
                    logger.info(f"Comics generated for {len(result.get('comics_generated', []))} players")
                    
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

    async def update_npc_team(self, day: int) -> dict:
        """
        Update NPC team based on team assembly logic.
        Track team assembly over 3 days from first crew member.
        Assign bot NPCs if team not full after 3 days.
        """
        logger.info(f"Updating NPC team for day {day}")

        try:
            async with aiohttp.ClientSession() as session:
                # Call API to update NPC team
                async with session.post(f"{self.api_url}/admin/update-npc-team", json={
                    "day": day,
                    "language": self.language
                }) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"API error: {resp.status} - {error_text}")
                        raise Exception(f"API error: {resp.status}")

                    result = await resp.json()
                    logger.info(f"NPC team updated: {result.get('npc_count')} NPCs")
                    return result

        except Exception as e:
            logger.error(f"Failed to update NPC team: {e}")
            return {"status": "failed", "error": str(e)}

    async def notify_player_auto_action(self, player_id: int, day: int, action_text: str) -> bool:
        """
        Notify player that AI selected an action on their behalf.
        Returns True if notification sent successfully.
        """
        logger.info(f"Sending auto-action notification to player {player_id}")

        try:
            async with aiohttp.ClientSession() as session:
                # Call API to send notification
                async with session.post(f"{self.api_url}/admin/notify-player", json={
                    "player_id": player_id,
                    "day": day,
                    "message_type": "auto_action",
                    "action_text": action_text
                }) as resp:
                    if resp.status != 200:
                        logger.warning(f"Failed to send notification: {resp.status}")
                        return False
                    
                    result = await resp.json()
                    logger.info(f"Notification sent to player {player_id}")
                    return True

        except Exception as e:
            logger.error(f"Failed to notify player: {e}")
            return False

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
        """Run the daily generation on a schedule with full game loop"""
        logger.info(f"Starting scheduled loop. Daily generation at {GAME_SCHEDULE_TIME}")

        while True:
            try:
                # Calculate time until next run
                next_run = self.get_next_run_time()
                wait_seconds = (next_run - datetime.now()).total_seconds()

                logger.info(f"Next generation scheduled for {next_run.isoformat()} (in {wait_seconds/3600:.1f} hours)")

                # Wait until next run time
                await asyncio.sleep(wait_seconds)

                # Generate daily episode with full game loop validation
                result = await self.generate_daily_episode()
                
                if result.get("status") == "game_ended":
                    logger.info("Game has ended, stopping scheduled generation")
                    break
                
                logger.info(f"Generation completed: Day {result.get('day')}")

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
