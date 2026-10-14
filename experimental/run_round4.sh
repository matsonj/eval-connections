#!/usr/bin/env bash
# Round 4: is it the set question? Pool diagnostic with the probe's wording and smaller batches, then the classic
# solver with the probe's set wording on Clef and Jev.
set -uo pipefail
cd "$(dirname "$0")/.."
SEEDS=${SEEDS:-"42 43 44 45 46"}
MODELS=${MODELS:-"typesafe/jev-1.13 perplexity/pplx-decider-v1.1-27b inception/mercury-decide cloudflare/clef openai/gpt-6-luna-decisions microsoft/microsoft-decision-1"}
STAMP=$(date +%Y-%m-%dT%H-%M-%S)
LOG=experimental/runs/round4_$STAMP.log
run() { echo -e "\n===== $* =====" | tee -a "$LOG"; uv run python "$@" 2>&1 | tee -a "$LOG" || echo "!! FAILED: $*" | tee -a "$LOG"; }
echo "round 4 started $STAMP" | tee -a "$LOG"
for M in $MODELS; do T=5; [ "$M" = "inception/mercury-decide" ] && T=3
  for S in $SEEDS; do
    run experimental/pool_diagnostic.py --model "$M" --seed $S --threads $T --set-wording probe --set-batch 64
    run experimental/pool_diagnostic.py --model "$M" --seed $S --threads $T --set-wording solver --set-batch 8
    run experimental/pool_diagnostic.py --model "$M" --seed $S --threads $T --set-wording probe --set-batch 8
  done
done
for M in cloudflare/clef typesafe/jev-1.13; do for S in $SEEDS; do
  run experimental/decisions_classic_solver.py --model "$M" --seed $S --tag probe_set --set-wording probe
  run experimental/decisions_classic_solver.py --model "$M" --seed $S --tag probe_setonly --set-wording probe --pair-weight 0
done; done
run experimental/summarize_round3.py --since "$STAMP" --none-since 2026-10-13T10-51-32
run experimental/summarize_round2.py --since 2026-10-13T10-51-32
echo -e "\nDone. Log: $LOG" | tee -a "$LOG"
