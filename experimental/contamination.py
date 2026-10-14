"""Contamination check: has the model memorized these specific puzzles?

Every puzzle in connections_puzzles.yml is dated 2025-09-30 or earlier, before the training cutoff of every
current model, so a before/after-cutoff split is not possible with this file. This script tests memory
directly instead. For each real group (the "hidden" group) the model is told which published puzzle it is and
shown the other three groups, then asked which of several four-word sets is the missing group. The decoys are
real groups from other puzzles that share no word with this board, so every option is equally "related" and
only memory (or a leak from the other three groups) can pick the right one. Chance is 1/(decoys+1).

Three conditions separate the signals:

  full         puzzle number and date + the other three groups (names and words)
  groups_only  the other three groups, no number or date
  blind        nothing: which of these is a Connections group? (should sit at chance)

full well above groups_only means the number and date are doing work: memorization.
groups_only above blind can be memorization of the board or ordinary thematic leakage.

For LLMs, --free-recall also asks, in the full condition, for the four missing words with no options at all;
exact matches are hard to explain without memorization.

    uv run python experimental/contamination.py --model typesafe/jev-1.13
    uv run python experimental/contamination.py --model anthropic/claude-haiku-4.5 --free-recall
"""
import argparse
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (LLM, Decisions, all_raw_puzzles, extract_json, is_decisions_model, load_api_key,  # noqa: E402
                     load_raw_puzzles, normalized, parse_letter, pct, save_run, score_question, upper_words)

CONDITIONS = ("full", "groups_only", "blind")
TASK = "Judge whether these four words are the missing fourth group of the puzzle described in the state."
LEVELS = ["No: not the missing group", "Yes: the missing group"]
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def build_items(puzzles: list[dict], decoys: int, rng: random.Random) -> list[dict]:
    pool = [(p["id"], g["name"], upper_words(g["words"])) for p in all_raw_puzzles() for g in p["groups"]]
    items = []
    for p in puzzles:
        board = set(upper_words(p["words"]))
        # No shared words, and no board word in the decoy's category name ("Named APOLLO" vs a board with APOLLO).
        outside = [words for pid, name, words in pool if pid != p["id"] and not board & set(words)
                   and not any(b in name.upper() for b in board)]
        for h, hidden in enumerate(p["groups"]):
            others = [{"category": g["name"], "words": upper_words(g["words"])}
                      for k, g in enumerate(p["groups"]) if k != h]
            options = [upper_words(hidden["words"])] + [list(w) for w in rng.sample(outside, min(decoys, len(outside)))]
            for o in options:
                rng.shuffle(o)
            rng.shuffle(options)
            items.append({"puzzle_id": p["id"], "date": str(p["date"]), "color": hidden.get("color"),
                          "hidden_category": hidden["name"], "answer": sorted(upper_words(hidden["words"])),
                          "others": others, "options": options})
    return items


def context(it: dict, condition: str) -> dict:
    if condition == "blind":
        return {"puzzle": "A published NYT Connections puzzle; one of its four groups of four words is missing."}
    state = {"puzzle": "A published NYT Connections puzzle with one of its four groups missing.",
             "other_groups": it["others"]}
    if condition == "full":
        state = {"puzzle": f"NYT Connections puzzle #{it['puzzle_id']}, published {it['date']}, "
                           "with one of its four groups missing.", "other_groups": it["others"]}
    return state


def context_text(it: dict, condition: str) -> str:
    s = context(it, condition)
    lines = [s["puzzle"]]
    for g in s.get("other_groups", []):
        lines.append(f"- {g['category']}: {', '.join(g['words'])}")
    return "\n".join(lines)


def run_decisions(dec: Decisions, items, condition: str, threads: int) -> list[int | None]:
    def one(it):
        qs = {f"o{j}": score_question(TASK, LEVELS, words=o) for j, o in enumerate(it["options"])}
        try:
            ans = dec.ask(context(it, condition), qs)
        except Exception as exc:  # noqa: BLE001
            print(f"request failed: {exc}", file=sys.stderr)
            return None
        ps = [normalized(ans[f"o{j}"], len(LEVELS)) for j in range(len(it["options"]))]
        return max(range(len(ps)), key=ps.__getitem__)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        return list(pool.map(one, items))


