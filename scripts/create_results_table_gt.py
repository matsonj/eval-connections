#!/usr/bin/env python3
"""Create a beautiful table using Great Tables."""

import pandas as pd
from great_tables import GT, md, html
from great_tables import style, loc
import numpy as np
import os


def load_and_filter_data(csv_file: str = "results/run_summaries.csv") -> pd.DataFrame:
    """Load CSV data and apply the filtering logic from the SQL query."""
    df = pd.read_csv(csv_file)
    
    # Apply filters similar to the SQL query
    filtered_df = df[
        (df['puzzles_attempted'] >= 11) &
        (df['total_guesses'] > 40) &
        (df['total_cost'].notna())
    ].copy()
    
    # Calculate combined eval cost
    filtered_df['eval_cost'] = filtered_df['total_cost'] + filtered_df['total_upstream_cost'].fillna(0)
    
    # Get the most recent run for each model (equivalent to ROW_NUMBER() OVER...)
    filtered_df['start_timestamp'] = pd.to_datetime(filtered_df['start_timestamp'])
    latest_runs = filtered_df.loc[
        filtered_df.groupby('model')['start_timestamp'].idxmax()
    ]
    
    # Calculate eval cost per game for sorting
    latest_runs['eval_cost_per_game'] = latest_runs['eval_cost'] / latest_runs['puzzles_attempted']
    
    # Sort by solve_rate desc, guess_accuracy desc, avg_time_sec asc, eval_cost_per_game asc
    latest_runs = latest_runs.sort_values([
        'solve_rate', 'guess_accuracy', 'avg_time_sec', 'eval_cost_per_game'
    ], ascending=[False, False, True, True])
    
    return latest_runs


def interpolate_color(value, min_val, max_val, color1, color2):
    """Interpolate between two hex colors based on value."""
    # Convert hex to RGB
    def hex_to_rgb(hex_color):
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    
    # Convert RGB to hex
    def rgb_to_hex(rgb):
        return '#' + ''.join(f'{int(c):02x}' for c in rgb)
    
    # Normalize value to 0-1 range
    if max_val == min_val:
        ratio = 0
    else:
        ratio = (value - min_val) / (max_val - min_val)
    ratio = max(0, min(1, ratio))  # Clamp to 0-1
    
    # Interpolate RGB values
    rgb1 = hex_to_rgb(color1)
    rgb2 = hex_to_rgb(color2)
    
    rgb_result = tuple(rgb1[i] + ratio * (rgb2[i] - rgb1[i]) for i in range(3))
    return rgb_to_hex(rgb_result)


