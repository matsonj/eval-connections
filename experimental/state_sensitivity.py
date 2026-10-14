"""Shared-state sensitivity: how fast does accuracy fall as irrelevant text is added to the state?

Reuses the relatedness_probe.py sets and question, and adds a "notes" field to the state (decisions models) or
a "Background notes" paragraph before the prompt (LLMs). Distractor types:

  prose        neutral sentences about an unrelated topic, at each length in --lengths (words)
  other_words  words drawn from other puzzles' boards, at each length; a distractor that looks like the task
  explainer    a short explanation of Connections category types, the kind of "helpful" context the
               classic-solver README found hurt Jev every time

Each condition reports true-group recall, false positive rate and AUROC (real vs fake), against a no-notes
baseline. Decisions models batch 64 sets per request, so a full sweep is a few dozen requests.

    uv run python experimental/state_sensitivity.py --model typesafe/jev-1.13
    uv run python experimental/state_sensitivity.py --model typesafe/jev-1.13 --lengths 0,100,400,1600
    uv run python experimental/state_sensitivity.py --model anthropic/claude-haiku-4.5 --lengths 0,400
"""
import argparse
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relatedness_probe as probe  # noqa: E402
from _common import (LLM, Decisions, all_raw_puzzles, is_decisions_model, load_api_key, pct,  # noqa: E402
                     save_run)

PROSE = (
    "The warehouse receives pallets on Tuesday and Thursday mornings. Drivers check in at the north gate and "
    "wait for a bay assignment. Each pallet is scanned, weighed and logged before it moves to storage. "
    "Cold items go to the rear cooler within twenty minutes. The night shift restocks the pick faces and "
    "clears the returns cage. Inventory counts run on the first Monday of each month. Damaged cartons are "
    "photographed and set aside for the claims team. Forklift batteries charge overnight in the east bay. "
    "The office closes at six, and the gate code changes every quarter. "
)
EXPLAINER = (
    "About Connections categories: groups are often wordplay rather than meaning. Common types are "
    "fill-in-the-blank phrases (words that precede or follow a shared word, such as ___ BALL), hidden words "
    "inside longer words, homophones, anagrams, words with a letter added or removed, members of a named set "
    "(teams, brands, characters), and synonyms for an unusual sense of a word. The hardest group usually uses "
    "wordplay, and red herrings are words that fit two groups."
)


def prose(n: int) -> str:
    words = (PROSE * (n // len(PROSE.split()) + 1)).split()
    return " ".join(words[:n])


def other_words(n: int, exclude_ids: set[int], rng: random.Random) -> str:
    pool = [w.upper() for p in all_raw_puzzles() if p["id"] not in exclude_ids for w in p["words"]]
    rng.shuffle(pool)
    while len(pool) < n:
        pool += pool
    return ", ".join(pool[:n])


def score(items, model_is_decisions, client, notes: str | None, threads: int) -> dict[str, float | None]:
    if model_is_decisions:
        state = {"puzzle": probe.BLURB, **({"notes": notes} if notes else {})}
        scores = {}
        for start in range(0, len(items), 64):
            chunk = items[start:start + 64]
            try:
                ans = client.ask(state, {f"q{i}": probe.decisions_question(it) for i, it in enumerate(chunk)})
                scores.update({it.item_id: ans[f"q{i}"]["score"] / (len(probe.LEVELS) - 1)
                               for i, it in enumerate(chunk)})
            except Exception as exc:  # noqa: BLE001
                print(f"request failed: {exc}", file=sys.stderr)
                client.totals.record(failures=1)
        return scores

    def one(it):
        prompt = (f"Background notes: {notes}\n\n" if notes else "") + probe.llm_prompt(it, "none")
        try:
            text = client.complete(prompt)
        except Exception as exc:  # noqa: BLE001
            print(f"{it.item_id}: {exc}", file=sys.stderr)
            return it.item_id, None
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]
        return it.item_id, (min(max(nums[-1], 0), 100) / 100 if nums else None)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        return dict(pool.map(one, items))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default="typesafe/jev-1.13")
    p.add_argument("--kind", choices=["auto", "decisions", "llm"], default="auto")
    p.add_argument("--lengths", default="0,50,200,800,3200", help="distractor lengths in words; 0 is the baseline")
    p.add_argument("--types", default="prose,other_words,explainer")
    p.add_argument("--puzzle-ids", type=lambda s: [int(x) for x in s.split(",")], default=None)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--threads", type=int, default=10)
    p.add_argument("--reasoning-effort", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    rng = random.Random(args.seed)
    items = probe.build_items(args.puzzle_ids, False, random.Random(args.seed), 2, 4)
    item_puzzles = {it.puzzle_id for it in items}
    lengths = [int(x) for x in args.lengths.split(",")]
    types = args.types.split(",")
    conditions: list[tuple[str, int, str | None]] = [("none", 0, None)]
    for t in types:
        if t == "explainer":
            conditions.append(("explainer", len(EXPLAINER.split()), EXPLAINER))
            continue
        for n in lengths:
            if n:
                conditions.append((t, n, prose(n) if t == "prose" else other_words(n, item_puzzles, rng)))
    if args.dry_run:
        print(f"{len(items)} sets x {len(conditions)} conditions")
        for t, n, notes in conditions:
            print(f"  {t:<12} {n:>5} words  {(notes or '')[:70]}")
        return

    api_key = load_api_key()
    decisions = is_decisions_model(args.model, args.kind)
    client = Decisions(api_key, args.model) if decisions else LLM(api_key, args.model, args.reasoning_effort)
    print(f"model {args.model}   {len(items)} sets per condition")
    print(f"{'distractor':<12} {'words':>6} {'recall':>7} {'FPR':>6} {'AUROC':>6} {'P(yes) real':>12}")
    rows = []
    for t, n, notes in conditions:
        s = probe.summarize(items, [score(items, decisions, client, notes, args.threads)])
        rows.append({"distractor": t, "words": n, **{k: s[k] for k in (
            "true_recall", "false_positive_rate", "auroc_true_vs_all_negatives", "unanswered")},
            "mean_p_true": s["by_kind"]["true"]["mean_p_yes"]})
        auc = s["auroc_true_vs_all_negatives"]
        mp = s["by_kind"]["true"]["mean_p_yes"]
        auc_s = "n/a" if auc is None else f"{auc:.3f}"
        mp_s = "n/a" if mp is None else f"{mp:.2f}"
        print(f"{t:<12} {n:>6} {pct(s['true_recall']):>7} {pct(s['false_positive_rate']):>6} {auc_s:>6} {mp_s:>12}")
    print(f"\n{client.totals.line()}")
    print(f"saved {save_run('state', args.model, {'model': args.model, 'seed': args.seed, 'rows': rows, 'cost_usd': client.totals.cost_usd})}")


if __name__ == "__main__":
    main()
