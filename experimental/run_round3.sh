#!/usr/bin/env bash
# Round 3: where does each model lose in the solver? Pool diagnostic + relatedness probe with the board in the state.
# Usage: bash experimental/run_round3.sh     (safe to run alongside round 2; light load)
set -uo pipefail
cd "$(dirname "$0")/.."
SEEDS=${SEEDS:-"42 43 44 45 46"}
MODELS=${MODELS:-"typesafe/jev-1.13 perplexity/pplx-decider-v1.1-27b inception/mercury-decide cloudflare/clef openai/gpt-6-luna-decisions microsoft/microsoft-decision-1"}
STAMP=$(date +%Y-%m-%dT%H-%M-%S)
LOG=experimental/runs/round3_$STAMP.log
run() { echo -e "\n===== $* =====" | tee -a "$LOG"; uv run python "$@" 2>&1 | tee -a "$LOG" || echo "!! FAILED: $*" | tee -a "$LOG"; }
echo "round 3 started $STAMP" | tee -a "$LOG"
for M in $MODELS; do T=5; [ "$M" = "inception/mercury-decide" ] && T=3
  for S in $SEEDS; do
    run experimental/pool_diagnostic.py --model "$M" --seed $S --threads $T
    run experimental/relatedness_probe.py --model "$M" --seed $S --context board --threads $T
  done
done
echo -e "\nDone. Log: $LOG" | tee -a "$LOG"
