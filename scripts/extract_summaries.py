#!/usr/bin/env python3
"""Extract run summaries from log files and create a CSV report."""

import json
import csv
import glob
import os
from pathlib import Path
from typing import List, Dict, Any


def extract_run_summaries(logs_dir: str = "logs") -> List[Dict[str, Any]]:
    """Extract all run summary records from JSONL log files."""
    summaries = []
    
    # Find all .jsonl files in the logs directory
    log_files = glob.glob(os.path.join(logs_dir, "*.jsonl"))
    
    print(f"Found {len(log_files)} log files in {logs_dir}/")
    
    for log_file in log_files:
        print(f"Processing {log_file}...")
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                        
                    try:
                        record = json.loads(line)
                        
                        # Look for run summary records
                        if record.get("message") == "Run summary":
                            # Add the source file for reference
                            record["log_file"] = os.path.basename(log_file)
                            
                            # Add version with fallback to 1.0.0 for older logs
                            if "version" not in record:
                                record["version"] = "1.0.0"
                            
                            summaries.append(record)
                            print(f"  Found run summary: {record.get('run_id', 'unknown')} (v{record['version']})")
                            
                    except json.JSONDecodeError as e:
                        print(f"  Warning: Invalid JSON on line {line_num}: {e}")
                        continue
                        
        except Exception as e:
            print(f"Error reading {log_file}: {e}")
            continue
    
    print(f"\nExtracted {len(summaries)} run summaries total")
    return summaries


def summaries_to_csv(summaries: List[Dict[str, Any]], output_file: str = "results/run_summaries.csv"):
    """Convert run summaries to CSV format."""
    if not summaries:
        print("No summaries to write")
        return
    
    # Create output directory if it doesn't exist
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Define the columns we want in the CSV (in order)
    columns = [
        "log_file",
        "run_id", 
        "model",
        "version",
        "timestamp",
        "seed",
        "puzzles_attempted",
        "puzzles_solved", 
        "solve_rate",
        "total_guesses",
        "correct_guesses",
        "incorrect_guesses", 
        "invalid_responses",
        "guess_accuracy",
        "avg_time_sec",
        "total_time_sec",
        "total_tokens",
        "total_prompt_tokens",
        "total_completion_tokens",
        "token_count_method",
        "total_cost",
        "total_upstream_cost",
        "start_timestamp",
        "end_timestamp"
    ]
    
    # Calculate derived metrics
    for summary in summaries:
        # Calculate solve rate
        if summary.get("puzzles_attempted", 0) > 0:
            summary["solve_rate"] = summary.get("puzzles_solved", 0) / summary["puzzles_attempted"]
        else:
            summary["solve_rate"] = 0
            
        # Calculate guess accuracy  
        if summary.get("total_guesses", 0) > 0:
            summary["guess_accuracy"] = summary.get("correct_guesses", 0) / summary["total_guesses"]
        else:
            summary["guess_accuracy"] = 0
    
    print(f"Writing {len(summaries)} summaries to {output_file}")
    
    with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        
        for summary in summaries:
            # Fill in missing columns with None/empty values
            row = {col: summary.get(col, None) for col in columns}
            writer.writerow(row)
    
    print(f"Successfully wrote {output_file}")


def main():
    """Main function to extract summaries and create CSV."""
    print("🔍 Extracting run summaries from log files...")
    
    # Check if logs directory exists
    if not os.path.exists("logs"):
        print("❌ No 'logs' directory found. Please run from the project root.")
        return
    
    # Extract summaries from all log files
    summaries = extract_run_summaries("logs")
    
    if not summaries:
        print("❌ No run summaries found in log files")
        return
    
    # Convert to CSV
    summaries_to_csv(summaries, "results/run_summaries.csv")
    
    # Print some basic stats
    print(f"\n📊 Summary Statistics:")
    print(f"   Total runs: {len(summaries)}")
    
    models = set(s.get("model", "unknown") for s in summaries)
    print(f"   Models tested: {len(models)}")
    print(f"   Models: {', '.join(sorted(models))}")
    
    total_puzzles = sum(s.get("puzzles_attempted", 0) for s in summaries)
    total_solved = sum(s.get("puzzles_solved", 0) for s in summaries)
    overall_solve_rate = total_solved / total_puzzles if total_puzzles > 0 else 0
    
    print(f"   Total puzzles attempted: {total_puzzles}")
    print(f"   Total puzzles solved: {total_solved}")
    print(f"   Overall solve rate: {overall_solve_rate:.1%}")
    
    total_cost = sum(s.get("total_cost", 0) for s in summaries)
    total_upstream = sum(s.get("total_upstream_cost", 0) for s in summaries)
    print(f"   Total OpenRouter cost: ${total_cost:.6f}")
    print(f"   Total upstream cost: ${total_upstream:.6f}")
    
    print(f"\n✅ Results saved to results/run_summaries.csv")


if __name__ == "__main__":
    main()
