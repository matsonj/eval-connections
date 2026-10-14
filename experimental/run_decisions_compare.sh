#!/usr/bin/env bash
# Re-run the decisions-model follow-ups for every other decisions model, with the same settings as the Jev runs.
# Usage: bash experimental/run_decisions_compare.sh [llm-for-hybrids]
# Override the model list with MODELS="a/b c/d" bash experimental/run_decisions_compare.sh
set -uo pipefail
cd "$(dirname "$0")/.."
H=${1:-anthropic/claude-haiku-5.5}
MODELS=${MODELS:-"perplexity/pplx-decider-v1.1-27b inception/mercury-decide cloudflare/clef openai/gpt-6-luna-decisions microsoft/microsoft-decision-1"}
mkdir -p experimental/runs
LOG=experimental/runs/decisions_compare_$(date +%Y-%m-%dT%H-%M-%S).log
run() { echo -e "\n===== $* =====" | tee -a "$LOG"; uv run python "$@" 2>&1 | tee -a "$LOG" || echo "!! FAILED: $*" | tee -a "$LOG"; }

for M in $MODELS; do
  T=10; [ "$M" = "inception/mercury-decide" ] && T=5   # Mercury returns 429s at higher concurrency
  run experimental/relatedness_probe.py --model "$M" --repeats 3 --threads $T
  run experimental/category_recognition.py --model "$M" --threads $T
  run experimental/state_sensitivity.py --model "$M" --threads $T
  run experimental/contamination.py --model "$M" --threads $T
  run experimental/hybrid_solver.py --mode filter --decider "$M" --llm "$H" --threads $T
  run experimental/hybrid_solver.py --mode propose --decider "$M" --llm "$H" --threads $T
done
run experimental/batch_scaling.py --models "$(echo $MODELS | tr ' ' ',')" --reps 10
shopt -s nullglob; probes=(experimental/runs/*_probe_*.json)
[ ${#probes[@]} -gt 0 ] && run experimental/calibration.py "${probes[@]}" --out experimental/runs/calibration_all.svg
echo -e "\nDone. Log: $LOG" | tee -a "$LOG"
