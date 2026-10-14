"""Hybrid solvers: a decisions model and an LLM splitting the work, on the classic-mode rules.

  --mode filter    The decisions model runs the full solver scoring (pair sums, exact feedback rules, direct
                   set Score blend) and passes its top K sets to the LLM, which picks the guess. The LLM sees
                   the remaining words and the guess history with feedback. Cheap judge narrows, LLM decides.
  --mode propose   The LLM proposes up to N candidate groups with category names. Candidates the feedback
                   rules out are dropped, and the decisions model scores the rest ("do these four words form
                   this category?"). The best-scoring proposal is guessed. LLM generates, judge verifies.

If the LLM reply cannot be used, the turn falls back to the plain decisions solver and the fallback is counted.
Compare against decisions_classic_solver.py (judge alone) and the main harness classic run (LLM alone).

    uv run python experimental/hybrid_solver.py --mode filter  --decider typesafe/jev-1.13 --llm anthropic/claude-haiku-4.5
    uv run python experimental/hybrid_solver.py --mode propose --decider typesafe/jev-1.13 --llm anthropic/claude-haiku-4.5
"""
import argparse
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decisions_classic_solver as solver  # noqa: E402
from _common import LLM, extract_json, load_api_key  # noqa: E402

PROPOSE_SET_TASK = ("Judge whether these four words together form one of the real groups in this Connections "
                    "puzzle, with the given category as their connection.")


class Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.fallbacks = 0
        self.llm_turns = 0

    def bump(self, fallback: bool) -> None:
        with self.lock:
            self.llm_turns += 1
            self.fallbacks += fallback


def history_text(turns: list[solver.Turn], mistakes: int) -> str:
    if not turns:
        return "No guesses yet. You have 4 mistakes available."
    lines = [f"- {', '.join(t.guess)} -> {t.result}" for t in turns]
    return "Guesses so far:\n" + "\n".join(lines) + f"\nMistakes remaining: {solver.MAX_MISTAKES - mistakes}."


def choose_filter(dec, llm: LLM, counters: Counters, k: int, remaining, feedback, turns, mistakes, rng):
    usage = solver.Usage()
    if len(remaining) == solver.GROUP_SIZE:
        return tuple(remaining), usage
    shuffled = remaining[:]
    rng.shuffle(shuffled)
    affinity, u = solver.pair_affinities(dec, shuffled)
    usage.add(u)
    pair_scores = solver.score_candidate_sets(remaining, affinity, feedback)
    if len(pair_scores) == 1:
        return next(iter(pair_scores)), usage
    blended, u = solver.blend_with_direct_scores(dec, shuffled, pair_scores, rng)
    usage.add(u)
    top = sorted(blended, key=blended.get, reverse=True)[:k]
    shown = top[:]
    rng.shuffle(shown)  # hide the judge's ranking so the LLM picks on content, not position
    options = "\n".join(f"{i + 1}. {', '.join(words)}" for i, words in enumerate(shown))
    prompt = (
        "You are playing NYT Connections: sixteen words hide four groups of four that share a specific "
        "connection. Guess one group at a time; a wrong guess costs a mistake, and ONE AWAY means three of the "
        "four words are in one group.\n\n"
        f"Remaining words: {', '.join(shuffled)}\n{history_text(turns, mistakes)}\n\n"
        f"A helper shortlisted these candidate groups:\n{options}\n\n"
        "Pick the one most likely to be a real group. Reply with only its number."
    )
    choice = None
    try:
        reply = llm.complete(prompt)
        digits = [int(t) for t in "".join(c if c.isdigit() else " " for c in reply).split()]
        choice = next((d for d in reversed(digits) if 1 <= d <= len(shown)), None)
    except Exception as exc:  # noqa: BLE001
        print(f"llm call failed: {exc}", file=sys.stderr)
    counters.bump(fallback=choice is None)
    return (shown[choice - 1] if choice else top[0]), usage


def choose_propose(dec, llm: LLM, counters: Counters, n: int, remaining, feedback, turns, mistakes, rng):
    usage = solver.Usage()
    if len(remaining) == solver.GROUP_SIZE:
        return tuple(remaining), usage
    shuffled = remaining[:]
    rng.shuffle(shuffled)
    prompt = (
        "You are playing NYT Connections: the words hide groups of four that share a specific connection "
        "(categories are often wordplay: fill-in-the-blank, hidden words, homophones). ONE AWAY means three of "
        "the four guessed words are in one group.\n\n"
        f"Remaining words: {', '.join(shuffled)}\n{history_text(turns, mistakes)}\n\n"
        f"Propose up to {n} different candidate groups, each exactly four of the remaining words, most likely "
        "first. Do not repeat a wrong guess.\n"
        'Reply with only JSON: [{"category": "...", "words": ["A", "B", "C", "D"]}, ...]'
    )
    valid: list[tuple[tuple[str, ...], str]] = []
    try:
        parsed = extract_json(llm.complete(prompt))
    except Exception as exc:  # noqa: BLE001
        print(f"llm call failed: {exc}", file=sys.stderr)
        parsed = None
    remaining_set = set(remaining)
    seen = set()
    for prop in parsed if isinstance(parsed, list) else []:
        try:
            words = frozenset(str(w).strip().upper() for w in prop["words"])
            category = str(prop.get("category", ""))
        except (KeyError, TypeError):
            continue
        if len(words) == 4 and words <= remaining_set and words not in seen \
                and solver.consistent_with_feedback(words, feedback):
            seen.add(words)
            valid.append((tuple(sorted(words)), category))
    counters.bump(fallback=not valid)
    if not valid:
        return solver.choose_guess(dec, remaining, feedback, rng)
    if len(valid) == 1:
        return valid[0][0], usage
    questions = {f"s{i}": solver.relatedness_question(PROPOSE_SET_TASK, words=list(w), category=c)
                 for i, (w, c) in enumerate(valid)}
    answers, u = dec.ask(solver.puzzle_state(shuffled), questions)
    usage.add(u)
    scores = [solver.normalized_score(answers[f"s{i}"]) + solver.one_away_bonus(frozenset(w), feedback) / 6
              for i, (w, _) in enumerate(valid)]
    return valid[max(range(len(valid)), key=scores.__getitem__)][0], usage


