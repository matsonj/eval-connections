"""Remove duplicate rows from controllog.events and controllog.postings in MotherDuck.

Strategy:
  1. Create clean tables with a canonical schema (fixing schema drift)
  2. INSERT deduplicated rows using ROW_NUMBER() partitioned by idempotency_key/posting_id
  3. Drop the old tables and rename the clean ones

Usage:
  uv run python scripts/dedupe_motherduck.py

  Or dry-run (prints counts only, no changes):
  uv run python scripts/dedupe_motherduck.py --dry-run
"""

import os
import sys
import duckdb  # type: ignore


def dedupe(target_db: str, dry_run: bool = False) -> None:
    con = duckdb.connect(target_db)

    # --- Report current state ---
    events_before = con.execute(
        "SELECT COUNT(*) as total, COUNT(DISTINCT COALESCE(idempotency_key, event_id::VARCHAR)) as uniq FROM controllog.events"
    ).fetchone()
    postings_before = con.execute(
        "SELECT COUNT(*) as total, COUNT(DISTINCT posting_id) as uniq FROM controllog.postings"
    ).fetchone()

    print(f"Events:   {events_before[0]} total, {events_before[1]} unique, {events_before[0] - events_before[1]} duplicates")
    print(f"Postings: {postings_before[0]} total, {postings_before[1]} unique, {postings_before[0] - postings_before[1]} duplicates")

    if dry_run:
        print("\n--dry-run: no changes made.")
        con.close()
        return

    if events_before[0] == events_before[1] and postings_before[0] == postings_before[1]:
        print("\nNo duplicates found. Nothing to do.")
        con.close()
        return

    print("\nDeduplicating...")

    # --- Dedupe events ---
    con.execute("""
        CREATE TABLE controllog.events_clean AS
        SELECT
            event_id::UUID AS event_id,
            event_time::VARCHAR AS event_time,
            kind::VARCHAR AS kind,
            actor_agent_id::VARCHAR AS actor_agent_id,
            actor_task_id::VARCHAR AS actor_task_id,
            project_id::VARCHAR AS project_id,
            run_id::VARCHAR AS run_id,
            source::VARCHAR AS source,
            idempotency_key::VARCHAR AS idempotency_key,
            payload_json
        FROM (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(idempotency_key, event_id::VARCHAR)
                    ORDER BY event_time
                ) AS rn
            FROM controllog.events
        )
        WHERE rn = 1
    """)

    # --- Dedupe postings ---
    con.execute("""
        CREATE TABLE controllog.postings_clean AS
        SELECT
            posting_id::UUID AS posting_id,
            event_id::UUID AS event_id,
            account_type::VARCHAR AS account_type,
            account_id::VARCHAR AS account_id,
            unit::VARCHAR AS unit,
            delta_numeric::DOUBLE AS delta_numeric,
            dims_json
        FROM (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY posting_id
                ) AS rn
            FROM controllog.postings
        )
        WHERE rn = 1
    """)

    # --- Verify counts before swap ---
    events_after = con.execute("SELECT COUNT(*) FROM controllog.events_clean").fetchone()[0]
    postings_after = con.execute("SELECT COUNT(*) FROM controllog.postings_clean").fetchone()[0]

    print(f"Events:   {events_before[0]} -> {events_after} (removed {events_before[0] - events_after})")
    print(f"Postings: {postings_before[0]} -> {postings_after} (removed {postings_before[0] - postings_after})")

    # --- Swap tables ---
    con.execute("DROP TABLE controllog.events")
    con.execute("ALTER TABLE controllog.events_clean RENAME TO events")

    con.execute("DROP TABLE controllog.postings")
    con.execute("ALTER TABLE controllog.postings_clean RENAME TO postings")

    print("\nDone. Tables swapped successfully.")
    con.close()


if __name__ == "__main__":
    db = os.environ.get("MOTHERDUCK_DB", "md:my_db")
    dry = "--dry-run" in sys.argv
    dedupe(db, dry_run=dry)
