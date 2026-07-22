#!/usr/bin/env python3
"""Create results pages using mviz.

Generates two leaderboards from results/run_summaries.csv:
- One-shot (primary): docs/results.md -> docs/index.html
- Classic (multi-turn): docs/classic.md -> docs/classic.html
"""

import json
import subprocess
import pandas as pd
import os


def load_and_filter_data(
    csv_file: str = "results/run_summaries.csv", mode: str = "oneshot"
) -> pd.DataFrame:
    """Load CSV data and apply filtering logic for one eval mode."""
    df = pd.read_csv(csv_file)

    # Backfill columns added in later versions so older CSVs still render.
    # Missing values stay NaN so downstream can show "—" for "not measured".
    for col in (
        "avg_inference_sec",
        "total_inference_sec",
        "total_backoff_sec",
        "total_score",
        "total_trap_bonus",
        "max_score",
        "avg_score",
        "trap_scored",
    ):
        if col not in df.columns:
            df[col] = pd.NA

    # Pre-4.0 CSVs have no mode column; every run back then was classic.
    if "mode" not in df.columns:
        df = df.assign(mode="classic")
    df = df.assign(mode=df["mode"].fillna("classic"))

    filtered_df = df[
        (df["puzzles_attempted"] == 20)
        & (df["total_cost"].notna())
        & (df["mode"] == mode)
    ].copy()

    # Legacy pre-trap one-shot smoke runs (no _TRAP_ in result strings) used a
    # different scoring scale — exclude them so the board only compares
    # trap-scored runs.
    if mode == "oneshot":
        filtered_df = filtered_df[filtered_df["trap_scored"].fillna(0) == 1]

    if filtered_df.empty:
        return filtered_df

    # Combined eval cost
    filtered_df.loc[:, "eval_cost"] = filtered_df["total_cost"] + filtered_df[
        "total_upstream_cost"
    ].fillna(0)

    # Latest run per model. MotherDuck emits offsets like "+00:00" for some rows
    # and plain ISO for others; without utc=True the resulting Series mixes
    # tz-aware and tz-naive Timestamps, and idxmax() can't compare them.
    filtered_df.loc[:, "start_timestamp"] = pd.to_datetime(
        filtered_df["start_timestamp"], format="ISO8601", utc=True
    )
    latest_runs = filtered_df.loc[
        filtered_df.groupby("model")["start_timestamp"].idxmax()
    ].copy()

    latest_runs.loc[:, "eval_cost_per_game"] = (
        latest_runs["eval_cost"] / latest_runs["puzzles_attempted"]
    )

    # Sort by inference time (fair across upstream-throttled models) with wall
    # time as a tiebreaker for historical runs that lack backoff data.
    sort_time = latest_runs["avg_inference_sec"].where(
        latest_runs["avg_inference_sec"].notna(), latest_runs["avg_time_sec"]
    )
    if mode == "oneshot":
        # Headline metric is total score (max 5 per puzzle = 100 on canonical).
        latest_runs.loc[:, "total_score"] = latest_runs["total_score"].fillna(0)
        sort_keys = ["total_score", "_sort_time", "eval_cost_per_game"]
    else:
        sort_keys = ["solve_rate", "_sort_time", "eval_cost_per_game"]

    latest_runs = latest_runs.assign(_sort_time=sort_time).sort_values(
        sort_keys,
        ascending=[False, True, True],
    ).drop(columns=["_sort_time"])

    return latest_runs



def format_percentage(rate):
    if rate >= 1.0:
        return "1.000"
    return f".{int(rate * 1000):03d}"


def format_time(seconds):
    if pd.isna(seconds) or seconds is None:
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