def prepare_table_data(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare data for the Great Tables display."""
    
    def format_percentage(rate):
        """Format percentage in sports box score style."""
        if rate >= 1.0:
            return "1.000"
        else:
            return f".{int(rate*1000):03d}"
    
    def format_time(seconds):
        """Format time as XmYs."""
        # Handle NaN, None, or invalid values
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
        else:
            return f"{secs}s"
    
    def format_tokens(tokens):
        """Format tokens as X.Xk."""
        return f"{tokens/1000:.1f}k"
    
    # Calculate derived values
    avg_tokens = df['total_tokens'] / df['puzzles_attempted']
    avg_cost = df['eval_cost'] / df['puzzles_attempted']
    
    # Create display dataframe with formatted values (sports box score style)
    table_df = pd.DataFrame({
        'Model': df['model'].values,
        'Version': df['version'].values,
        'Date': [pd.to_datetime(ts).strftime('%Y-%m-%d') for ts in df['start_timestamp'].values],
        'Puzzles': df['puzzles_attempted'].astype(int).values,
        'Solved': df['puzzles_solved'].astype(int).values,
        'Pct Solved': [format_percentage(rate) for rate in df['solve_rate'].values],
        'Guesses': df['total_guesses'].astype(int).values,
        'Correct': df['correct_guesses'].astype(int).values,
        'Incorrect': df['incorrect_guesses'].astype(int).values,
        'Invalid': df['invalid_responses'].astype(int).values,
        'Pct Correct': [format_percentage(acc) for acc in df['guess_accuracy'].values],
        'Run Time': [format_time(time) for time in df['total_time_sec'].values],
        'Avg Time': [format_time(time) for time in df['avg_time_sec'].values],
        'Tokens': [format_tokens(tokens) for tokens in df['total_tokens'].values],
        'Avg Tokens': [format_tokens(tokens) for tokens in avg_tokens.values],
        'Total Cost': [f"${cost:.2f}" for cost in df['eval_cost'].values],
        'Avg Cost': [f"${cost:.3f}" for cost in avg_cost.values],
    })

    # Turn Model cell into hyperlink to per-run logs page
    # Only for versions >= 2.0.1
    if 'run_id' in df.columns and 'version' in df.columns:
        def version_tuple(v: str):
            try:
                parts = [int(x) for x in str(v).split('.')[:3]]
                while len(parts) < 3:
                    parts.append(0)
                return tuple(parts)
            except Exception:
                return (0, 0, 0)
        links = []
        for model_name, run_id, ver in zip(df['model'].values, df['run_id'].values, df['version'].values):
            if version_tuple(ver) >= (2, 0, 1):
                href = f"logs/{run_id}.html"
                # Use Markdown link; Great Tables renders md() content
                links.append(md(f'[{model_name}]({href})'))
            else:
                links.append(model_name)
        table_df['Model'] = links
    
    # Add background colors based on key metrics
    solve_rate_values = df['solve_rate'].values
    cost_values = df['eval_cost'].values
    accuracy_values = df['guess_accuracy'].values
    
    # Calculate background colors
    table_df['solve_bg'] = [
        interpolate_color(rate, 0, 1, '#f8f8f8', '#90c695') 
        for rate in solve_rate_values
    ]
    table_df['cost_bg'] = [
        interpolate_color(cost, cost_values.min(), cost_values.max(), '#7fb3d3', '#f8f8f8') 
        for cost in cost_values
    ]
    table_df['accuracy_bg'] = [
        interpolate_color(acc, 0, 1, '#f8f8f8', '#ffaaaa') 
        for acc in accuracy_values
    ]
    
    # Reset index to ensure clean integer index
    table_df = table_df.reset_index(drop=True)
    
    return table_df


def create_great_table(df: pd.DataFrame, save_path: str = "results/results_table_gt.png"):
    """Create a beautiful table using Great Tables."""
    
    # Prepare the data
    table_df = prepare_table_data(df)
    
    # Create the Great Table
    gt_table = (
        GT(table_df)
        .tab_header(
            title="Connections Evaluation Box Score",
            subtitle=f"Latest runs for {len(df)} models (>=11 puzzles, >40 guesses, sorted by solve rate, accuracy, avg time, cost per game)"
        )
        .cols_hide(columns=["solve_bg", "cost_bg", "accuracy_bg"])  # Hide background color columns
        .cols_label(
            Model="Model",
            Version="Ver",
            Date="Date",
            Puzzles="GP",  # Games Played
            Solved="W",    # Wins
            **{"Pct Solved": "WIN PCT"},
            Guesses="ATT", # Attempts
            Correct="HIT",
            Incorrect="MISS",
            Invalid="ERR",
            **{"Pct Correct": "ACC PCT"},
            **{"Run Time": "TIME"},
            **{"Avg Time": "AVG/G"},
            Tokens="TOK",
            **{"Avg Tokens": "TOK/G"},
            **{"Total Cost": "COST"},
            **{"Avg Cost": "$/G"}
        )
    )
    
    # Apply background colors manually
    for i, row in table_df.iterrows():
        # Apply background color to Pct Solved column
        gt_table = gt_table.tab_style(
            style=style.fill(color=row['solve_bg']),
            locations=loc.body(columns=["Pct Solved"], rows=[i])
        )
        # Apply background color to Total Cost column  
        gt_table = gt_table.tab_style(
            style=style.fill(color=row['cost_bg']),
            locations=loc.body(columns=["Total Cost"], rows=[i])
        )
        # Apply background color to Pct Correct column
        gt_table = gt_table.tab_style(
            style=style.fill(color=row['accuracy_bg']),
            locations=loc.body(columns=["Pct Correct"], rows=[i])
        )
    
    # Continue with other styling
    gt_table = (
        gt_table
        .tab_style(
            style=style.text(
                font="Arial",
                size="30px",
                weight="bold"
            ),
            locations=loc.title()
        )
        .tab_style(
            style=style.text(
                font="Arial",
                size="21px",
                color="#666666"
            ),
            locations=loc.subtitle()
        )
        .tab_style(
            style=style.text(
                font="Arial",
                size="18px",
                weight="bold",
                color="#000000"
            ),
            locations=loc.column_labels()
        )
        .tab_style(
            style=style.text(
                font="Arial",
                size="16px",
                color="#000000"
            ),
            locations=loc.body()
        )
        # Monospace font for percentages and stats
        .tab_style(
            style=style.text(
                font="Courier New",
                size="16px",
                color="#000000",
                weight="bold"
            ),
            locations=loc.body(columns=["Pct Solved", "Pct Correct"])
        )
        # Right-align numeric columns
        .tab_style(
            style=style.text(align="right"),
            locations=loc.body(columns=["GP", "W", "ATT", "HIT", "MISS", "ERR", "TIME", "AVG/G", "TOK", "TOK/G", "COST", "$/G"])
        )
        # Center-align percentage columns
        .tab_style(
            style=style.text(align="center"),
            locations=loc.body(columns=["WIN PCT", "ACC PCT"])
        )
        .tab_style(
            style=style.borders(
                sides=["bottom"],
                color="#dddddd",
                weight="1px"
            ),
            locations=loc.body()
        )
        .tab_style(
            style=style.borders(
                sides=["top", "bottom"],
                color="#000000",
                weight="2px"
            ),
            locations=loc.column_labels()
        )
        .tab_options(
            table_font_size="16px",
            heading_align="center",
            column_labels_border_bottom_width="2px",
            column_labels_border_bottom_color="#000000",
            table_border_top_style="solid",
            table_border_bottom_style="solid",
            table_border_top_width="1px",
            table_border_bottom_width="1px"
        )
    )
    
    # Save as HTML to docs directory for GitHub Pages
    docs_html_path = "docs/index.html"
    
    # Use the show() method to generate HTML and save it
    html_content = gt_table._render_as_html()
    
    # Extract the actual ID from the generated HTML
    import re
    id_match = re.search(r'<div id="([^"]+)"', html_content)
    if id_match:
        actual_id = id_match.group(1)
    else:
        actual_id = "gt_table"  # fallback
    
    # Add responsive CSS to the HTML content with the correct ID
    responsive_css = f"""
#{actual_id} {{
  width: 100%;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  box-sizing: border-box;
  padding: 10px 0;
}}

