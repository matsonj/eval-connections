"""Median (min–max) over repeated runs: classic solver by model and tuning tag, plus the diagnostics. Offline.

    uv run python experimental/summarize_round2.py --since 2026-10-13T10-00-00 > experimental/runs/round2.md
"""
import argparse
import glob
import json
import statistics
from collections import defaultdict
from pathlib import Path

RUNS = Path(__file__).resolve().parent / "runs"
ORDER = ["typesafe/jev-1.13", "perplexity/pplx-decider-v1.1-27b", "inception/mercury-decide", "cloudflare/clef",
         "openai/gpt-6-luna-decisions", "microsoft/microsoft-decision-1"]
TAGS = ["base", "set2", "set4", "setonly", "set2_k120", "bin_set2", "probe_set", "probe_setonly",
        # cost tuning (run_cost_tuning.sh)
        "noul_pair", "noul_set", "noul_both", "terse_pair", "tersetask_pair", "terse_set", "tersetask_set", "lv2",
        "k20", "k30", "k40", "margin05", "margin1", "reask_touched", "reask_unc", "reask_unc_wide", "endgame",
        "nb_tersetask", "nb_margin03", "nb_margin05", "nb_touched", "nb_unc", "nb_endgame", "nb_combo", "score_combo", "nb_eg_unc", "lv2_m05"]


def files(pattern: str, since: str):
    for f in sorted(glob.glob(str(RUNS / pattern))):
        if Path(f).name[:19] >= since:
            yield json.loads(Path(f).read_text())


def mmm(xs, fmt="{:.1f}"):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "–"
    med = fmt.format(statistics.median(xs))
    return med if len(xs) == 1 else f"{med} ({fmt.format(min(xs))}–{fmt.format(max(xs))})"


def key(m):
    return (ORDER.index(m) if m in ORDER else 99, m)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="0000", help="only files whose timestamp prefix is >= this")
    p.add_argument("--model", default=None, help="only this model's classic runs")
    p.add_argument("--all-puzzles", action="store_true", help="--all runs instead of canonical-20 runs")
    args = p.parse_args()
    since = args.since

    classic = defaultdict(list)
    for d in files("*.json", since):
        if "tag" in d and "results" in d and "config" in d:
            r = d["results"]
            if (args.model and d["model"] != args.model) or (len(r) > 20) != args.all_puzzles:
                continue
            classic[(d["model"], d["tag"])].append({
                "seed": d["seed"], "wins": sum(x["won"] for x in r), "groups": sum(x["groups_found"] for x in r),
                "mistakes": sum(x["mistakes"] for x in r) / len(r), "cost": sum(x["cost_usd"] for x in r),
                "errors": sum(1 for x in r if x.get("error")),
                "tokens": sum(x["input_tokens"] for x in r), "n": len(r),
                "first": sum(x["first_guess_correct"] for x in r)})
    if classic:
        print("## Classic solver, median (min–max) over seeds\n")
        n = next(iter(classic.values()))[0]["n"]
        print(f"| Model | Config | Runs | Wins /{n} | Groups /{4 * n} | Mistakes/puzzle | First guess right | "
              "Input tokens/run | Cost/run | Failed puzzles |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for (m, tag) in sorted(classic, key=lambda k: (key(k[0]), TAGS.index(k[1]) if k[1] in TAGS else 99, k[1])):
            rs = classic[(m, tag)]
            print(f"| {m} | {tag} | {len(rs)} | {mmm([x['wins'] for x in rs])} | {mmm([x['groups'] for x in rs])} | "
                  f"{mmm([x['mistakes'] for x in rs], '{:.2f}')} | {mmm([x['first'] for x in rs])} | "
                  f"{mmm([x['tokens'] / 1000 for x in rs], '{:.0f}k')} | {mmm([x['cost'] for x in rs], '${:.3f}')} | {sum(x['errors'] for x in rs)} |")

    probe = defaultdict(list)
    for d in files("*_probe_*_none.json", since):
        probe[d["model"]].append(d["summary"])
    cats = defaultdict(list)
    for d in files("*_categories_*.json", since):
        cats[d["model"]].append(d["accuracy"])
    cont = defaultdict(list)
    for d in files("*_contamination_*.json", since):
        cont[d["model"]].append({k: v["accuracy"] for k, v in d["conditions"].items()})
    state = defaultdict(list)
    for d in files("*_state_*.json", since):
        state[d["model"]].append({(r["distractor"], r["words"]): r for r in d["rows"]})

    models = sorted(set(probe) | set(cats) | set(cont) | set(state), key=key)
    if models:
        pc = lambda xs: mmm([x * 100 for x in xs if x is not None], "{:.0f}")
        print("\n## Diagnostics, median (min–max) over seeds; percentages\n")
        print("| Model | Runs | Recall | False positives | AUROC vs mixed | AUROC vs near-miss | Purple recall | Category naming | Contamination full / blind |")
        print("|---|---|---|---|---|---|---|---|---|")
        for m in models:
            s = probe.get(m, [])
            c = cont.get(m, [])
            print(f"| {m} | {len(s)} | {pc([x['true_recall'] for x in s])} | {pc([x['false_positive_rate'] for x in s])} | "
                  f"{mmm([x['auroc_true_vs_mixed'] for x in s], '{:.2f}')} | {mmm([x['auroc_true_vs_near_miss'] for x in s], '{:.2f}')} | "
                  f"{pc([(x['true_recall_by_color'] or {}).get('purple') for x in s])} | {pc(cats.get(m, []))} | "
                  f"{pc([x.get('full') for x in c])} / {pc([x.get('blind') for x in c])} |")
        print("\n## State sensitivity, median over seeds\n")
        print("| Model | AUROC none | AUROC prose 3200 | AUROC other words 3200 | FPR none | FPR explainer |")
        print("|---|---|---|---|---|---|")
        for m in models:
            rs = state.get(m, [])
            if not rs:
                continue
            g = lambda k, f: [next((r[f] for kk, r in x.items() if kk[0] == k[0] and (k[1] is None or kk[1] == k[1])), None) for x in rs]
            print(f"| {m} | {mmm(g(('none', 0), 'auroc_true_vs_all_negatives'), '{:.2f}')} | "
                  f"{mmm(g(('prose', 3200), 'auroc_true_vs_all_negatives'), '{:.2f}')} | "
                  f"{mmm(g(('other_words', 3200), 'auroc_true_vs_all_negatives'), '{:.2f}')} | "
                  f"{pc(g(('none', 0), 'false_positive_rate'))} | {pc(g(('explainer', None), 'false_positive_rate'))} |")


if __name__ == "__main__":
    main()