def build_table_data(df: pd.DataFrame, mode: str = "oneshot") -> list[dict[str, str]]:
    """Build the data rows for the mviz table."""
    avg_tokens = df["total_tokens"] / df["puzzles_attempted"]
    avg_cost = df["eval_cost"] / df["puzzles_attempted"]

    rows = []
    for _, row in df.iterrows():
        run_id = row.get("run_id", "")
        model_name = row["model"]
        if run_id:
            model_cell = f'<a href="logs/{run_id}.html">{model_name}</a>'
        else:
            model_cell = model_name

        date = row["start_timestamp"].strftime("%Y-%m-%d")
        idx = row.name
        avg_tok = avg_tokens[idx]
        avg_c = avg_cost[idx]

        # Inference time when we measured it (wall minus retry backoff),
        # falling back to wall time for historical runs we didn't instrument.
        avg_inference = row.get("avg_inference_sec")
        if avg_inference is None or pd.isna(avg_inference):
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
                if max_score is not None and not pd.isna(max_score)
                else 5 * int(row["puzzles_attempted"])
            )
            trap_bonus = row.get("total_trap_bonus")
            trap_cell = "—" if trap_bonus is None or pd.isna(trap_bonus) else str(int(trap_bonus))
            rows.append({
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
            rows.append({
                "model": model_cell,
                "date": date,
                "w": str(int(row["puzzles_solved"])),
                "win_pct": round(float(row["solve_rate"]), 4),
                "hit_att": f"{hit}/{att}",
                "acc_pct": round(float(row["guess_accuracy"]), 4),
                **common_tail,
            })

    return rows


def write_mviz_markdown(
    df: pd.DataFrame, output_path: str = "docs/results.md", mode: str = "oneshot"
):
    """Write the mviz markdown file with table spec."""
    num_models = len(df)
    data = build_table_data(df, mode)

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

    # Scatter of points vs cost-efficiency above the one-shot table — labeled
    # per model so the frontier reads at a glance. X is inverted from raw cost
    # to "games per $10" so right = cheaper (fugu-ultra ~32, flash-lite ~1700).
    chart_block = ""
    if mode == "oneshot":
        chart_data = [
            {
                "model": row["model"],
                "games per $10": round(
                    float(row["puzzles_attempted"]) * 10 / float(row["eval_cost"])
                ),
                "pts": int(row["total_score"]),
            }
            for _, row in df.iterrows()
            if float(row["eval_cost"]) > 0
        ]
        scatter_spec = json.dumps(
            {
                "type": "scatter",
                "title": "Points vs Efficiency (games per $10)",
                "x": "games per $10",
                "y": "pts",
                "label": "model",
                "showLabels": True,
                "data": chart_data,
            },
            indent=2,
        )
        # Horizontal bars, lowest score at the bottom of the category axis so
        # the leader renders on top.
        bar_spec = json.dumps(
            {
                "type": "bar",
                "title": "Points",
                "x": "model",
                "y": "pts",
                "horizontal": True,
                "data": [
                    {"model": row["model"], "pts": int(row["total_score"])}
                    for _, row in df.iloc[::-1].iterrows()
                ],
            },
            indent=2,
        )
        # No blank line between the two blocks — they share the row.
        chart_block = f"""```scatter size=[8,10]
{scatter_spec}
```
```bar size=[8,10]
{bar_spec}
```

"""

    md_content = f"""---
theme: light
title: {title}
orientation: landscape
continuous: true
---

{intro}

{chart_block}```table
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
    import re
    html = re.sub(
        r"\[([^\[\]]+)\]\((classic\.html|index\.html)\)",
        r'<a href="\2">\1</a>',
        html,
    )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML rendered to {html_path}")


def inject_table_link_into_readme(readme_path: str = "README.md"):
    """Inject a link to the HTML table into README.md."""
    with open(readme_path, "r", encoding="utf-8") as f:
        readme_content = f.read()

    results_section = """## Latest Results

[📊 View Interactive Results Table](https://matsonj.github.io/eval-connections/) - Sports-style box score showing latest one-shot model performance ([classic leaderboard](https://matsonj.github.io/eval-connections/classic.html))

*Table includes points scored, costs, token usage, and timing metrics formatted like sports statistics.*

"""

    if "## Latest Results" in readme_content:
        start_idx = readme_content.find("## Latest Results")
        end_idx = readme_content.find("## License", start_idx)
        if end_idx != -1:
            new_readme = (
                readme_content[:start_idx] + results_section + readme_content[end_idx:]
            )
        else:
            print("Could not find License section to place results before")
            return False
    else:
        license_idx = readme_content.find("## License")
        if license_idx != -1:
            new_readme = (
                readme_content[:license_idx]
                + results_section
                + readme_content[license_idx:]
            )
        else:
            new_readme = readme_content + "\n\n" + results_section

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(new_readme)

    print(f"HTML table link injected into {readme_path}")
    return True


def main():
    pages = [
        ("oneshot", "docs/results.md", "docs/index.html"),
        ("classic", "docs/classic.md", "docs/classic.html"),
    ]

    for mode, md_path, html_path in pages:
        print(f"Creating mviz results table for {mode} mode...")
        df = load_and_filter_data("results/run_summaries.csv", mode=mode)

        if df.empty:
            # No runs for this mode yet (e.g. oneshot before the backfill).
            # Leave any existing page in place rather than rendering an empty table.
            print(f"No {mode} data found matching the criteria; skipping {html_path}")
            continue

        print(f"Found {len(df)} models meeting criteria")
        for i, (_, row) in enumerate(df.iterrows(), 1):
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

        write_mviz_markdown(df, md_path, mode=mode)
        render_html(md_path, html_path)

    inject_table_link_into_readme("README.md")


if __name__ == "__main__":
    main()