/* Make the table fluid and readable on small viewports */
#{actual_id} .gt_table {{
  width: 100% !important;
  max-width: 100%;
  border-collapse: collapse;
  table-layout: auto; /* let columns size themselves */
}}

/* Override hardcoded font sizes from inline styles */
#{actual_id} .gt_table,
#{actual_id} .gt_table th,
#{actual_id} .gt_table td {{
  font-size: clamp(12px, 1.2vw + 10px, 16px) !important;
  line-height: 1.25;
  white-space: nowrap; /* reduce tall rows; allow scroll instead of wrap */
}}

/* Allow wrapping for long titles/subtitles only */
#{actual_id} .gt_title,
#{actual_id} .gt_subtitle {{
  white-space: normal;
  word-break: break-word;
}}

/* Sticky header for usability on small screens */
#{actual_id} .gt_col_headings,
#{actual_id} .gt_heading {{
  position: sticky;
  top: 0;
  z-index: 3;
  background: #fff;
}}

/* Sticky first column (Model) so you keep context while scrolling horizontally) */
#{actual_id} table tr > *:first-child {{
  position: sticky;
  left: 0;
  z-index: 2; /* sit above body cells but under header row */
  background: #fff;
  box-shadow: 1px 0 0 #e5e7eb; /* subtle divider when scrolled */
}}

/* Improve touch targets and spacing slightly on narrow screens */
@media (max-width: 768px) {{
  #{actual_id} .gt_table th,
  #{actual_id} .gt_table td {{
    padding: 6px 8px !important;
  }}
}}

/* Progressive column pruning by viewport width (keeps the essentials) */
/* Column order (1-indexed):
   1 Model, 2 Version, 3 Date, 4 GP, 5 W, 6 WIN PCT, 7 ATT, 8 HIT, 9 MISS, 10 ERR,
   11 ACC PCT, 12 TIME, 13 AVG/G, 14 TOK, 15 TOK/G, 16 COST, 17 $/G
*/

/* <= 1200px: hide super-verbose metrics first */
@media (max-width: 1200px) {{
  #{actual_id} th:nth-child(14), #{actual_id} td:nth-child(14) {{ display: none; }} /* TOK */
  #{actual_id} th:nth-child(15), #{actual_id} td:nth-child(15) {{ display: none; }} /* TOK/G */
  #{actual_id} th:nth-child(17), #{actual_id} td:nth-child(17) {{ display: none; }} /* $/G */
  #{actual_id} th:nth-child(10), #{actual_id} td:nth-child(10) {{ display: none; }} /* ERR */
}}

/* <= 992px: trim more second-order diagnostics */
@media (max-width: 992px) {{
  #{actual_id} th:nth-child(8),  #{actual_id} td:nth-child(8)  {{ display: none; }} /* HIT */
  #{actual_id} th:nth-child(9),  #{actual_id} td:nth-child(9)  {{ display: none; }} /* MISS */
  #{actual_id} th:nth-child(13), #{actual_id} td:nth-child(13) {{ display: none; }} /* AVG/G */
}}

/* <= 768px: keep headline stats */
@media (max-width: 768px) {{
  #{actual_id} th:nth-child(12), #{actual_id} td:nth-child(12) {{ display: none; }} /* TIME */
  #{actual_id} th:nth-child(7),  #{actual_id} td:nth-child(7)  {{ display: none; }} /* ATT */
}}

