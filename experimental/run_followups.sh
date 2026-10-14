#!/usr/bin/env bash
# Run every follow-up investigation once. Usage: bash experimental/run_followups.sh [decisions-model] [llm]
# Outputs land in experimental/runs/; a combined log in experimental/runs/followups_<timestamp>.log
set -uo pipefail
cd "$(dirname "$0")/.."
J=${1:-typesafe/jev-1.13}
H=${2:-anthropic/claude-haiku-5.5}
mkdir -p experimental/runs
LOG=experimental/runs/followups_$(date +%Y-%m-%dT%H-%M-%S).log
run() { echo -e "\n===== $* =====" | tee -a "$LOG"; uv run python "$@" 2>&1 | tee -a "$LOG" || echo "!! FAILED: $*" | tee -a "$LOG"; }

run experimental/relatedness_probe.py --model "$J" --repeats 3
run experimental/relatedness_probe.py --model "$H"
run experimental/category_recognition.py --model "$J"
run experimental/category_recognition.py --model "$H"
run experimental/state_sensitivity.py --model "$J"
run experimental/contamination.py --model "$J"
run experimental/contamination.py --model "$H" --free-recall
run experimental/llm_judge_solver.py --model "$H"
run experimental/hybrid_solver.py --mode filter --llm "$H"
run experimental/hybrid_solver.py --mode propose --llm "$H"
run experimental/batch_scaling.py --models "$J" --reps 10
shopt -s nullglob; probes=(experimental/runs/*_probe_*.json)
[ ${#probes[@]} -gt 0 ] && run experimental/calibration.py "${probes[@]}"
echo -e "\nDone. Log: $LOG" | tee -a "$LOG"