def play(choose, puzzle: solver.Puzzle, seed: int) -> solver.GameResult:
    """solver.play with a pluggable choose() that also sees the guess history."""
    rng = random.Random(seed + puzzle.id)
    remaining = puzzle.words[:]
    rng.shuffle(remaining)
    solved, feedback, turns, usage, mistakes = [], solver.Feedback(), [], solver.Usage(), 0
    started = time.perf_counter()
    while mistakes < solver.MAX_MISTAKES and len(solved) < len(puzzle.groups):
        guess, u = choose(remaining, feedback, turns, mistakes, rng)
        usage.add(u)
        result = solver.judge(guess, puzzle, solved)
        turns.append(solver.Turn(guess=tuple(sorted(guess)), result=result))
        guessed = frozenset(guess)
        if result == "CORRECT":
            solved.append(guessed)
            remaining = [w for w in remaining if w not in guessed]
        else:
            mistakes += 1
            feedback.wrong.append(guessed)
            if result == "INCORRECT - ONE AWAY":
                feedback.one_away.append(guessed)
    return solver.GameResult(puzzle.id, len(solved) == len(puzzle.groups), len(solved), len(turns), mistakes,
                             turns[0].result == "CORRECT", turns, usage, time.perf_counter() - started)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--mode", choices=["filter", "propose"], required=True)
    p.add_argument("--decider", default="typesafe/jev-1.13")
    p.add_argument("--llm", required=True)
    p.add_argument("--top-k", type=int, default=8, help="filter mode: candidates shown to the LLM")
    p.add_argument("--proposals", type=int, default=6, help="propose mode: groups the LLM proposes")
    p.add_argument("--puzzle-ids", type=lambda s: [int(x) for x in s.split(",")], default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--threads", type=int, default=10)
    p.add_argument("--reasoning-effort", default=None)
    args = p.parse_args()

    api_key = load_api_key()
    puzzles = solver.load_puzzles(args.puzzle_ids, canonical_only=not args.all)
    price = solver.price_per_input_token(api_key, args.decider)
    dec = solver.DecisionsClient(api_key, args.decider)
    llm = LLM(api_key, args.llm, args.reasoning_effort)
    counters = Counters()

    def choose(remaining, feedback, turns, mistakes, rng):
        if args.mode == "filter":
            return choose_filter(dec, llm, counters, args.top_k, remaining, feedback, turns, mistakes, rng)
        return choose_propose(dec, llm, counters, args.proposals, remaining, feedback, turns, mistakes, rng)

    def safe_play(puzzle):
        started = time.perf_counter()
        try:
            return play(choose, puzzle, args.seed)
        except Exception as exc:  # noqa: BLE001
            return solver.GameResult(puzzle.id, False, 0, 0, 0, False, [], solver.Usage(),
                                     time.perf_counter() - started, error=f"{type(exc).__name__}: {exc}")

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        results = sorted(pool.map(safe_play, puzzles), key=lambda r: r.puzzle_id)
    wall = time.perf_counter() - started

    label = f"hybrid-{args.mode}: {args.decider} + {args.llm}"
    solver.print_report(results, wall, label, price)
    decider_cost = sum(r.usage.input_tokens for r in results) * price
    print(f"llm usage:            {llm.totals.line()}")
    print(f"llm turns:            {counters.llm_turns}   fell back to decisions solver: {counters.fallbacks}")
    print(f"total cost:           ${decider_cost + llm.totals.cost_usd:.4f} "
          f"(decider ${decider_cost:.4f} + llm ${llm.totals.cost_usd:.4f})")
    path = solver.save_results(results, f"hybrid-{args.mode}_{args.llm.split('/')[-1]}", args.seed, price)
    data = json.loads(path.read_text())
    data.update(method=f"hybrid {args.mode}", decider=args.decider, llm=args.llm, llm_cost_usd=llm.totals.cost_usd,
                decider_cost_usd=decider_cost, llm_turns=counters.llm_turns, fallbacks=counters.fallbacks,
                wall_seconds=wall, top_k=args.top_k, proposals=args.proposals)
    path.write_text(json.dumps(data, indent=1))
    print(f"saved:                {path}")


if __name__ == "__main__":
    main()
