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

        # Compute threshold BEFORE inserting new rows
        last_event_time_row = con.execute(
            "SELECT max(event_time) FROM controllog.events"
        ).fetchone()
        last_event_time = last_event_time_row[0] if last_event_time_row else None

        if last_event_time is None:
            # No existing rows; load all
            con.execute(
                """
                INSERT INTO controllog.events
                SELECT * FROM read_json_auto(?, format='newline_delimited');
                """,
                [events_files],
            )
        else:
            # Incremental load: only rows strictly after the last seen timestamp
            con.execute(
                """
                INSERT INTO controllog.events
                SELECT *
                FROM read_json_auto(?, format='newline_delimited')
                WHERE event_time > ?;
                """,
                [events_files, last_event_time],
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

        # Use the same threshold captured before events were inserted in this run, if available.
        # If not computed (no events_files), derive it now from the table.
        try:
            last_event_time  # type: ignore[name-defined]
        except NameError:
            last_event_time_row = con.execute(
                "SELECT max(event_time) FROM controllog.events"
            ).fetchone()
            last_event_time = last_event_time_row[0] if last_event_time_row else None

        if last_event_time is None:
            # No existing events; load all postings
            con.execute(
                """
                INSERT INTO controllog.postings
                SELECT * FROM read_json_auto(?, format='newline_delimited');
                """,
                [postings_files],
            )
        else:
            # Incremental load: only postings whose event belongs to newly inserted events
            con.execute(
                """
                INSERT INTO controllog.postings
                SELECT p.*
                FROM read_json_auto(?, format='newline_delimited') p
                JOIN controllog.events e ON p.event_id = e.event_id
                WHERE e.event_time > ?;
                """,
                [postings_files, last_event_time],
            )

    con.close()


if __name__ == "__main__":
    log_dir = Path(os.environ.get("CTRL_LOG_DIR", "logs"))
    db = os.environ.get("MOTHERDUCK_DB", "md:")
    load_directory(log_dir, db)



