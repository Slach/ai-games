"""Bootstrap for telegram-bot tests.

bot.py has module-level side effects that assume the container
environment: player_store runs init_db() on import with a default
PLAYER_STATE_DB that only exists inside the container, and bot.py
configures root logging at import time. Point PLAYER_STATE_DB at a
throwaway file, and pre-configure root logging so bot.py's
basicConfig() becomes a no-op and test output stays at WARNING level.
"""

import logging
import os
import tempfile

logging.basicConfig(level=logging.WARNING, handlers=[logging.StreamHandler()])

_scratch = tempfile.mkdtemp(prefix="tg_bot_tests_")
os.environ.setdefault("PLAYER_STATE_DB", os.path.join(_scratch, "player_states.db"))
