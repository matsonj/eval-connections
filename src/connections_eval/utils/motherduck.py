"""Loading controllog files into DuckDB/MotherDuck, plus upload validation."""

import os
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
import duckdb  # type: ignore

# Canonical column lists. JSONL may contain extra fields (e.g. ingest_time) that we skip.
EVENTS_COLS = [
    "event_id", "event_time", "kind", "actor_agent_id", "actor_task_id",
    "project_id", "run_id", "source", "idempotency_key", "payload_json",
]
POSTINGS_COLS = [
    "posting_id", "event_id", "account_type", "account_id",
    "unit", "delta_numeric", "dims_json",
]

DEFAULT_DB = "md:"


def connect_motherduck(db: Optional[str] = None) -> duckdb.DuckDBPyConnection:
    """Connect to MotherDuck (or a local DuckDB file).

    Args:
        db: connection string; defaults to $MOTHERDUCK_DB, else "md:".
    """
    target = db or os.environ.get("MOTHERDUCK_DB", DEFAULT_DB)
    if target.startswith("md:"):
        return duckdb.connect(target)
    return duckdb.connect(str(Path(target)))


def load_directory(base_log_dir: Path, target_db: str = DEFAULT_DB) -> None:
    """Append controllog JSONL partitions into controllog.events/postings.

    Rows already present are skipped, so re-running a partition is a no-op.
    """
    base = Path(base_log_dir) / "controllog"
    base.mkdir(parents=True, exist_ok=True)

    events_files = [str(p) for p in base.glob("*/events.jsonl")]
    postings_files = [str(p) for p in base.glob("*/postings.jsonl")]

    con = connect_motherduck(target_db)

    # Use a dedicated schema to avoid search_path issues in MD
    con.execute("CREATE SCHEMA IF NOT EXISTS controllog")

    if events_files:
        # idempotency_key must stay VARCHAR: a partition whose keys are all
        # plain UUIDs (e.g. a run with zero guesses) makes read_json_auto infer
        # UUID, and the dedup comparison then casts the target's suffixed keys
        # ("<uuid>:prompt") to UUID, which fails with an INT128 conversion error.
        ecols = ", ".join(
            f"{c}::VARCHAR AS {c}" if c == "idempotency_key" else c
            for c in EVENTS_COLS
        )

        # Ensure table exists with canonical schema
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS controllog.events AS
            SELECT {ecols}
            FROM read_json_auto(?, format='newline_delimited') WHERE 0;
            """,
            [events_files[0]],
        )

        # Deduplicate using idempotency_key (falls back to event_id for legacy rows)
        con.execute(
            f"""
            INSERT INTO controllog.events
            SELECT {ecols}
            FROM read_json_auto(?, format='newline_delimited') src
            WHERE NOT EXISTS (
                SELECT 1 FROM controllog.events tgt
                WHERE tgt.idempotency_key IS NOT NULL
                  AND src.idempotency_key IS NOT NULL
                  AND tgt.idempotency_key = src.idempotency_key::VARCHAR
            )
            AND NOT EXISTS (
                SELECT 1 FROM controllog.events tgt
                WHERE tgt.event_id = src.event_id
            );
            """,
            [events_files],
        )

    if postings_files:
        pcols = ", ".join(POSTINGS_COLS)

        # Ensure table exists with canonical schema
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS controllog.postings AS
            SELECT {pcols}
            FROM read_json_auto(?, format='newline_delimited') WHERE 0;
            """,
            [postings_files[0]],
        )

        # Deduplicate using posting_id
        con.execute(
            f"""
            INSERT INTO controllog.postings
            SELECT {pcols}
            FROM read_json_auto(?, format='newline_delimited') src
            WHERE NOT EXISTS (
                SELECT 1 FROM controllog.postings tgt
                WHERE tgt.posting_id = src.posting_id
            );
            """,
            [postings_files],
        )

    con.close()


def trial_balance(con) -> None:
    """Raise RuntimeError unless every resource account nets to zero per unit."""
    rows = con.execute(
        """
        WITH sums AS (
          SELECT account_type, unit, SUM(delta_numeric) AS net
          FROM controllog.postings
          WHERE account_type LIKE 'resource.%' OR account_type IN ('truth.state','value.utility')
          GROUP BY 1,2
        )
        SELECT * FROM sums WHERE ABS(net) > 1e-9
        """
    ).fetchall()
    if rows:
        raise RuntimeError(f"Trial balance FAILED: {rows}")


