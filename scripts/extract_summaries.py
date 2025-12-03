#!/usr/bin/env python3
"""Extract run summaries from MotherDuck controllog and create a CSV report."""

import csv
import os
from pathlib import Path
from typing import List, Dict, Any
import duckdb  # type: ignore


def extract_run_summaries_from_motherduck(db: str = "md:") -> List[Dict[str, Any]]:
    """Extract all run summaries by aggregating from controllog events and postings."""
    summaries = []
    
    print(f"Connecting to MotherDuck database: {db}")
    con = duckdb.connect(db)
    
    try:
        # Query to aggregate run summaries from controllog events and postings
        # This aggregates metrics per run_id
        # Note: payload_json and dims_json are STRUCT types, not JSON, so we use dot notation
        query = """
        WITH run_metadata AS (
            -- Get run metadata (model, timestamps) from events
            -- Model is in payload_json.model for model_prompt/model_completion events
            -- Version is not stored in controllog, will default to 2.0.2
            SELECT 
                e.run_id,
                MAX(CASE WHEN e.kind IN ('model_prompt', 'model_completion') THEN e.payload_json.model END) AS model,
                MIN(e.event_time) AS start_timestamp,
                MAX(e.event_time) AS end_timestamp
            FROM controllog.events e
            WHERE e.run_id IS NOT NULL
            GROUP BY e.run_id
        ),
        puzzle_stats AS (
            -- Count puzzles attempted and solved per run
            -- puzzles_attempted: count all unique puzzle_ids from any event
            -- puzzles_solved: count puzzles that reached DONE state (from postings)
            SELECT 
                e.run_id,
                COUNT(DISTINCT e.payload_json.puzzle_id) AS puzzles_attempted,
                COUNT(DISTINCT CASE 
                    WHEN p.dims_json."to" = 'DONE' 
                    THEN e.payload_json.puzzle_id 
                END) AS puzzles_solved
            FROM controllog.events e
            LEFT JOIN controllog.postings p ON p.event_id = e.event_id AND p.account_type = 'truth.state'
            WHERE e.run_id IS NOT NULL
            AND e.payload_json.puzzle_id IS NOT NULL
            GROUP BY e.run_id
        ),
        guess_stats AS (
            -- Aggregate guess statistics from model_completion events
            SELECT 
                e.run_id,
                COUNT(*) AS total_guesses,
                COUNT(CASE WHEN e.payload_json.result = 'CORRECT' OR e.payload_json.result LIKE 'CORRECT%' THEN 1 END) AS correct_guesses,
                COUNT(CASE WHEN e.payload_json.result LIKE 'INCORRECT%' THEN 1 END) AS incorrect_guesses,
                COUNT(CASE WHEN e.payload_json.result LIKE 'INVALID%' THEN 1 END) AS invalid_responses
            FROM controllog.events e
            WHERE e.run_id IS NOT NULL
            AND e.kind = 'model_completion'
            GROUP BY e.run_id
        ),
        token_stats AS (
            -- Aggregate token and cost statistics from postings
            -- Tokens: unit is "+tokens", account_type is "resource.tokens", phase in dims_json.phase
            -- Money: unit is "$", account_type is "resource.money", account_id contains "vendor:openrouter" or "vendor:upstream"
            SELECT 
                e.run_id,
                -- Total tokens: sum of all token postings (both prompt and completion phases)
                SUM(CASE WHEN p.account_type = 'resource.tokens' AND p.unit = '+tokens' AND p.dims_json.phase = 'prompt' THEN ABS(p.delta_numeric) ELSE 0 END) +
                SUM(CASE WHEN p.account_type = 'resource.tokens' AND p.unit = '+tokens' AND p.dims_json.phase = 'completion' THEN ABS(p.delta_numeric) ELSE 0 END) AS total_tokens,
                -- Prompt tokens
                SUM(CASE WHEN p.account_type = 'resource.tokens' AND p.unit = '+tokens' AND p.dims_json.phase = 'prompt' THEN ABS(p.delta_numeric) ELSE 0 END) AS total_prompt_tokens,
                -- Completion tokens
                SUM(CASE WHEN p.account_type = 'resource.tokens' AND p.unit = '+tokens' AND p.dims_json.phase = 'completion' THEN ABS(p.delta_numeric) ELSE 0 END) AS total_completion_tokens,
                -- OpenRouter cost (vendor:openrouter)
                SUM(CASE WHEN p.account_type = 'resource.money' AND p.unit = '$' AND p.account_id LIKE 'vendor:openrouter%' THEN ABS(p.delta_numeric) ELSE 0 END) AS total_cost,
                -- Upstream cost (vendor:upstream)
                SUM(CASE WHEN p.account_type = 'resource.money' AND p.unit = '$' AND p.account_id LIKE 'vendor:upstream%' THEN ABS(p.delta_numeric) ELSE 0 END) AS total_upstream_cost
            FROM controllog.postings p
            JOIN controllog.events e ON p.event_id = e.event_id
            WHERE e.run_id IS NOT NULL
            GROUP BY e.run_id
        ),
        time_stats AS (
            -- Aggregate time statistics from postings
            SELECT 
                e.run_id,
                SUM(CASE WHEN p.account_type = 'resource.time_ms' AND p.unit = 'ms' THEN ABS(p.delta_numeric) ELSE 0 END) / 1000.0 AS total_time_sec
            FROM controllog.postings p
            JOIN controllog.events e ON p.event_id = e.event_id
            WHERE e.run_id IS NOT NULL
            GROUP BY e.run_id
        )
        SELECT 
            rm.run_id,
            COALESCE(rm.model, 'unknown') AS model,
            '2.0.2' AS version,  -- Version not stored in controllog, defaulting to current version
            rm.start_timestamp,
            rm.end_timestamp,
            NULL AS seed,  -- Seed not stored in controllog events
            COALESCE(ps.puzzles_attempted, 0) AS puzzles_attempted,
            COALESCE(ps.puzzles_solved, 0) AS puzzles_solved,
            COALESCE(gs.total_guesses, 0) AS total_guesses,
            COALESCE(gs.correct_guesses, 0) AS correct_guesses,
            COALESCE(gs.incorrect_guesses, 0) AS incorrect_guesses,
            COALESCE(gs.invalid_responses, 0) AS invalid_responses,
            COALESCE(ts.total_tokens, 0) AS total_tokens,
            COALESCE(ts.total_prompt_tokens, 0) AS total_prompt_tokens,
            COALESCE(ts.total_completion_tokens, 0) AS total_completion_tokens,
            COALESCE(ts.total_cost, 0.0) AS total_cost,
            COALESCE(ts.total_upstream_cost, 0.0) AS total_upstream_cost,
            COALESCE(tims.total_time_sec, 0.0) AS total_time_sec
        FROM run_metadata rm
        LEFT JOIN puzzle_stats ps ON rm.run_id = ps.run_id
        LEFT JOIN guess_stats gs ON rm.run_id = gs.run_id
        LEFT JOIN token_stats ts ON rm.run_id = ts.run_id
        LEFT JOIN time_stats tims ON rm.run_id = tims.run_id
        ORDER BY rm.start_timestamp DESC
        """
        
        print("Querying controllog.events and controllog.postings...")
        results = con.execute(query).fetchall()
        columns = [desc[0] for desc in con.description]
        
        for row in results:
            summary = dict(zip(columns, row))
            # Convert to expected format
            summary["log_file"] = None  # Not available from MotherDuck
            summary["timestamp"] = summary.get("start_timestamp")
            summary["token_count_method"] = "API"  # Default assumption
            
            # If model is None, try to extract from run_id (format: YYYY-MM-DDTHH-MM-SS_model)
            if not summary.get("model") and summary.get("run_id"):
                parts = summary["run_id"].split("_", 1)
                if len(parts) > 1:
                    summary["model"] = parts[1]
                else:
                    summary["model"] = "unknown"
            
            summaries.append(summary)
            print(f"  Found run summary: {summary.get('run_id', 'unknown')} (v{summary.get('version', 'unknown')})")
        
    finally:
        con.close()
    
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
        
        # Calculate average time per puzzle
        total_time = summary.get("total_time_sec", 0) or 0
        puzzles_attempted = summary.get("puzzles_attempted", 0) or 0
        if puzzles_attempted > 0:
            summary["avg_time_sec"] = total_time / puzzles_attempted
        else:
            summary["avg_time_sec"] = 0.0
    
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
    print("🔍 Extracting run summaries from MotherDuck...")
    
    # Get MotherDuck database connection string from environment
    db = os.environ.get("MOTHERDUCK_DB", "md:")
    
    # Extract summaries from MotherDuck
    summaries = extract_run_summaries_from_motherduck(db)
    
    if not summaries:
        print("❌ No run summaries found in MotherDuck")
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
