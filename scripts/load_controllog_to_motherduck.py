"""Load controllog JSONL into DuckDB or MotherDuck.

Thin CLI over ``connections_eval.utils.motherduck.load_directory``; re-exported
here so `scripts.load_controllog_to_motherduck.load_directory` keeps working for
manual uploads.

If MOTHERDUCK_DB starts with "md:", rows go to MotherDuck; otherwise to a local
DuckDB file at that path.
"""

import os
from pathlib import Path

from connections_eval.utils.motherduck import (  # noqa: F401  (re-export)
    EVENTS_COLS,
    POSTINGS_COLS,
    load_directory,
)

if __name__ == "__main__":
    log_dir = Path(os.environ.get("CTRL_LOG_DIR", "logs"))
    db = os.environ.get("MOTHERDUCK_DB", "md:")
    load_directory(log_dir, db)
