#!/usr/bin/env python3
"""Create results page using mviz."""

import json
import subprocess
import pandas as pd
import os


def load_and_filter_data(csv_file: str = "results/run_summaries.csv") -> pd.DataFrame:
    """Load CSV data and apply filtering logic."""
    df = pd.read_csv(csv_file)

    filtered_df = df[
        (df["puzzles_attempted"] == 20)
        & (df["total_cost"].notna())
    ].copy()

    # Combined eval cost
    filtered_df.loc[:, "eval_cost"] = filtered_df["total_cost"] + filtered_df[
        "total_upstream_cost"
    ].fillna(0)

    # Latest run per model
    filtered_df.loc[:, "start_timestamp"] = pd.to_datetime(
        filtered_df["start_timestamp"]
    )
    latest_runs = filtered_df.loc[
        filtered_df.groupby("model")["start_timestamp"].idxmax()
    ].copy()

    latest_runs.loc[:, "eval_cost_per_game"] = (
        latest_runs["eval_cost"] / latest_runs["puzzles_attempted"]
    )

    latest_runs = latest_runs.sort_values(
        ["solve_rate", "avg_time_sec", "eval_cost_per_game"],
        ascending=[False, True, True],
    )

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


def build_table_data(df: pd.DataFrame) -> list[dict[str, str]]:
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

        date = pd.to_datetime(row["start_timestamp"]).strftime("%Y-%m-%d")
        idx = row.name
        avg_tok = avg_tokens[idx]
        avg_c = avg_cost[idx]

        hit = int(row["correct_guesses"])
        att = int(row["total_guesses"])

        rows.append({
            "model": model_cell,
            "date": date,
            "w": str(int(row["puzzles_solved"])),
            "win_pct": round(float(row["solve_rate"]), 4),
            "hit_att": f"{hit}/{att}",
            "acc_pct": round(float(row["guess_accuracy"]), 4),
            "avg_time": format_time(row["avg_time_sec"]),
            "tok_per_game": format_tokens(avg_tok),
            "cost": round(float(row["eval_cost"]), 2),
            "cost_per_game": f"${avg_c:.3f}",
        })

    return rows


def write_mviz_markdown(
    df: pd.DataFrame, output_path: str = "docs/results.md"
):
    """Write the mviz markdown file with table spec."""
    num_models = len(df)
    data = build_table_data(df)

    columns = [
        {"id": "model", "title": "Model", "bold": True},
        {"id": "date", "title": "Date"},
        {"id": "w", "title": "W", "align": "right"},
        {"id": "win_pct", "title": "WIN%", "align": "right", "bold": True, "type": "heatmap", "higherIsBetter": True, "fmt": "pct1"},
        {"id": "hit_att", "title": "HIT/ATT", "align": "right"},
        {"id": "acc_pct", "title": "ACC%", "align": "right", "bold": True, "type": "heatmap", "higherIsBetter": True, "fmt": "pct1"},
        {"id": "avg_time", "title": "AVG/G", "align": "right"},
        {"id": "tok_per_game", "title": "TOK/G", "align": "right"},
        {"id": "cost", "title": "COST", "align": "right", "type": "heatmap", "higherIsBetter": False, "fmt": "currency_auto"},
        {"id": "cost_per_game", "title": "$/G", "align": "right"},
    ]

    table_spec = json.dumps({"columns": columns, "data": data, "size": [16, "auto"]}, indent=2)

    md_content = f"""---
theme: light
title: Connections Evaluation Box Score
continuous: true
---

Latest runs for {num_models} models (20 games each, sorted by solve rate, avg time, cost)

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
    result = subprocess.run(
        ["npx", "--yes", "mviz", md_path, "-o", html_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"mviz stderr: {result.stderr}")
        raise RuntimeError(f"mviz failed with exit code {result.returncode}")

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    override_css = """
    body.theme-dark .data-table a { color: #5cb8e6; }
    body.theme-dark .data-table a:visited { color: #b39ddb; }
"""
    html = html.replace("</style>", override_css + "</style>", 1)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML rendered to {html_path}")


def inject_table_link_into_readme(readme_path: str = "README.md"):
    """Inject a link to the HTML table into README.md."""
    with open(readme_path, "r", encoding="utf-8") as f:
        readme_content = f.read()

    results_section = """## Latest Results

[📊 View Interactive Results Table](https://matsonj.github.io/eval-connections/) - Sports-style box score showing latest model performance

*Table includes solve rates, costs, token usage, and timing metrics formatted like sports statistics.*

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
    print("Creating mviz results table...")

    df = load_and_filter_data("results/run_summaries.csv")

    if df.empty:
        print("No data found matching the criteria")
        return

    print(f"Found {len(df)} models meeting criteria")
    for i, (_, row) in enumerate(df.iterrows(), 1):
        print(
            f"  {i:2d}. {row['model']:15s}: {row['solve_rate']:5.1%} solve rate, "
            f"${row['eval_cost']:5.2f} cost, {row['guess_accuracy']:5.1%} accuracy"
        )

    write_mviz_markdown(df, "docs/results.md")
    render_html("docs/results.md", "docs/index.html")
    inject_table_link_into_readme("README.md")


if __name__ == "__main__":
    main()
