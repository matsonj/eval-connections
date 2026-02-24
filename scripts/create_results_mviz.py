#!/usr/bin/env python3
"""Create results page using mviz."""

import json
import re
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


def interpolate_color(value, min_val, max_val, color_low, color_high):
    """Interpolate between two hex colors based on value position in range."""
    if max_val == min_val:
        ratio = 0.5
    else:
        ratio = (value - min_val) / (max_val - min_val)
    ratio = max(0.0, min(1.0, ratio))

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))

    r1 = hex_to_rgb(color_low)
    r2 = hex_to_rgb(color_high)
    rgb = tuple(int(r1[i] + ratio * (r2[i] - r1[i])) for i in range(3))
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


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


def build_table_data(df: pd.DataFrame) -> tuple[list[dict[str, str]], list[dict[str, float]]]:
    """Build the data rows and raw metric values for the mviz table."""
    avg_tokens = df["total_tokens"] / df["puzzles_attempted"]
    avg_cost = df["eval_cost"] / df["puzzles_attempted"]

    rows = []
    raw_values = []
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

        rows.append({
            "model": model_cell,
            "date": date,
            "w": str(int(row["puzzles_solved"])),
            "win_pct": format_percentage(row["solve_rate"]),
            "att": str(int(row["total_guesses"])),
            "hit": str(int(row["correct_guesses"])),
            "miss": str(int(row["incorrect_guesses"])),
            "err": str(int(row["invalid_responses"])),
            "acc_pct": format_percentage(row["guess_accuracy"]),
            "time": format_time(row["total_time_sec"]),
            "avg_time": format_time(row["avg_time_sec"]),
            "tok": format_tokens(row["total_tokens"]),
            "tok_per_game": format_tokens(avg_tok),
            "cost": f"${row['eval_cost']:.2f}",
            "cost_per_game": f"${avg_c:.3f}",
        })
        raw_values.append({
            "solve_rate": float(row["solve_rate"]),
            "guess_accuracy": float(row["guess_accuracy"]),
            "eval_cost": float(row["eval_cost"]),
        })

    return rows, raw_values


def write_mviz_markdown(
    df: pd.DataFrame, output_path: str = "docs/results.md"
) -> list[dict[str, float]]:
    """Write the mviz markdown file with table spec. Returns raw values for heatmap."""
    num_models = len(df)
    data, raw_values = build_table_data(df)

    columns = [
        {"id": "model", "title": "Model", "bold": True},
        {"id": "date", "title": "Date"},
        {"id": "w", "title": "W", "align": "right"},
        {"id": "win_pct", "title": "WIN PCT", "align": "right", "bold": True},
        {"id": "att", "title": "ATT", "align": "right"},
        {"id": "hit", "title": "HIT", "align": "right"},
        {"id": "miss", "title": "MISS", "align": "right"},
        {"id": "err", "title": "ERR", "align": "right"},
        {"id": "acc_pct", "title": "ACC PCT", "align": "right", "bold": True},
        {"id": "time", "title": "TIME", "align": "right"},
        {"id": "avg_time", "title": "AVG/G", "align": "right"},
        {"id": "tok", "title": "TOK", "align": "right"},
        {"id": "tok_per_game", "title": "TOK/G", "align": "right"},
        {"id": "cost", "title": "COST", "align": "right"},
        {"id": "cost_per_game", "title": "$/G", "align": "right"},
    ]

    table_spec = json.dumps({"columns": columns, "data": data}, indent=2)

    md_content = f"""---
theme: light
title: Connections Evaluation Box Score
continuous: true
---

Latest runs for {num_models} models (20 games each, sorted by solve rate, avg time, cost)

```table size=[15,auto]
{table_spec}
```
"""

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"mviz markdown written to {output_path}")
    return raw_values


def render_html(
    md_path: str = "docs/results.md",
    html_path: str = "docs/index.html",
    raw_values: list[dict[str, float]] | None = None,
):
    """Run npx mviz to render markdown to HTML, then inject heatmap colors."""
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

    # Widen dashboard and allow horizontal scroll
    override_css = """
    .dashboard { max-width: 1200px; overflow-x: auto; }
    .data-table { white-space: nowrap; }
"""
    html = html.replace("</style>", override_css + "</style>", 1)

    # Inject heatmap background colors into specific columns
    # Column indices (0-based): WIN PCT=3, ACC PCT=8, COST=13
    if raw_values:
        cost_vals = [r["eval_cost"] for r in raw_values]
        cost_min, cost_max = min(cost_vals), max(cost_vals)

        def color_for_row(row_vals):
            return {
                3: interpolate_color(row_vals["solve_rate"], 0, 1, "#f8f8f8", "#90c695"),
                8: interpolate_color(row_vals["guess_accuracy"], 0, 1, "#f8f8f8", "#90c695"),
                13: interpolate_color(row_vals["eval_cost"], cost_min, cost_max, "#90c695", "#f8f8f8"),
            }

        # Process each <tr> in <tbody>
        tbody_match = re.search(r"<tbody>(.*?)</tbody>", html, re.DOTALL)
        if tbody_match:
            tbody = tbody_match.group(1)
            rows = re.findall(r"<tr>(.*?)</tr>", tbody, re.DOTALL)
            new_rows = []
            for row_idx, row_html in enumerate(rows):
                if row_idx >= len(raw_values):
                    new_rows.append(f"<tr>{row_html}</tr>")
                    continue
                colors = color_for_row(raw_values[row_idx])
                cells = re.findall(r"<td([^>]*)>(.*?)</td>", row_html, re.DOTALL)
                new_cells = []
                for col_idx, (attrs, content) in enumerate(cells):
                    if col_idx in colors:
                        bg = colors[col_idx]
                        attrs = attrs.replace(
                            'style="', f'style="background-color:{bg};', 1
                        )
                    new_cells.append(f"<td{attrs}>{content}</td>")
                new_rows.append(f"<tr>{''.join(new_cells)}</tr>")
            new_tbody = f"<tbody>{''.join(new_rows)}</tbody>"
            html = html[: tbody_match.start()] + new_tbody + html[tbody_match.end() :]

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

    raw_values = write_mviz_markdown(df, "docs/results.md")
    render_html("docs/results.md", "docs/index.html", raw_values=raw_values)
    inject_table_link_into_readme("README.md")


if __name__ == "__main__":
    main()
