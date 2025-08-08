#!/usr/bin/env python3
"""Create a beautiful table using Great Tables."""

import pandas as pd
from great_tables import GT, md, html
from great_tables import style, loc
import numpy as np


def load_and_filter_data(csv_file: str = "results/run_summaries.csv") -> pd.DataFrame:
    """Load CSV data and apply the filtering logic from the SQL query."""
    df = pd.read_csv(csv_file)
    
    # Apply filters similar to the SQL query
    filtered_df = df[
        (df['puzzles_attempted'] == 11) &
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
    
    # Sort by solve_rate desc, eval_cost, guess_accuracy desc
    latest_runs = latest_runs.sort_values([
        'solve_rate', 'eval_cost', 'guess_accuracy'
    ], ascending=[False, True, False])
    
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
    
    # Create display dataframe with formatted values
    table_df = pd.DataFrame({
        'Model': df['model'].values,
        'Solve Rate': [f"{rate:.1%}" for rate in df['solve_rate'].values],
        'Cost': [f"${cost:.3f}" for cost in df['eval_cost'].values],
        'Command': [f"--model {model}" for model in df['model'].values],
        'Guess Accuracy': [f"{acc:.1%}" for acc in df['guess_accuracy'].values],
    })
    
    # Add background colors based on values
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
            title="Connections Evaluation Results - All Models",
            subtitle=f"Showing all {len(df)} models (11 puzzles, >40 guesses, sorted by solve rate)"
        )
        .cols_hide(columns=["solve_bg", "cost_bg", "accuracy_bg"])  # Hide background color columns
        .cols_label(
            Model="Model",
            **{"Solve Rate": "Puzzles Solved"},
            Cost="Total Eval Cost",
            Command="Command", 
            **{"Guess Accuracy": "Correct Answers"}
        )
    )
    
    # Apply background colors manually
    for i, row in table_df.iterrows():
        # Apply background color to Solve Rate column
        gt_table = gt_table.tab_style(
            style=style.fill(color=row['solve_bg']),
            locations=loc.body(columns=["Solve Rate"], rows=[i])
        )
        # Apply background color to Cost column  
        gt_table = gt_table.tab_style(
            style=style.fill(color=row['cost_bg']),
            locations=loc.body(columns=["Cost"], rows=[i])
        )
        # Apply background color to Guess Accuracy column
        gt_table = gt_table.tab_style(
            style=style.fill(color=row['accuracy_bg']),
            locations=loc.body(columns=["Guess Accuracy"], rows=[i])
        )
    
    # Continue with other styling
    gt_table = (
        gt_table
        .tab_style(
            style=style.text(
                font="Helvetica",
                size="14px",
                weight="600"
            ),
            locations=loc.title()
        )
        .tab_style(
            style=style.text(
                font="Helvetica",
                size="12px",
                color="#666666"
            ),
            locations=loc.subtitle()
        )
        .tab_style(
            style=style.text(
                font="Helvetica",
                size="11px",
                weight="bold",
                color="#333333"
            ),
            locations=loc.column_labels()
        )
        .tab_style(
            style=style.text(
                font="Helvetica",
                size="10px",
                color="#333333"
            ),
            locations=loc.body()
        )
        .tab_style(
            style=style.text(
                font="Monaco",
                size="9px",
                color="#666666"
            ),
            locations=loc.body(columns=["Command"])
        )
        .tab_style(
            style=style.borders(
                sides=["bottom"],
                color="#cccccc",
                weight="1px"
            ),
            locations=loc.body()
        )
        .tab_style(
            style=style.borders(
                sides=["top", "bottom"],
                color="#666666",
                weight="2px"
            ),
            locations=loc.column_labels()
        )
        .tab_options(
            table_font_size="10px",
            heading_align="center",
            column_labels_border_bottom_width="2px",
            column_labels_border_bottom_color="#666666",
            table_border_top_style="none",
            table_border_bottom_style="none"
        )
    )
    
    # Save as HTML first
    html_path = save_path.replace('.png', '.html')
    
    # Use the show() method to generate HTML and save it
    html_content = gt_table._render_as_html()
    
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"Great Tables HTML saved to {html_path}")
    print("Open the HTML file in a browser to view the table!")
    
    # Note: PNG export requires webdriver setup which can be complex
    print("PNG export skipped - HTML version provides the same visual output")
    
    return gt_table


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
    
    return gt_table


if __name__ == "__main__":
    main()
