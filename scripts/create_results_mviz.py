#!/usr/bin/env python3
"""Create results pages using mviz.

Generates two leaderboards from results/run_summaries.csv:
- One-shot (primary): docs/results.md -> docs/index.html
- Classic (multi-turn): docs/classic.md -> docs/classic.html
"""

import json
import os
import re
import subprocess

import duckdb

# Columns added in later CSV versions. Backfilled as NULL when the CSV
# predates them so older CSVs still render; missing values stay NULL so
# downstream can show "—" for "not measured".
BACKFILL_COLUMNS = (
    "avg_inference_sec",
    "total_inference_sec",
    "total_backoff_sec",
    "total_score",
    "total_trap_bonus",
    "max_score",
    "avg_score",
    "trap_scored",
)

# A single connection is used for every query in this script. DuckDB's
# read_csv interprets naive timestamps in the session timezone, and the
# TIMESTAMPTZ values it returns are rendered in that same timezone — both
# steps must agree with the old pandas `to_datetime(..., utc=True)` behavior,
# so the session timezone is pinned to UTC here rather than left to whatever
# connection happens to run a given query.
_CON = duckdb.connect()
_CON.execute("SET TimeZone='UTC'")


def load_summaries(csv_file: str = "results/run_summaries.csv") -> duckdb.DuckDBPyRelation:
    """Read the run summaries CSV and normalize columns across CSV versions.

    Returns a DuckDB relation with a `_csv_row` column recording each row's
    original position in the file (used later to break ties the same way
    pandas' idxmax/stable-sort would).
    """
    header = _CON.sql(
        f"SELECT * FROM read_csv({csv_file!r}, parallel=false) LIMIT 0"
    )
    header_cols = set(header.columns)

    select_parts = ["*"]
    for col in BACKFILL_COLUMNS:
        if col not in header_cols:
            select_parts.append(f"NULL AS {col}")

    # Pre-4.0 CSVs have no mode column; every run back then was classic.
    if "mode" not in header_cols:
        select_parts.append("'classic' AS mode")

    select_clause = ", ".join(select_parts)
    query = f"""
        SELECT {select_clause}, row_number() OVER () AS _csv_row
        FROM read_csv({csv_file!r}, parallel=false)
    """
    df = _CON.sql(query)

    if "mode" in header_cols:
        df = _CON.sql("SELECT * REPLACE (COALESCE(mode, 'classic') AS mode) FROM df")

    return df


def filter_for_mode(df: duckdb.DuckDBPyRelation, mode: str = "oneshot") -> list[dict]:
    """Apply the leaderboard filtering logic for one eval mode.

    Returns the selected rows (latest run per model, sorted for display) as
    a list of plain dicts.
    """
    trap_clause = ""
    if mode == "oneshot":
        # Legacy pre-trap one-shot smoke runs (no _TRAP_ in result strings) used a
        # different scoring scale — exclude them so the board only compares
        # trap-scored runs.
        trap_clause = "AND COALESCE(trap_scored, 0) = 1"

    filtered = _CON.sql(  # noqa: F841 — referenced by name in later SQL (DuckDB replacement scan)
        f"""
        SELECT *
        FROM df
        WHERE puzzles_attempted = 20
          AND total_cost IS NOT NULL
          AND mode = '{mode}'
          -- Rows whose model couldn't be resolved from telemetry (garbage runs)
          AND model != 'unknown'
          {trap_clause}
        """
    )

    # Fetch only a count here (not a full row): `filtered` still carries the
    # raw TIMESTAMPTZ start_timestamp/end_timestamp columns, and DuckDB's
    # Python conversion for timezone-aware timestamps needs pytz, which isn't
    # a project dependency — those columns are reformatted to plain
    # strings before any full-row fetch below.
    if _CON.sql("SELECT count(*) FROM filtered").fetchone()[0] == 0:
        return []

    # Combined eval cost. Latest run per model, tie-broken by original CSV
    # row order (mirrors groupby(...).idxmax() picking the first occurrence
    # of the max timestamp within a group).
    with_eval_cost = _CON.sql(  # noqa: F841 — referenced by name in later SQL (DuckDB replacement scan)
        """
        SELECT *,
               total_cost + COALESCE(total_upstream_cost, 0) AS eval_cost,
               row_number() OVER (
                   PARTITION BY model
                   ORDER BY start_timestamp DESC, _csv_row ASC
               ) AS _rn
        FROM filtered
        """
    )

    latest_runs = _CON.sql(  # noqa: F841 — referenced by name in later SQL (DuckDB replacement scan)
        """
        SELECT *,
               eval_cost / puzzles_attempted AS eval_cost_per_game,
               -- Sort by inference time (fair across upstream-throttled models)
               -- with wall time as a tiebreaker for historical runs that lack
               -- backoff data.
               COALESCE(avg_inference_sec, avg_time_sec) AS _sort_time,
               row_number() OVER (ORDER BY model ASC) AS _orig_order
        FROM with_eval_cost
        WHERE _rn = 1
        """
    )

    # Reformat start_timestamp to the plain date string build_table_data
    # needs, and drop the unused end_timestamp: both are TIMESTAMPTZ, and
    # fetching those into Python (below) would otherwise require pytz.
    if mode == "oneshot":
        # Headline metric is total score (max 5 per puzzle = 100 on canonical).
        ordered = _CON.sql(
            """
            SELECT * EXCLUDE (end_timestamp)
                   REPLACE (
                       COALESCE(total_score, 0) AS total_score,
                       strftime(start_timestamp, '%Y-%m-%d') AS start_timestamp
                   )
            FROM latest_runs
            ORDER BY COALESCE(total_score, 0) DESC, _sort_time ASC,
                     eval_cost_per_game ASC, _orig_order ASC
            """
        )
    else:
        ordered = _CON.sql(
            """
            SELECT * EXCLUDE (end_timestamp)
                   REPLACE (strftime(start_timestamp, '%Y-%m-%d') AS start_timestamp)
            FROM latest_runs
            ORDER BY solve_rate DESC, _sort_time ASC,
                     eval_cost_per_game ASC, _orig_order ASC
            """
        )

    cols = ordered.columns
    return [dict(zip(cols, row)) for row in ordered.fetchall()]