def upload_controllog_to_motherduck(log_path: Path, db: str) -> bool:
    """
    Upload controllog files to MotherDuck.

    Args:
        log_path: Base log directory containing controllog subdirectory
        db: MotherDuck database connection string (e.g., "md:my_db")

    Returns:
        True if upload succeeded, False otherwise
    """
    try:
        load_directory(log_path, db)
        return True
    except Exception as e:
        print(f"Error uploading to MotherDuck: {e}")
        return False


def validate_upload(run_id: str, db: str) -> bool:
    """
    Validate that the run's events exist in MotherDuck.

    Args:
        run_id: The run_id to validate
        db: MotherDuck database connection string

    Returns:
        True if validation passed, False otherwise
    """
    try:
        con = connect_motherduck(db)

        # Postings may legitimately be zero (no resource tracking), so events
        # are the only signal worth failing on.
        events_result = con.execute(
            "SELECT COUNT(*) FROM controllog.events WHERE run_id = ?",
            [run_id]
        ).fetchone()
        con.close()

        return bool(events_result) and events_result[0] > 0

    except Exception as e:
        print(f"Error validating upload: {e}")
        return False


def run_trial_balance(db: str) -> bool:
    """
    Run trial balance check on MotherDuck database.

    Args:
        db: MotherDuck database connection string

    Returns:
        True if trial balance passed, False otherwise
    """
    try:
        con = connect_motherduck(db)
        trial_balance(con)
        con.close()
        return True
    except RuntimeError as e:
        print(f"Trial balance failed: {e}")
        return False
    except Exception as e:
        print(f"Error running trial balance: {e}")
        return False


def cleanup_local_files(log_path: Path, run_id: str) -> None:
    """
    Delete this run's controllog records from the local JSONL files.

    Since multiple runs can share the same date-partitioned directory, this function
    filters the JSONL files to remove only lines related to this run_id.

    Args:
        log_path: Base log directory containing controllog subdirectory
        run_id: The run_id to identify which files to clean up
    """
    try:
        # Extract date from run_id (format: YYYY-MM-DDTHH-MM-SS_model)
        date_str = run_id.split("T")[0] if "T" in run_id else None

        if not date_str:
            # Fallback to today's date if we can't parse it
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        controllog_dir = log_path / "controllog" / date_str
        if not controllog_dir.exists() or not controllog_dir.is_dir():
            return

        events_file = controllog_dir / "events.jsonl"
        postings_file = controllog_dir / "postings.jsonl"

        # Check if files exist and filter them
        events_updated = False
        postings_updated = False
        event_ids_to_remove = set()

        # Filter events.jsonl - remove lines for this run_id
        if events_file.exists():
            filtered_events = []

            with open(events_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("run_id") == run_id:
                            event_ids_to_remove.add(event.get("event_id"))
                            events_updated = True
                        else:
                            filtered_events.append(line)
                    except json.JSONDecodeError:
                        # Keep malformed lines
                        filtered_events.append(line)

            # Write filtered events back
            if events_updated:
                with open(events_file, 'w', encoding='utf-8') as f:
                    for line in filtered_events:
                        f.write(line + '\n')

        # Filter postings.jsonl - remove postings for events we removed
        if postings_file.exists() and event_ids_to_remove:
            filtered_postings = []

            with open(postings_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        posting = json.loads(line)
                        if posting.get("event_id") not in event_ids_to_remove:
                            filtered_postings.append(line)
                        else:
                            postings_updated = True
                    except json.JSONDecodeError:
                        # Keep malformed lines
                        filtered_postings.append(line)

            # Write filtered postings back
            if postings_updated:
                with open(postings_file, 'w', encoding='utf-8') as f:
                    for line in filtered_postings:
                        f.write(line + '\n')

        # If both files are now empty, remove the directory
        if events_updated or postings_updated:
            if events_file.exists() and events_file.stat().st_size == 0:
                events_file.unlink()
            if postings_file.exists() and postings_file.stat().st_size == 0:
                postings_file.unlink()

            # Remove directory if it's now empty
            try:
                if not any(controllog_dir.iterdir()):
                    controllog_dir.rmdir()
                    print(f"Cleaned up empty controllog directory: {controllog_dir}")
                else:
                    print(f"Cleaned up controllog files for run_id: {run_id}")
            except OSError:
                # Directory not empty or other error, that's fine
                print(f"Cleaned up controllog files for run_id: {run_id}")

    except Exception as e:
        print(f"Warning: Error cleaning up local files: {e}")
