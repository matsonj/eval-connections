#!/usr/bin/env bash
# Round 2: every decisions model 5x (seeds 42-46) on the classic solver and the diagnostics, plus a Clef retune sweep.
# Usage: bash experimental/run_round2.sh            # phases A C B, in that order
#        PHASES="C" bash experimental/run_round2.sh  # just the sweep
# Then:  uv run python experimental/summarize_round2.py --since <the stamp printed at the start>
set -uo pipefail
cd "$(dirname "$0")/.."
PHASES=${PHASES:-"A C B"}
SEEDS=${SEEDS:-"42 43 44 45 46"}
MODELS=${MODELS:-"typesafe/jev-1.13 perplexity/pplx-decider-v1.1-27b inception/mercury-decide cloudflare/clef openai/gpt-6-luna-decisions microsoft/microsoft-decision-1"}
SWEEP_MODELS=${SWEEP_MODELS:-"cloudflare/clef typesafe/jev-1.13"}
STAMP=$(date +%Y-%m-%dT%H-%M-%S)
mkdir -p experimental/runs
LOG=experimental/runs/round2_$STAMP.log
run() { echo -e "\n===== $* =====" | tee -a "$LOG"; uv run python "$@" 2>&1 | tee -a "$LOG" || echo "!! FAILED: $*" | tee -a "$LOG"; }
threads() { [ "$1" = "inception/mercury-decide" ] && echo "$2" || echo "$3"; }
echo "round 2 started; summarize with: --since $STAMP" | tee -a "$LOG"

for P in $PHASES; do case $P in
A)  # classic solver, Jev-tuned defaults, 5 seeds per model
  for M in $MODELS; do for S in $SEEDS; do
    run experimental/decisions_classic_solver.py --model "$M" --seed $S --threads $(threads $M 10 20) --tag base
  done; done ;;
C)  # retune sweep: weight the direct set score more (Clef's strength), widen the pool, binary levels
  for M in $SWEEP_MODELS; do for S in $SEEDS; do
    run experimental/decisions_classic_solver.py --model "$M" --seed $S --tag set2      --set-weight 2
    run experimental/decisions_classic_solver.py --model "$M" --seed $S --tag set4      --set-weight 4
    run experimental/decisions_classic_solver.py --model "$M" --seed $S --tag setonly   --pair-weight 0
    run experimental/decisions_classic_solver.py --model "$M" --seed $S --tag set2_k120 --set-weight 2 --top-k 120
    run experimental/decisions_classic_solver.py --model "$M" --seed $S --tag bin_set2  --set-weight 2 --levels 2
  done; done ;;
B)  # diagnostics, 5 seeds per model (seed changes the sampled fakes and decoys)
  for M in $MODELS; do T=$(threads $M 5 10); for S in $SEEDS; do
    run experimental/relatedness_probe.py --model "$M" --seed $S --threads $T
    run experimental/category_recognition.py --model "$M" --seed $S --threads $T
    run experimental/state_sensitivity.py --model "$M" --seed $S --threads $T
    run experimental/contamination.py --model "$M" --seed $S --threads $T
  done; done ;;
esac; done
run experimental/summarize_round2.py --since "$STAMP"
echo -e "\nDone. Log: $LOG" | tee -a "$LOG"
