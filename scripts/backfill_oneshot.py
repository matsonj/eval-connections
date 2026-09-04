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
import csv
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

RUN_SUMMARIES_CSV = Path("results/run_summaries.csv")
MODEL_MAPPINGS_YML = Path("inputs/model_mappings.yml")
RECENT_DAYS = 90
SOLVE_RATE_FLOOR = 0.75


# Historical CSV rows recorded under old/renamed slugs -> current CLI name,
# so a mapping fix doesn't orphan a model's run history.
LEGACY_SLUG_ALIASES = {
    "anthropic/claude-4.5-sonnet": "sonnet-4.5",  # slug-order typo fixed 2026-07-22
}


def load_reverse_mapping() -> dict[str, str]:
    """OpenRouter model ID -> CLI name, from model_mappings.yml."""
    with open(MODEL_MAPPINGS_YML) as f:
        data = yaml.safe_load(f)
    reverse: dict[str, str] = dict(LEGACY_SLUG_ALIASES)
    for section in data["models"].values():
        for cli_name, openrouter_id in section.items():
            # Strip variant suffixes like ":free" so CSV IDs still match.
            reverse[openrouter_id.split(":")[0]] = cli_name
    return reverse


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> datetime:
    """Parse an ISO timestamp; MotherDuck emits some rows tz-naive."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_runs(csv_path: Path = RUN_SUMMARIES_CSV) -> list[dict[str, Any]]:
    """Read run_summaries.csv rows, normalizing the fields we key off."""
    runs: list[dict[str, Any]] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["mode"] = (row.get("mode") or "").strip() or "classic"
            row["start_dt"] = _ts(row.get("start_timestamp"))
            runs.append(row)
    return runs


def select_models(runs: list[dict[str, Any]], select_all: bool = False) -> tuple[list[str], set[str]]:
    """Return (selected OpenRouter IDs, already-backfilled IDs).

    select_all=True selects every model with any 20-puzzle classic run;
    default is the targeted subset (recent OR high solve rate).
    """
    canonical = [r for r in runs if _float(r.get("puzzles_attempted")) == 20]
    classic = [r for r in canonical if r["mode"] == "classic"]

    if select_all:
        selected = {r["model"] for r in classic}
    else:
        first_run: dict[str, datetime] = {}
        latest: dict[str, dict[str, Any]] = {}
        for r in classic:
            model = r["model"]
            if model not in first_run or r["start_dt"] < first_run[model]:
                first_run[model] = r["start_dt"]
            if model not in latest or r["start_dt"] > latest[model]["start_dt"]:
                latest[model] = r
        cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
        recent = {m for m, dt in first_run.items() if dt >= cutoff}
        high = {m for m, r in latest.items()
                if (_float(r.get("solve_rate")) or 0.0) >= SOLVE_RATE_FLOOR}
        selected = recent | high

    # Only trap-scored runs count as backfilled — legacy pre-trap smoke runs
    # used a different scoring scale and must be re-run.
    done = {r["model"] for r in canonical
            if r["mode"] == "oneshot" and (_float(r.get("trap_scored")) or 0.0) == 1}
    return sorted(selected), done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the resolved model list and exit (no API calls)")
    parser.add_argument("--threads", type=int, default=8,
                        help="Threads per eval run (default: 8)")
    parser.add_argument("--all", action="store_true",
                        help="Select every model with a 20-puzzle classic run (not just recent/high-scoring)")
    parser.add_argument("--force", action="store_true",
                        help="Re-run models that already have a trap-scored one-shot run "
                             "(needed after scoring-rule or prompt changes)")
    args = parser.parse_args()

    runs = load_runs()
    selected, done = select_models(runs, select_all=args.all)
    reverse = load_reverse_mapping()

    runnable: list[tuple[str, str]] = []  # (openrouter_id, cli_name)
    unmapped: list[str] = []
    skipped: list[str] = []
    for openrouter_id in selected:
        if openrouter_id in done and not args.force:
            skipped.append(openrouter_id)
            continue
        # CSV IDs may carry variant suffixes (":free"); mapping keys are stripped.
        cli_name = reverse.get(openrouter_id.split(":")[0])
        if cli_name is None:
            unmapped.append(openrouter_id)
            continue
        runnable.append((openrouter_id, cli_name))

    criteria = ("all models with a 20-puzzle classic run" if args.all
                else f"first run < {RECENT_DAYS}d ago OR solve rate >= {SOLVE_RATE_FLOOR:.0%}")
    print(f"Selection: {len(selected)} models ({criteria})"
          + (" [--force: re-running existing one-shot runs]" if args.force else ""))
    if skipped:
        print(f"\nAlready backfilled ({len(skipped)}), skipping:")
        for m in skipped:
            print(f"  - {m}")
    if unmapped:
        print(f"\n!! UNMAPPED ({len(unmapped)}) — no CLI name in model_mappings.yml, "
              f"add a mapping or ignore if deprecated:")
        for m in unmapped:
            print(f"  !! {m}")

    # Run fastest models first (per-puzzle avg from each model's latest run)
    # so results start landing early; models with no timing history go last.
    timing: dict[str, float] = {}
    for row in sorted(runs, key=lambda r: r["start_dt"]):
        avg = _float(row.get("avg_inference_sec"))
        if avg is None:
            avg = _float(row.get("avg_time_sec"))
        if avg is not None:
            timing[row["model"]] = avg  # last write wins = latest run
    runnable.sort(key=lambda mc: timing.get(mc[0], float("inf")))

    print(f"\nTo run ({len(runnable)} models x 20 one-shot calls each, fastest first):")
    for openrouter_id, cli_name in runnable:
        est = timing.get(openrouter_id)
        est_str = f"~{est:.0f}s/puzzle" if est is not None else "no timing history"
        print(f"  - {cli_name:24s} ({openrouter_id})  {est_str}")

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
        if proc.returncode == 3:
            # Insufficient OpenRouter credits — every remaining model would
            # fail the same way. Stop the fleet.
            remaining = [c for _, c in runnable[i:]]
            print(f"\n!! ABORTING BACKFILL: OpenRouter credits exhausted. "
                  f"{len(remaining)} models not attempted: {', '.join(remaining)}")
            print("Top up at https://openrouter.ai/settings/credits, then re-run "
                  "this script (completed models are skipped unless --force).")
            break

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
