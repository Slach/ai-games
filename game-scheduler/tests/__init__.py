"""Bootstrap for game-scheduler tests.

main.py has a module-level side effect that assumes the container
environment: root logging is configured at import time with a FileHandler
inside logs/, a directory owned by the Docker container user. Redirect
FileHandler paths into a throwaway dir and pre-configure root logging so
main.py's basicConfig() becomes a no-op and test output stays lean.
"""

import logging
import os
import tempfile

_scratch = tempfile.mkdtemp(prefix="game_scheduler_tests_")
_RealFileHandler = logging.FileHandler


def _redirect_file_handler(*args, **kwargs):
    if args and isinstance(args[0], (str, os.PathLike)):
        args = (os.path.join(_scratch, os.path.basename(os.fspath(args[0]))),) + args[1:]
    return _RealFileHandler(*args, **kwargs)


logging.FileHandler = _redirect_file_handler
logging.basicConfig(level=logging.WARNING, handlers=[logging.StreamHandler()])