def format_time(seconds):
    if seconds is None:
        return "0s"
    try:
        seconds = float(seconds)
        if seconds < 0:
            seconds = 0
    except (ValueError, TypeError):
        return "0s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes > 0:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def format_tokens(tokens):
    return f"{tokens / 1000:.1f}k"


def build_table_data(rows: list[dict], mode: str = "oneshot") -> list[dict[str, str]]:
    """Build the data rows for the mviz table."""
    data = []
    for row in rows:
        run_id = row.get("run_id", "")
        model_name = row["model"]
        if run_id:
            model_cell = f'<a href="logs/{run_id}.html">{model_name}</a>'
        else:
            model_cell = model_name

        # Already formatted to a plain date string in filter_for_mode's SQL.
        date = row["start_timestamp"]
        avg_tok = row["total_tokens"] / row["puzzles_attempted"]
        avg_c = row["eval_cost"] / row["puzzles_attempted"]

        # Inference time when we measured it (wall minus retry backoff),
        # falling back to wall time for historical runs we didn't instrument.
        avg_inference = row.get("avg_inference_sec")
        if avg_inference is None:
            avg_time_display = format_time(row["avg_time_sec"])
        else:
            avg_time_display = format_time(avg_inference)

        common_tail = {
            "avg_time": avg_time_display,
            "tok_per_game": format_tokens(avg_tok),
            "cost": round(float(row["eval_cost"]), 2),
            "cost_per_game": f"${avg_c:.3f}",
        }

        if mode == "oneshot":
            # correct_guesses = groups matched; 4 groups possible per puzzle.
            grp_hit = int(row["correct_guesses"])
            grp_max = 4 * int(row["puzzles_attempted"])
            max_score = row.get("max_score")
            max_score = (
                int(max_score)
                if max_score is not None
                else 5 * int(row["puzzles_attempted"])
            )
            trap_bonus = row.get("total_trap_bonus")
            trap_cell = "—" if trap_bonus is None else str(int(trap_bonus))
            data.append({
                "model": model_cell,
                "date": date,
                "pts": int(row["total_score"]),
                "pts_pct": round(float(row["total_score"]) / max_score, 4) if max_score else 0.0,
                "w": str(int(row["puzzles_solved"])),
                "grp": f"{grp_hit}/{grp_max}",
                "trap": trap_cell,
                "inv": str(int(row["invalid_responses"])),
                **common_tail,
            })
        else:
            hit = int(row["correct_guesses"])
            att = int(row["total_guesses"])
            data.append({
                "model": model_cell,
                "date": date,
                "w": str(int(row["puzzles_solved"])),
                "win_pct": round(float(row["solve_rate"]), 4),
                "hit_att": f"{hit}/{att}",
                "acc_pct": round(float(row["guess_accuracy"]), 4),
                **common_tail,
            })

    return data


