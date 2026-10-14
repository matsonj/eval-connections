"""Round 3 summary: where each model loses in the solver (pool diagnostic) and the probe with the board in the state.

    uv run python experimental/summarize_round3.py --since 2026-10-13T12-19-04 --none-since 2026-10-13T10-51-32 > experimental/runs/round3.md
"""
import argparse
import glob
import json
import statistics
from collections import defaultdict
from pathlib import Path

from summarize_round2 import key, mmm

RUNS = Path(__file__).resolve().parent / "runs"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="0000")
    p.add_argument("--none-since", default="0000", help="where to take the no-context probe runs from (round 2's stamp)")
    args = p.parse_args()
    since = args.since
    load = lambda pat, after=since: [json.loads(Path(f).read_text()) for f in sorted(glob.glob(str(RUNS / pat)))
                                     if Path(f).name[:19] >= after]

    pool = defaultdict(list)
    for d in load("*_pool*.json"):
        rows = d["rows"]
        n = len(rows)
        pool[(d["model"], d.get("set_wording", "solver"), d.get("set_batch", 64))].append({
            "in_pool": sum(r["in_pool"] for r in rows) / (4 * n) * 100,
            "any_in_pool": sum(1 for r in rows if r["in_pool"] > 0) / n * 100,
            "best_rank": statistics.median(r["best_pair_rank"] for r in rows),
            "pool_blend": sum(r["pick_pool_blend"] for r in rows) / n * 100,
            "pool_set": sum(r["pick_pool_set"] for r in rows) / n * 100,
            "truth_blend": sum(r["pick_truth_blend"] for r in rows) / n * 100,
            "truth_set": sum(r["pick_truth_set"] for r in rows) / n * 100,
            "gap": statistics.mean(r["direct_true_mean"] for r in rows)
                   - statistics.mean(r["direct_false_mean"] for r in rows if r["direct_false_mean"] is not None),
        })
    print("## Where the solver loses each model: first turn, median (min–max) over seeds\n")
    print("Pool = the top-60 four-word sets by pair score, the only candidates the solver ever scores directly.\n")
    print("| Model | Set wording | Set batch | Seeds | True groups in pool | Puzzles with ≥1 in pool | Best true-group pair rank (of 1,820) | First guess right: solver | …set score alone | …if truth were in pool (blend) | …(set alone) | Set-score gap true − other |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    pct = "{:.0f}%"
    for k3 in sorted(pool, key=lambda k: (key(k[0]), k[1] != "solver", -k[2])):
        rs = pool[k3]
        m = f"{k3[0]} | {k3[1]} | {k3[2]}"
        g = lambda k, f=pct: mmm([r[k] for r in rs], f)
        print(f"| {m} | {len(rs)} | {g('in_pool')} | {g('any_in_pool')} | {g('best_rank', '{:.0f}')} | {g('pool_blend')} | "
              f"{g('pool_set')} | {g('truth_blend')} | {g('truth_set')} | {g('gap', '{:.2f}')} |")

    none_, board = defaultdict(list), defaultdict(list)
    for d in load("*_probe_*_board.json"):
        board[d["model"]].append(d["summary"])
    for d in load("*_probe_*_none.json", args.none_since):
        none_[d["model"]].append(d["summary"])
    if board:
        print("\n## Relatedness probe: no context vs the full board in the state, median over seeds\n")
        print("| Model | Recall none → board | False positives none → board | AUROC near-miss none → board | Purple recall none → board |")
        print("|---|---|---|---|---|")
        for m in sorted(board, key=key):
            a, b = none_.get(m, []), board[m]
            f = lambda xs, k, mul=100, fmt="{:.0f}": fmt.format(statistics.median(x[k] * mul for x in xs)) if xs else "–"
            pr = lambda xs: "{:.0f}".format(statistics.median(x["true_recall_by_color"]["purple"] * 100 for x in xs)) if xs else "–"
            print(f"| {m} | {f(a, 'true_recall')} → {f(b, 'true_recall')} | {f(a, 'false_positive_rate')} → {f(b, 'false_positive_rate')} | "
                  f"{f(a, 'auroc_true_vs_near_miss', 1, '{:.2f}')} → {f(b, 'auroc_true_vs_near_miss', 1, '{:.2f}')} | {pr(a)} → {pr(b)} |")


if __name__ == "__main__":
    main()
