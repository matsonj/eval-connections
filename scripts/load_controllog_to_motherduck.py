"""Load controllog JSONL into DuckDB or MotherDuck.

If MOTHERDUCK_DB starts with "md:", we ATTACH MotherDuck as a remote target and
push locally-read JSONL into remote tables. Otherwise, we create/update a local
DuckDB file.
"""

import os
from pathlib import Path
import duckdb  # type: ignore


def load_directory(base_log_dir: Path, target_db: str = "md:") -> None:
    base = Path(base_log_dir) / "controllog"
    base.mkdir(parents=True, exist_ok=True)

    # Append JSONL from all partitions
    events_files = [str(p) for p in base.glob("*/events.jsonl")]
    postings_files = [str(p) for p in base.glob("*/postings.jsonl")]

    # Connect directly to target (MotherDuck or local file)
    if target_db.startswith("md:"):
        con = duckdb.connect(target_db)
    else:
        con = duckdb.connect(str(Path(target_db)))

    # Use a dedicated schema to avoid search_path issues in MD
    con.execute("CREATE SCHEMA IF NOT EXISTS controllog")

    if events_files:
        # Ensure table exists (schema inferred from a sample file)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS controllog.events AS
            SELECT * FROM read_json_auto(?, format='newline_delimited') WHERE 0;
            """,
            [events_files[0]],
        )

        # Deduplicate using idempotency_key (falls back to event_id for legacy rows)
        con.execute(
            """
            INSERT INTO controllog.events
            SELECT src.*
            FROM read_json_auto(?, format='newline_delimited') src
            WHERE NOT EXISTS (
                SELECT 1 FROM controllog.events tgt
                WHERE tgt.idempotency_key IS NOT NULL
                  AND src.idempotency_key IS NOT NULL
                  AND tgt.idempotency_key = src.idempotency_key
            )
            AND NOT EXISTS (
                SELECT 1 FROM controllog.events tgt
                WHERE tgt.event_id = src.event_id
            );
            """,
            [events_files],
        )

    if postings_files:
        # Ensure table exists (schema inferred from a sample file)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS controllog.postings AS
            SELECT * FROM read_json_auto(?, format='newline_delimited') WHERE 0;
            """,
            [postings_files[0]],
        )

        # Deduplicate using posting_id
        con.execute(
            """
            INSERT INTO controllog.postings
            SELECT src.*
            FROM read_json_auto(?, format='newline_delimited') src
            WHERE NOT EXISTS (
                SELECT 1 FROM controllog.postings tgt
                WHERE tgt.posting_id = src.posting_id
            );
            """,
            [postings_files],
        )

    con.close()


if __name__ == "__main__":
    log_dir = Path(os.environ.get("CTRL_LOG_DIR", "logs"))
    db = os.environ.get("MOTHERDUCK_DB", "md:")
    load_directory(log_dir, db)