def write_mviz_markdown(
    rows: list[dict], output_path: str = "docs/results.md", mode: str = "oneshot"
):
    """Write the mviz markdown file with table spec."""
    num_models = len(rows)
    data = build_table_data(rows, mode)

    common_tail_columns = [
        {"id": "avg_time", "title": "AVG/G", "align": "right"},
        {"id": "tok_per_game", "title": "TOK/G", "align": "right"},
        {"id": "cost", "title": "COST", "align": "right", "type": "heatmap", "higherIsBetter": False, "fmt": "currency_auto"},
        {"id": "cost_per_game", "title": "$/G", "align": "right"},
    ]

    if mode == "oneshot":
        title = "Connections Eval — One-Shot Box Score"
        intro = (
            f"Latest one-shot runs for {num_models} models (20 games each, one submission per game, "
            f"max 100 pts; sorted by points, avg time, cost) · "
            f"[Classic (multi-turn) leaderboard →](classic.html)"
        )
        columns = [
            {"id": "model", "title": "Model", "bold": True},
            {"id": "date", "title": "Date"},
            {"id": "pts", "title": "PTS", "align": "right", "bold": True, "type": "heatmap", "higherIsBetter": True},
            {"id": "pts_pct", "title": "PTS%", "align": "right", "type": "heatmap", "higherIsBetter": True, "fmt": "pct1"},
            {"id": "w", "title": "W", "align": "right"},
            {"id": "grp", "title": "GRP", "align": "right"},
            {"id": "trap", "title": "TRAP", "align": "right"},
            {"id": "inv", "title": "INV", "align": "right"},
            *common_tail_columns,
        ]
    else:
        title = "Connections Eval — Classic Box Score"
        intro = (
            f"Latest classic (multi-turn) runs for {num_models} models (20 games each, "
            f"sorted by solve rate, avg time, cost) · "
            f"[← One-shot leaderboard](index.html)"
        )
        columns = [
            {"id": "model", "title": "Model", "bold": True},
            {"id": "date", "title": "Date"},
            {"id": "w", "title": "W", "align": "right"},
            {"id": "win_pct", "title": "WIN%", "align": "right", "bold": True, "type": "heatmap", "higherIsBetter": True, "fmt": "pct1"},
            {"id": "hit_att", "title": "HIT/ATT", "align": "right"},
            {"id": "acc_pct", "title": "ACC%", "align": "right", "bold": True, "type": "heatmap", "higherIsBetter": True, "fmt": "pct1"},
            *common_tail_columns,
        ]

    table_spec = json.dumps(
        {
            "columns": columns,
            "data": data,
            "size": [16, "auto"],
            "sortable": True,
            "filter": True,
        },
        indent=2,
    )

    md_content = f"""---
theme: light
title: {title}
orientation: landscape
continuous: true
---

{intro}

```table
{table_spec}
```
"""

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"mviz markdown written to {output_path}")


def render_html(
    md_path: str = "docs/results.md",
    html_path: str = "docs/index.html",
):
    """Run npx mviz to render markdown to HTML, then apply CSS fixes."""
    # Pin mviz: leaving this unversioned silently broke sortable/filter when a
    # newer mviz shipped a regression. Bump deliberately when validated locally.
    MVIZ_VERSION = "1.6.7"
    result = subprocess.run(
        ["npx", "--yes", f"mviz@{MVIZ_VERSION}", md_path, "-o", html_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"mviz stderr: {result.stderr}")
        raise RuntimeError(f"mviz failed with exit code {result.returncode}")

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    override_css = """
    .dashboard { zoom: 1.5; }
    body.theme-dark .data-table a { color: #5cb8e6; }
    body.theme-dark .data-table a:visited { color: #b39ddb; }
    .data-table td:nth-child(2) { white-space: nowrap; }
"""
    html = html.replace("</style>", override_css + "</style>", 1)

    # mviz renders the intro line as plain text (no markdown/HTML), so convert
    # our cross-page markdown links to anchors here. Scoped to the two known
    # leaderboard hrefs so table JSON is never touched.
    html = re.sub(
        r"\[([^\[\]]+)\]\((classic\.html|index\.html)\)",
        r'<a href="\2">\1</a>',
        html,
    )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML rendered to {html_path}")


def main():
    pages = [
        ("oneshot", "docs/results.md", "docs/index.html"),
        ("classic", "docs/classic.md", "docs/classic.html"),
    ]

    summaries = load_summaries("results/run_summaries.csv")

    for mode, md_path, html_path in pages:
        print(f"Creating mviz results table for {mode} mode...")
        rows = filter_for_mode(summaries, mode=mode)

        if not rows:
            # No runs for this mode yet (e.g. oneshot before the backfill).
            # Leave any existing page in place rather than rendering an empty table.
            print(f"No {mode} data found matching the criteria; skipping {html_path}")
            continue

        print(f"Found {len(rows)} models meeting criteria")
        for i, row in enumerate(rows, 1):
            if mode == "oneshot":
                print(
                    f"  {i:2d}. {row['model']:15s}: {int(row['total_score']):3d} pts, "
                    f"${row['eval_cost']:5.2f} cost"
                )
            else:
                print(
                    f"  {i:2d}. {row['model']:15s}: {row['solve_rate']:5.1%} solve rate, "
                    f"${row['eval_cost']:5.2f} cost, {row['guess_accuracy']:5.1%} accuracy"
                )

        write_mviz_markdown(rows, md_path, mode=mode)
        render_html(md_path, html_path)


if __name__ == "__main__":
    main()
