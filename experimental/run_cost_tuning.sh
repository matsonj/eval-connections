#!/usr/bin/env bash
# Cost tuning on Jev: one lever at a time, then combinations. Each line below is "tag flags...".
#   CONFIGS="noul_pair k30" SEEDS="42 43" experimental/run_cost_tuning.sh     # screen two configs on two seeds
# Runs PARALLEL configs at once (each run already uses 20 threads), then prints the summary table.
set -uo pipefail
cd "$(dirname "$0")/.."
SEEDS=${SEEDS:-"42 43"}
PARALLEL=${PARALLEL:-3}
MODEL=${MODEL:-typesafe/jev-1.13}
read -r -d '' TABLE <<'EOF'
base
noul_pair       --pair-type noul
noul_set        --set-type noul
noul_both       --pair-type noul --set-type noul
terse_pair      --pair-wording terse
tersetask_pair  --pair-wording terse-task
terse_set       --set-wording terse
tersetask_set   --set-wording terse-task
lv2             --levels 2
k20             --top-k 20
k30             --top-k 30
k40             --top-k 40
margin05        --set-margin 0.5
margin1         --set-margin 1.0
reask_touched   --reask touched
reask_unc       --reask uncertain
reask_unc_wide  --reask uncertain --uncertain-band 0.15,0.85
endgame         --endgame splits
nb_tersetask    --pair-type noul --set-type noul --pair-wording terse-task
nb_margin03     --pair-type noul --set-type noul --set-margin 0.3
nb_margin05     --pair-type noul --set-type noul --set-margin 0.5
nb_touched      --pair-type noul --set-type noul --reask touched
nb_unc          --pair-type noul --set-type noul --reask uncertain
nb_endgame      --pair-type noul --set-type noul --endgame splits
nb_combo        --pair-type noul --set-type noul --reask touched --set-margin 0.5 --endgame splits
score_combo     --levels 2 --set-margin 0.5 --reask uncertain --endgame splits
nb_eg_unc       --pair-type noul --set-type noul --endgame splits --reask uncertain
EOF
STAMP=$(date +%Y-%m-%dT%H-%M-%S)
LOGDIR=experimental/runs/cost_$STAMP
mkdir -p "$LOGDIR"
CONFIGS=${CONFIGS:-$(echo "$TABLE" | awk '{print $1}')}
for C in $CONFIGS; do
  LINE=$(echo "$TABLE" | awk -v c="$C" '$1 == c')
  [ -z "$LINE" ] && LINE="$C ${EXTRA_FLAGS:-}"   # ad-hoc tag: flags from EXTRA_FLAGS
  FLAGS=$(echo "$LINE" | cut -s -d' ' -f2-)
  for S in $SEEDS; do
    printf '%s\0' "uv run python experimental/decisions_classic_solver.py --model $MODEL --seed $S --tag $C $FLAGS > $LOGDIR/${C}_$S.log 2>&1 || echo \"!! FAILED: $C seed $S\""
  done
done | xargs -0 -n 1 -P "$PARALLEL" sh -c
uv run python experimental/summarize_round2.py --since "$STAMP" --model "$MODEL"
echo "logs: $LOGDIR"
