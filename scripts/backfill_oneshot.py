#!/usr/bin/env python3
"""Backfill one-shot canonical runs for the leaderboard.

Selection (from results/run_summaries.csv, classic 20-puzzle runs only):
- models whose FIRST run is within the last 90 days ("released recently"), OR
- models whose LATEST run scored a solve rate >= 0.75.

CSV model names are OpenRouter IDs (e.g. "anthropic/claude-fable-5"); the CLI
takes mapping keys (e.g. "fable-5"), so we reverse-map via
inputs/model_mappings.yml and loudly report models that no longer map.

Models that already have a 20-puzzle one-shot run in the CSV are skipped, so
re-running after a partial failure only picks up the stragglers. Refresh the
CSV between sessions with: uv run python scripts/extract_summaries.py

Usage:
    uv run python scripts/backfill_oneshot.py --dry-run   # preview the list
    uv run python scripts/backfill_oneshot.py             # run the backfill
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

RUN_SUMMARIES_CSV = Path("results/run_summaries.csv")
MODEL_MAPPINGS_YML = Path("inputs/model_mappings.yml")
RECENT_DAYS = 90
SOLVE_RATE_FLOOR = 0.75


def load_reverse_mapping() -> dict[str, str]:
    """OpenRouter model ID -> CLI name, from model_mappings.yml."""
    with open(MODEL_MAPPINGS_YML) as f:
        data = yaml.safe_load(f)
    reverse: dict[str, str] = {}
    for section in data["models"].values():
        for cli_name, openrouter_id in section.items():
            # Strip variant suffixes like ":free" so CSV IDs still match.
            reverse[openrouter_id.split(":")[0]] = cli_name
    return reverse


def select_models(df: pd.DataFrame) -> tuple[list[str], set[str]]:
    """Return (selected OpenRouter IDs, already-backfilled IDs)."""
    if "mode" not in df.columns:
        df = df.assign(mode="classic")
    df = df.assign(mode=df["mode"].fillna("classic"))
    df = df[df["puzzles_attempted"] == 20].copy()
    df = df.assign(
        start_timestamp=pd.to_datetime(
            df["start_timestamp"], format="ISO8601", utc=True
        )
    )

    classic = df[df["mode"] == "classic"]
    first_run = classic.groupby("model")["start_timestamp"].min()
    latest = classic.loc[classic.groupby("model")["start_timestamp"].idxmax()]

    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    recent = set(first_run[first_run >= cutoff].index)
    high = set(latest[latest["solve_rate"] >= SOLVE_RATE_FLOOR]["model"])

    done = set(df[df["mode"] == "oneshot"]["model"])
    return sorted(recent | high), done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the resolved model list and exit (no API calls)")
    parser.add_argument("--threads", type=int, default=8,
                        help="Threads per eval run (default: 8)")
    args = parser.parse_args()

    df = pd.read_csv(RUN_SUMMARIES_CSV)
    selected, done = select_models(df)
    reverse = load_reverse_mapping()

    runnable: list[tuple[str, str]] = []  # (openrouter_id, cli_name)
    unmapped: list[str] = []
    skipped: list[str] = []
    for openrouter_id in selected:
        if openrouter_id in done:
            skipped.append(openrouter_id)
            continue
        # CSV IDs may carry variant suffixes (":free"); mapping keys are stripped.
        cli_name = reverse.get(openrouter_id.split(":")[0])
        if cli_name is None:
            unmapped.append(openrouter_id)
            continue
        runnable.append((openrouter_id, cli_name))

    print(f"Selection: {len(selected)} models "
          f"(first run < {RECENT_DAYS}d ago OR solve rate >= {SOLVE_RATE_FLOOR:.0%})")
    if skipped:
        print(f"\nAlready backfilled ({len(skipped)}), skipping:")
        for m in skipped:
            print(f"  - {m}")
    if unmapped:
        print(f"\n!! UNMAPPED ({len(unmapped)}) — no CLI name in model_mappings.yml, "
              f"add a mapping or ignore if deprecated:")
        for m in unmapped:
            print(f"  !! {m}")

    print(f"\nTo run ({len(runnable)} models x 20 one-shot calls each):")
    for openrouter_id, cli_name in runnable:
        print(f"  - {cli_name}  ({openrouter_id})")

    if args.dry_run:
        print("\n--dry-run: no evaluations executed")
        return 0
    if not runnable:
        print("\nNothing to run")
        return 0

    results: list[tuple[str, bool]] = []
    for i, (openrouter_id, cli_name) in enumerate(runnable, 1):
        print(f"\n[{i}/{len(runnable)}] Running {cli_name} ...", flush=True)
        proc = subprocess.run(
            ["uv", "run", "connections_eval", "run",
             "--model", cli_name, "--mode", "oneshot", "--canonical",
             "--threads", str(args.threads)],
        )
        ok = proc.returncode == 0
        results.append((cli_name, ok))
        print(f"[{i}/{len(runnable)}] {cli_name}: {'OK' if ok else f'FAILED (exit {proc.returncode})'}")

    print("\n===== Backfill summary =====")
    passed = [m for m, ok in results if ok]
    failed = [m for m, ok in results if not ok]
    print(f"Passed: {len(passed)}/{len(results)}")
    for m in passed:
        print(f"  OK      {m}")
    for m in failed:
        print(f"  FAILED  {m}")
    if failed:
        print("\nRe-run this script to retry failures "
              "(refresh the CSV first: uv run python scripts/extract_summaries.py)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
