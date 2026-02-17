"""Remove bogus vendor:upstream postings for non-BYOK runs.

Problem: For non-BYOK models, the OpenRouter API returns both `usage.cost`
and `usage.cost_details.upstream_inference_cost` with the same value.  Our
logging code recorded both, creating duplicate money postings:

  project:connections_eval  +X   (for openrouter)
  vendor:openrouter         -X
  project:connections_eval  +X   (for upstream — BOGUS)
  vendor:upstream           -X   (BOGUS)

This doubles the reported cost for affected runs.

Fix: For events that have BOTH vendor:openrouter AND vendor:upstream money
postings with non-zero amounts, delete the vendor:upstream posting and the
extra project posting.

Strategy: Rebuild controllog.postings via CTAS, excluding the bad rows.

Usage:
  uv run python scripts/fix_nonbyok_upstream_postings.py --dry-run
  uv run python scripts/fix_nonbyok_upstream_postings.py
"""

import os
import sys
import duckdb  # type: ignore


FIND_BAD_EVENTS = """
    SELECT p.event_id
    FROM controllog.postings p
    WHERE p.account_type = 'resource.money'
    AND p.account_id LIKE 'vendor:%'
    GROUP BY p.event_id
    HAVING
        COUNT(DISTINCT p.account_id) = 2
        AND SUM(CASE WHEN p.account_id = 'vendor:openrouter'
                     THEN ABS(p.delta_numeric) ELSE 0 END) > 0
        AND SUM(CASE WHEN p.account_id = 'vendor:upstream'
                     THEN ABS(p.delta_numeric) ELSE 0 END) > 0
"""


def fix_upstream_postings(target_db: str, dry_run: bool = False) -> None:
    con = duckdb.connect(target_db)

    # --- Report scope ---
    affected = con.execute(f"""
        WITH bad AS ({FIND_BAD_EVENTS})
        SELECT
            COUNT(DISTINCT bad.event_id) AS events,
            (SELECT COUNT(*) FROM controllog.postings p
             WHERE p.event_id IN (SELECT event_id FROM bad)
             AND p.account_type = 'resource.money'
             AND p.account_id = 'vendor:upstream')          AS upstream_postings,
            (SELECT COUNT(*) FROM controllog.postings p
             WHERE p.event_id IN (SELECT event_id FROM bad)
             AND p.account_type = 'resource.money'
             AND p.account_id LIKE 'project:%')             AS project_postings
        FROM bad
    """).fetchone()

    n_events, n_upstream, n_project = affected
    n_to_delete = n_upstream + n_upstream  # upstream + one duplicate project per event
    print(f"Affected events:           {n_events}")
    print(f"vendor:upstream to delete: {n_upstream}")
    print(f"project duplicates:        {n_upstream}  (1 of {n_project} per event)")
    print(f"Total postings to remove:  {n_to_delete}")

    # Show affected runs
    runs = con.execute(f"""
        WITH bad AS ({FIND_BAD_EVENTS})
        SELECT DISTINCT e.run_id,
            SUM(CASE WHEN p.account_id = 'vendor:upstream'
                     THEN ABS(p.delta_numeric) ELSE 0 END) AS bogus_cost
        FROM controllog.events e
        JOIN controllog.postings p ON p.event_id = e.event_id
        WHERE e.event_id IN (SELECT event_id FROM bad)
        AND p.account_type = 'resource.money'
        GROUP BY e.run_id
        ORDER BY e.run_id
    """).fetchall()
    print(f"\nAffected runs ({len(runs)}):")
    for run_id, bogus_cost in runs:
        print(f"  {run_id:50s}  ${bogus_cost:.4f}")

    if n_events == 0:
        print("\nNothing to fix.")
        con.close()
        return

    if dry_run:
        print("\n--dry-run: no changes made.")
        con.close()
        return

    # --- Total postings before ---
    total_before = con.execute("SELECT COUNT(*) FROM controllog.postings").fetchone()[0]

    # --- Build clean postings table ---
    # For each affected event, identify the posting_ids to DELETE:
    #   1. All vendor:upstream money postings
    #   2. One of the two duplicate project money postings (the one with the
    #      higher posting_id, to keep the first-created one)
    con.execute(f"""
        CREATE TABLE controllog.postings_fixed AS
        SELECT * FROM controllog.postings
        WHERE posting_id NOT IN (
            -- vendor:upstream postings for affected events
            SELECT p.posting_id
            FROM controllog.postings p
            WHERE p.event_id IN ({FIND_BAD_EVENTS})
            AND p.account_type = 'resource.money'
            AND p.account_id = 'vendor:upstream'

            UNION ALL

            -- duplicate project postings: keep rn=1, delete rn=2
            SELECT posting_id FROM (
                SELECT p.posting_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY p.event_id
                        ORDER BY p.posting_id DESC  -- delete the later one
                    ) AS rn
                FROM controllog.postings p
                WHERE p.event_id IN ({FIND_BAD_EVENTS})
                AND p.account_type = 'resource.money'
                AND p.account_id LIKE 'project:%'
            )
            WHERE rn = 1
        )
    """)

    total_after = con.execute("SELECT COUNT(*) FROM controllog.postings_fixed").fetchone()[0]
    removed = total_before - total_after

    print(f"\nPostings: {total_before} -> {total_after} (removed {removed})")

    if removed != n_to_delete:
        print(f"WARNING: expected to remove {n_to_delete}, actually removed {removed}")
        con.execute("DROP TABLE controllog.postings_fixed")
        print("Aborted. Dropped staging table.")
        con.close()
        return

    # --- Swap ---
    con.execute("DROP TABLE controllog.postings")
    con.execute("ALTER TABLE controllog.postings_fixed RENAME TO postings")
    print("Done. Table swapped successfully.")

    # --- Verify: no more dual-vendor events ---
    remaining = con.execute(f"""
        WITH bad AS ({FIND_BAD_EVENTS})
        SELECT COUNT(*) FROM bad
    """).fetchone()[0]
    print(f"Remaining dual-vendor events: {remaining}")

    con.close()


if __name__ == "__main__":
    db = os.environ.get("MOTHERDUCK_DB", "md:my_db")
    dry = "--dry-run" in sys.argv
    fix_upstream_postings(db, dry_run=dry)