/* <= 600px: minimal set for mobile: Model, Version, GP, W, WIN PCT, COST */
@media (max-width: 600px) {{
  #{actual_id} th:nth-child(3),  #{actual_id} td:nth-child(3)  {{ display: none; }} /* Date */
  #{actual_id} th:nth-child(11), #{actual_id} td:nth-child(11) {{ display: none; }} /* ACC PCT */
  /* If you prefer ACC PCT over COST on tiny screens, swap which of 11 or 16 is hidden */
}}

/* <= 480px: ultra-compact — Model, Version, GP, W, WIN PCT, COST */
@media (max-width: 480px) {{
  /* hide ACC PCT so we keep COST as business-facing metric */
  #{actual_id} th:nth-child(11), #{actual_id} td:nth-child(11) {{ display: none; }} /* ACC PCT */
}}

/* Optional: softer borders on mobile */
@media (max-width: 768px) {{
  #{actual_id} .gt_row {{ border-top-color: #eee; }}
  #{actual_id} .gt_col_headings {{ border-bottom-color: #ddd; }}
}}
"""
    
    # Convert any markdown placeholders that leaked as object reprs (e.g., Md(text='[name](href)'))
    try:
        import re as _re_md
        _pat_md = _re_md.compile(r"Md\(text='\[([^\]]+)\]\(([^\)]+)\)'\)")
        html_content = _pat_md.sub(r'<a href="\2">\1</a>', html_content)
        _pat_html = _re_md.compile(r"Html\(text='(.*?)'\)")
        html_content = _pat_html.sub(r'\1', html_content)
    except Exception:
        pass

    # Insert the responsive CSS into the HTML
    if '<style>' in html_content:
        # Insert after the existing style block
        style_end = html_content.find('</style>')
        html_content = html_content[:style_end] + responsive_css + html_content[style_end:]
    else:
        # Create a new style block if none exists
        head_end = html_content.find('</head>')
        if head_end != -1:
            html_content = html_content[:head_end] + f'<style>{responsive_css}</style>' + html_content[head_end:]
        else:
            # If no head tag, insert at the beginning
            html_content = f'<style>{responsive_css}</style>' + html_content
    
    # Save to docs directory (for both local viewing and GitHub Pages)
    with open(docs_html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"Great Tables HTML saved to {docs_html_path}")
    print("View locally: open docs/index.html in browser")
    
    # Note: PNG export requires webdriver setup which can be complex
    print("PNG export skipped - HTML version provides the same visual output")
    
    return gt_table


def inject_table_link_into_readme(readme_path: str = "README.md"):
    """Inject a link to the HTML table into README.md instead of the full HTML."""
    
    # Read current README
    with open(readme_path, 'r', encoding='utf-8') as f:
        readme_content = f.read()
    
    # Define the results section content
    results_section = """## Latest Results

[📊 View Interactive Results Table](https://matsonj.github.io/eval-connections/) - Sports-style box score showing latest model performance

*Table includes solve rates, costs, token usage, and timing metrics formatted like sports statistics.*

"""
    
    # Check if we already have a results section
    if "## Latest Results" in readme_content:
        # Replace existing results section
        start_idx = readme_content.find("## Latest Results")
        end_idx = readme_content.find("## License", start_idx)
        if end_idx != -1:
            # Replace the section
            new_readme = (
                readme_content[:start_idx] + 
                results_section +
                readme_content[end_idx:]
            )
        else:
            print("❌ Could not find License section to place results before")
            return False
    else:
        # Add new results section before License
        license_idx = readme_content.find("## License")
        if license_idx != -1:
            new_readme = (
                readme_content[:license_idx] +
                results_section +
                readme_content[license_idx:]
            )
        else:
            # Append at the end if no License section found
            new_readme = readme_content + "\n\n" + results_section
    
    # Write updated README
    with open(readme_path, 'w', encoding='utf-8') as f:
        f.write(new_readme)
    
    print(f"✅ HTML table link injected into {readme_path}")
    return True


def main():
    """Main function to create the results table."""
    print("📊 Creating Great Tables results table...")
    
    # Load and filter data
    df = load_and_filter_data("results/run_summaries.csv")
    
    if df.empty:
        print("❌ No data found matching the criteria")
        return
    
    print(f"Found {len(df)} models meeting criteria")
    print("\nAll performers:")
    for i, (_, row) in enumerate(df.iterrows(), 1):
        print(f"  {i:2d}. {row['model']:15s}: {row['solve_rate']:5.1%} solve rate, ${row['eval_cost']:5.2f} cost, {row['guess_accuracy']:5.1%} accuracy")
    
    # Create the table
    gt_table = create_great_table(df, "results/results_table_gt.png")
    
    # Also inject link into README.md
    inject_table_link_into_readme("README.md")
    
    return gt_table


if __name__ == "__main__":
    main()
