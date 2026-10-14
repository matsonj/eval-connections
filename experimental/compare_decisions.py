"""Summarize the latest follow-up results per model into one markdown table (offline).

    uv run python experimental/compare_decisions.py > experimental/runs/compare.md
    uv run python experimental/compare_decisions.py --include-llms   # also show LLM reference rows
"""
import glob
import json
import sys
from pathlib import Path

RUNS = Path(__file__).resolve().parent / "runs"


def latest(pattern: str, key=lambda d: d.get("model")) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(glob.glob(str(RUNS / pattern))):  # timestamped names: later files win
        d = json.loads(Path(f).read_text())
        k = key(d)
        if k:
            out[k] = d
    return out


def f(x, fmt="{:.2f}"):
    return "–" if x is None else fmt.format(x)


probe = latest("*_probe_*_none.json")
cats = latest("*_categories_*.json")
state = latest("*_state_*.json")
cont = latest("*_contamination_*.json")
hf = latest("*_hybrid-filter_*.json", key=lambda d: d.get("decider"))
hp = latest("*_hybrid-propose_*.json", key=lambda d: d.get("decider"))
INCLUDE_LLMS = "--include-llms" in sys.argv
models = sorted(set(probe) | set(cats) | set(state) | set(cont) | set(hf) | set(hp),
                key=lambda m: (not m.startswith("typesafe"), "/claude" in m, m))
if not INCLUDE_LLMS:  # decisions models only; LLM rows (e.g. Haiku) are reference-only
    models = [m for m in models if m in hf or m in hp or (state.get(m) and "/claude" not in m)]

print("## Relatedness probe, category recognition, contamination\n")
print("| Model | Recall | FPR | AUROC vs mixed | AUROC vs near-miss | Purple recall | Repeat spread | Category naming | Contamination full / groups / blind |")
print("|---|---|---|---|---|---|---|---|---|")
for m in models:
    s = probe.get(m, {}).get("summary", {})
    c = cont.get(m, {}).get("conditions", {})
    ct = " / ".join(f(c.get(k, {}).get("accuracy"), "{:.0%}") for k in ("full", "groups_only", "blind")) if c else "–"
    print(f"| {m} | {f(s.get('true_recall'), '{:.0%}')} | {f(s.get('false_positive_rate'), '{:.0%}')} | "
          f"{f(s.get('auroc_true_vs_mixed'))} | {f(s.get('auroc_true_vs_near_miss'))} | "
          f"{f((s.get('true_recall_by_color') or {}).get('purple'), '{:.0%}')} | "
          f"{f(s.get('mean_max_minus_min_across_repeats'))} | {f(cats.get(m, {}).get('accuracy'), '{:.0%}')} | {ct} |")

print("\n## State sensitivity (AUROC, real vs all fakes)\n")
conds = None
for m in models:
    rows = state.get(m, {}).get("rows")
    if not rows:
        continue
    if conds is None:
        conds = [(r["distractor"], r["words"]) for r in rows]
        print("| Model | " + " | ".join(f"{d} {w}" for d, w in conds) + " | explainer FPR |")
        print("|---" * (len(conds) + 2) + "|")
    fpr = next((r["false_positive_rate"] for r in rows if r["distractor"] == "explainer"), None)
    print(f"| {m} | " + " | ".join(f(r.get("auroc_true_vs_all_negatives")) for r in rows) + f" | {f(fpr, '{:.0%}')} |")

print("\n## Hybrid solvers (canonical 20)\n")
print("| Decider | Mode | LLM | Wins | Groups | Mistakes/puzzle | Total cost | Wall s | Fallbacks |")
print("|---|---|---|---|---|---|---|---|---|")
for mode, src in (("filter", hf), ("propose", hp)):
    for m in models:
        d = src.get(m)
        if not d:
            continue
        r = d["results"]
        print(f"| {m} | {mode} | {d.get('llm')} | {sum(x['won'] for x in r)}/{len(r)} | "
              f"{sum(x['groups_found'] for x in r)}/{4 * len(r)} | {sum(x['mistakes'] for x in r) / len(r):.2f} | "
              f"${d.get('llm_cost_usd', 0) + d.get('decider_cost_usd', 0):.3f} | {f(d.get('wall_seconds'), '{:.0f}')} | {d.get('fallbacks')} |")

print("\n## Batch scaling (median ms by questions per request)\n")
rows = []
for fpath in sorted(glob.glob(str(RUNS / "*_batch_scaling_*.json"))):
    for r in json.loads(Path(fpath).read_text()).get("rows", []):
        rows.append(r)
latest_rows = {(r["model"], r["questions"]): r for r in rows}
sizes = sorted({q for _, q in latest_rows})
bm = sorted({m for m, _ in latest_rows}, key=lambda m: (not m.startswith("typesafe"), m))
print("| Model | " + " | ".join(f"{q}q" for q in sizes) + " | ms/question @64 |")
print("|---" * (len(sizes) + 2) + "|")
for m in bm:
    cells = [f(latest_rows.get((m, q), {}).get("median_ms"), "{:.0f}") for q in sizes]
    print(f"| {m} | " + " | ".join(cells) + f" | {f(latest_rows.get((m, max(sizes)), {}).get('ms_per_question'), '{:.1f}')} |")
