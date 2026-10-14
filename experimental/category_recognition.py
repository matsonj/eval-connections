"""Knowledge vs discovery: given the right four words, can the model name their category?

For every real group, the model sees the four words and a set of category names: the puzzle's own four
names (so the other three are plausible near-misses from the same board) plus --decoys names from other
puzzles. It is scored on whether it picks the right name. Pair this with relatedness_probe.py:

  knows the category, misses the relatedness   -> a discovery failure (the solver's search is the problem)
  misses the category too                      -> a knowledge gap (no solver will fix it)

Decisions models get one 2-level Score per (words, category) and the highest P(yes) is their pick, which also
gives a rank for the right name. LLMs get one multiple-choice prompt per group and answer with a letter.

    uv run python experimental/category_recognition.py --model typesafe/jev-1.13
    uv run python experimental/category_recognition.py --model anthropic/claude-haiku-4.5
    uv run python experimental/category_recognition.py --dry-run
"""
import argparse
import random
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (BLURB, LLM, Decisions, all_raw_puzzles, is_decisions_model, load_api_key,  # noqa: E402
                     load_raw_puzzles, normalized, parse_letter, pct, save_run, score_question, upper_words)

TASK = "Judge whether this category name correctly describes the connection shared by all four words."
LEVELS = ["No: the category does not fit these four words", "Yes: the category is their connection"]
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def build_items(puzzles: list[dict], decoys: int, rng: random.Random) -> list[dict]:
    pool = [(p["id"], g["name"]) for p in all_raw_puzzles() for g in p["groups"]]
    items = []
    for p in puzzles:
        names = [g["name"] for g in p["groups"]]
        outside = [n for pid, n in pool if pid != p["id"] and n not in names]
        for g in p["groups"]:
            options = names + rng.sample(outside, min(decoys, len(outside)))
            rng.shuffle(options)
            words = upper_words(g["words"])
            rng.shuffle(words)
            items.append({"puzzle_id": p["id"], "color": g.get("color"), "answer": g["name"],
                          "words": words, "options": options})
    return items


def run_decisions(dec: Decisions, items: list[dict]) -> list[dict]:
    questions, index = {}, []
    for i, it in enumerate(items):
        for j, name in enumerate(it["options"]):
            questions[f"c{i}_{j}"] = score_question(TASK, LEVELS, words=it["words"], category=name)
            index.append((i, j))
    answers = dec.ask({"puzzle": BLURB}, questions)
    out = []
    for i, it in enumerate(items):
        ps = [normalized(answers[f"c{i}_{j}"], len(LEVELS)) for j in range(len(it["options"]))]
        truth = it["options"].index(it["answer"])
        rank = 1 + sum(p > ps[truth] for p in ps)
        out.append({**it, "picked": it["options"][max(range(len(ps)), key=ps.__getitem__)],
                    "p_yes": ps, "rank_of_answer": rank})
    return out


def run_llm(llm: LLM, items: list[dict], threads: int) -> list[dict]:
    def one(it):
        options = "\n".join(f"{LETTERS[j]}. {name}" for j, name in enumerate(it["options"]))
        prompt = (f"{BLURB}\n\nWhich category is the connection shared by these four words?\n"
                  f"Words: {', '.join(it['words'])}\n\n{options}\n\nReply with only the letter.")
        try:
            k = parse_letter(llm.complete(prompt, max_tokens=None), len(it["options"]))
        except Exception as exc:  # noqa: BLE001
            print(f"llm call failed: {exc}", file=sys.stderr)
            k = None
        return {**it, "picked": it["options"][k] if k is not None else None, "rank_of_answer": None}

    with ThreadPoolExecutor(max_workers=threads) as pool:
        return list(pool.map(one, items))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default="typesafe/jev-1.13")
    p.add_argument("--kind", choices=["auto", "decisions", "llm"], default="auto")
    p.add_argument("--decoys", type=int, default=2, help="category names from other puzzles added per group")
    p.add_argument("--puzzle-ids", type=lambda s: [int(x) for x in s.split(",")], default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--threads", type=int, default=10)
    p.add_argument("--reasoning-effort", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    items = build_items(load_raw_puzzles(args.puzzle_ids, args.all), args.decoys, random.Random(args.seed))
    if args.dry_run:
        print(f"{len(items)} groups, {len(items[0]['options'])} options each (chance {1 / len(items[0]['options']):.0%})")
        print(items[0])
        return
    api_key = load_api_key()
    if is_decisions_model(args.model, args.kind):
        client = Decisions(api_key, args.model)
        results = run_decisions(client, items)
    else:
        client = LLM(api_key, args.model, args.reasoning_effort)
        results = run_llm(client, items, args.threads)

    n_opt = len(items[0]["options"])
    correct = [r["picked"] == r["answer"] for r in results]
    print(f"model {args.model}   {len(results)} groups   {n_opt} options each (chance {1 / n_opt:.0%})")
    print(f"category named correctly: {sum(correct)}/{len(correct)} ({pct(sum(correct) / len(correct))})")
    for color in ("yellow", "green", "blue", "purple"):
        c = [r["picked"] == r["answer"] for r in results if r["color"] == color]
        if c:
            print(f"  {color:<7} {sum(c)}/{len(c)}")
    ranks = [r["rank_of_answer"] for r in results if r["rank_of_answer"]]
    if ranks:
        print(f"mean rank of the right name: {statistics.mean(ranks):.2f} of {n_opt}")
    unanswered = sum(r["picked"] is None for r in results)
    if unanswered:
        print(f"unparseable replies: {unanswered}")
    print("\nmissed:")
    for r in results:
        if r["picked"] != r["answer"]:
            print(f"  {r['puzzle_id']:>4} {r['color'] or '':<7} {', '.join(r['words'])}\n"
                  f"       answer: {r['answer']}   picked: {r['picked']}")
    print(f"\n{client.totals.line()}")
    print(f"saved {save_run('categories', args.model, {'model': args.model, 'decoys': args.decoys, 'seed': args.seed, 'accuracy': sum(correct) / len(correct), 'cost_usd': client.totals.cost_usd, 'results': results})}")


if __name__ == "__main__":
    main()