def run_llm(llm: LLM, items, condition: str, threads: int) -> list[int | None]:
    def one(it):
        opts = "\n".join(f"{LETTERS[j]}. {', '.join(o)}" for j, o in enumerate(it["options"]))
        question = ("Which of these is one of its four-word groups?" if condition == "blind"
                    else "Which of these is the missing group?")
        prompt = f"{context_text(it, condition)}\n\n{question}\n{opts}\n\nReply with only the letter."
        try:
            return parse_letter(llm.complete(prompt), len(it["options"]))
        except Exception as exc:  # noqa: BLE001
            print(f"llm call failed: {exc}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(max_workers=threads) as pool:
        return list(pool.map(one, items))


def free_recall(llm: LLM, items, threads: int) -> list[int]:
    def one(it):
        prompt = (f"{context_text(it, 'full')}\n\nFrom memory of this published puzzle, what were the four words "
                  'of the missing group? Reply with only JSON: {"words": ["A", "B", "C", "D"]}')
        try:
            parsed = extract_json(llm.complete(prompt))
            guessed = {str(w).strip().upper() for w in (parsed or {}).get("words", [])}
        except Exception:  # noqa: BLE001
            guessed = set()
        return len(guessed & set(it["answer"]))

    with ThreadPoolExecutor(max_workers=threads) as pool:
        return list(pool.map(one, items))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default="typesafe/jev-1.13")
    p.add_argument("--kind", choices=["auto", "decisions", "llm"], default="auto")
    p.add_argument("--decoys", type=int, default=5)
    p.add_argument("--puzzle-ids", type=lambda s: [int(x) for x in s.split(",")], default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--free-recall", action="store_true", help="llm only")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--threads", type=int, default=10)
    p.add_argument("--reasoning-effort", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    items = build_items(load_raw_puzzles(args.puzzle_ids, args.all), args.decoys, random.Random(args.seed))
    if args.dry_run:
        it = items[0]
        print(f"{len(items)} hidden groups, {len(it['options'])} options (chance {1 / len(it['options']):.0%})\n")
        print(context_text(it, "full"))
        for j, o in enumerate(it["options"]):
            print(f"{LETTERS[j]}. {', '.join(o)}{'   <- answer' if sorted(o) == it['answer'] else ''}")
        return

    api_key = load_api_key()
    decisions = is_decisions_model(args.model, args.kind)
    client = Decisions(api_key, args.model) if decisions else LLM(api_key, args.model, args.reasoning_effort)
    chance = 1 / (args.decoys + 1)
    print(f"model {args.model}   {len(items)} hidden groups   chance {chance:.0%}")
    out = {"model": args.model, "decoys": args.decoys, "seed": args.seed, "conditions": {}}
    for cond in CONDITIONS:
        picks = (run_decisions if decisions else run_llm)(client, items, cond, args.threads)
        hits = [k is not None and sorted(it["options"][k]) == it["answer"] for it, k in zip(items, picks)]
        by_color = {c: sum(h for h, it in zip(hits, items) if it["color"] == c) for c in ("yellow", "green", "blue", "purple")}
        print(f"{cond:<12} {sum(hits):>3}/{len(hits)} ({pct(sum(hits) / len(hits))})   "
              + "  ".join(f"{c} {n}" for c, n in by_color.items()))
        out["conditions"][cond] = {"accuracy": sum(hits) / len(hits), "by_color": by_color,
                                   "picks": [it["options"][k] if k is not None else None for it, k in zip(items, picks)]}
    if args.free_recall and not decisions:
        matches = free_recall(client, items, args.threads)
        exact = sum(m == 4 for m in matches)
        print(f"free recall  all four words: {exact}/{len(items)}   mean words recalled: {sum(matches) / len(matches):.2f} of 4")
        out["free_recall"] = {"exact": exact, "matches": matches}
    print(f"\n{client.totals.line()}")
    out["cost_usd"] = client.totals.cost_usd
    out["items"] = items
    print(f"saved {save_run('contamination', args.model, out)}")


if __name__ == "__main__":
    main()
