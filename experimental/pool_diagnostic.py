"""Where does the classic solver lose a model: building the candidate pool (pairs) or picking from it (sets)?

First turn of each puzzle only, no feedback. For each model:
  1. Ask the 120 pair questions, rank all 1,820 four-word sets by pair sum (exactly as the solver does).
  2. Pool recall: how many of the 4 true groups land in the top-K pool the solver sends for a direct set score.
  3. Ask the direct set question on the pool PLUS any true groups that missed it (so every true group gets scored).
  4. Report how often the set score alone, and the solver's blend, would put a true group first
     - over the real pool (what the solver does) and
     - over pool + truth (what it would do if the pool always held the answer).

    uv run python experimental/pool_diagnostic.py --model cloudflare/clef --seed 42
"""
import argparse
import itertools
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decisions_classic_solver as solver  # noqa: E402
from _common import save_run  # noqa: E402


PROBE_BLURB = "NYT Connections: a group is four words that share one specific connection."
PROBE_QUESTION = ("Do these four words share one specific connection, the way the four words "
                  "in a single NYT Connections group do?")
PROBE_LEVELS = ["No: they do not form one group", "Yes: they form one group"]


def ask_sets(client, words, sets, wording: str, batch: int) -> dict:
    """Direct set scores in [0, 1], keyed by set. wording: the solver's question or the relatedness probe's."""
    out = {}
    for start in range(0, len(sets), batch):
        chunk = sets[start:start + batch]
        if wording == "probe":
            state = {"puzzle": PROBE_BLURB, "words": list(words)}
            qs = {f"s{i}": {"type": "score", "instructions": {"task": PROBE_QUESTION, "words": list(w)},
                            "criteria": [{"summary": lv} for lv in PROBE_LEVELS]} for i, w in enumerate(chunk)}
            n_levels = len(PROBE_LEVELS)
        else:
            state, qs, n_levels = solver.puzzle_state(words), solver.set_questions(chunk), len(solver.RELATEDNESS_LEVELS)
        answers, _ = client.ask(state, qs)
        out.update({w: answers[f"s{i}"]["score"] / (n_levels - 1) for i, w in enumerate(chunk)})
    return out


def one(client, puzzle, seed: int, top_k: int, wording: str = "solver", batch: int = 64) -> dict:
    rng = random.Random(seed * 1000 + puzzle.id)
    words = list(puzzle.words)
    rng.shuffle(words)
    affinity, _ = solver.pair_affinities(client, words)
    pair_scores = {w: solver.pair_sum(w, affinity) for w in itertools.combinations(sorted(words), 4)}
    ranked = sorted(pair_scores, key=pair_scores.get, reverse=True)
    truth = [tuple(sorted(g)) for g in puzzle.groups]
    pair_rank = {t: ranked.index(t) + 1 for t in truth}
    pool = ranked[:top_k]
    extra = [t for t in truth if t not in pool]
    asked = pool + extra
    rng.shuffle(asked)
    direct = ask_sets(client, words, asked, wording, batch)
    blend = {w: pair_scores[w] / solver.PAIRS_PER_SET + direct[w] for w in asked}

    def first_is_true(scores: dict, cands) -> bool:
        return max(cands, key=scores.get) in truth

    # rank of each true group by direct score among pool + truth (1 = best)
    by_direct = sorted(asked, key=direct.get, reverse=True)
    return {
        "puzzle_id": puzzle.id,
        "pair_rank": {"|".join(t): pair_rank[t] for t in truth},
        "in_pool": sum(1 for t in truth if t in pool),
        "best_pair_rank": min(pair_rank.values()),
        "set_rank_with_truth": {"|".join(t): by_direct.index(t) + 1 for t in truth},
        "direct_true_mean": statistics.mean(direct[t] for t in truth),
        "direct_false_mean": statistics.mean(direct[w] for w in pool if w not in truth) if any(w not in truth for w in pool) else None,
        "pick_pool_set": first_is_true(direct, pool),
        "pick_pool_blend": first_is_true(blend, pool),
        "pick_truth_set": first_is_true(direct, asked),
        "pick_truth_blend": first_is_true(blend, asked),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default="typesafe/jev-1.13")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--top-k", type=int, default=solver.CANDIDATE_SETS_FOR_DIRECT_SCORE)
    p.add_argument("--threads", type=int, default=10)
    p.add_argument("--set-wording", choices=["solver", "probe"], default="solver",
                   help="ask the direct set question in the solver's wording or the relatedness probe's")
    p.add_argument("--set-batch", type=int, default=64, help="set questions per request (64 = as the solver sends them)")
    p.add_argument("--puzzle-ids", type=lambda s: [int(x) for x in s.split(",")], default=None)
    args = p.parse_args()
    puzzles = solver.load_puzzles(args.puzzle_ids, canonical_only=True)
    api_key = solver.load_api_key()
    price = solver.price_per_input_token(api_key, args.model)
    client = solver.DecisionsClient(api_key, args.model)
    started = time.perf_counter()

    def safe(pz):
        try:
            return one(client, pz, args.seed, args.top_k, args.set_wording, args.set_batch)
        except Exception as exc:  # noqa: BLE001
            print(f"puzzle {pz.id} failed: {exc}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        rows = [r for r in pool.map(safe, puzzles) if r]
    n = len(rows)
    share = lambda k: sum(r[k] for r in rows) / n
    print(f"model {args.model}   seed {args.seed}   puzzles {n}   top-k {args.top_k}   "
          f"set wording {args.set_wording}   set batch {args.set_batch}   wall {time.perf_counter() - started:.0f}s")
    print(f"true groups in the top-{args.top_k} pool:       {sum(r['in_pool'] for r in rows)}/{4 * n}")
    print(f"median best true-group pair rank:    {statistics.median(r['best_pair_rank'] for r in rows)} of 1820")
    print(f"first guess right, solver as is:     blend {share('pick_pool_blend'):.0%}   set score alone {share('pick_pool_set'):.0%}")
    print(f"first guess right, truth in pool:    blend {share('pick_truth_blend'):.0%}   set score alone {share('pick_truth_set'):.0%}")
    dt = statistics.mean(r["direct_true_mean"] for r in rows)
    df = statistics.mean(r["direct_false_mean"] for r in rows if r["direct_false_mean"] is not None)
    print(f"mean set score: true groups {dt:.2f}   other pool sets {df:.2f}")
    name = "pool" if (args.set_wording, args.set_batch) == ("solver", 64) else f"pool-{args.set_wording}-b{args.set_batch}"
    print(f"saved {save_run(name,
                        args.model, {'model': args.model, 'seed': args.seed, 'top_k': args.top_k,
                        'set_wording': args.set_wording, 'set_batch': args.set_batch, 'price': price, 'rows': rows})}")


if __name__ == "__main__":
    main()
