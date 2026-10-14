"""Batch scaling: how does a decisions model's latency grow with questions per request?

Sends the solver's own pair questions (from one puzzle board) in requests of 1, 4, 16, 32 and 64 questions,
serially, several times each, and reports median and p90 latency, ms per question and cost per question.
Serial requests isolate the model; --concurrency N also measures throughput with N requests in flight.

The classic solver sends up to 64 questions per request, so a model whose latency grows with batch size
looks slower there than on OpenRouter's latency page. This puts a number on that.

    uv run python experimental/batch_scaling.py                                 # all six known decisions models
    uv run python experimental/batch_scaling.py --models typesafe/jev-1.13 --reps 10
    uv run python experimental/batch_scaling.py --models typesafe/jev-1.13 --concurrency 20
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
from _common import KNOWN_DECISIONS_MODELS, Decisions, load_api_key, load_raw_puzzles, save_run  # noqa: E402


def p90(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--models", default=",".join(KNOWN_DECISIONS_MODELS))
    p.add_argument("--sizes", default="1,4,16,32,64")
    p.add_argument("--reps", type=int, default=5)
    p.add_argument("--puzzle-id", type=int, default=246)
    p.add_argument("--concurrency", type=int, default=0, help="also measure throughput with N requests in flight")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    sizes = [int(s) for s in args.sizes.split(",")]
    if max(sizes) > 64:
        sys.exit("64 is the most questions per request every model accepts")
    raw = load_raw_puzzles([args.puzzle_id])[0]
    words = [w.upper() for w in raw["words"]]
    random.Random(args.seed).shuffle(words)
    state = solver.puzzle_state(words)
    pairs = list(itertools.combinations(words, 2))
    api_key = load_api_key()

    rows = []
    for model in args.models.split(","):
        dec = Decisions(api_key, model)
        print(f"\n{model}")
        print(f"{'questions':>9} {'median ms':>10} {'p90 ms':>8} {'ms/question':>12} {'$/1k questions':>15}")
        dec.ask(state, solver.pair_questions(pairs[:1]))  # warm-up, not counted
        for n in sizes:
            qs = solver.pair_questions(pairs[:n])
            times, cost_before = [], dec.totals.cost_usd
            for _ in range(args.reps):
                started = time.perf_counter()
                try:
                    dec.ask(state, qs)
                    times.append((time.perf_counter() - started) * 1000)
                except Exception as exc:  # noqa: BLE001
                    print(f"  request failed: {exc}", file=sys.stderr)
            if not times:
                continue
            per_q_cost = (dec.totals.cost_usd - cost_before) / (len(times) * n)
            med = statistics.median(times)
            row = {"model": model, "questions": n, "median_ms": med, "p90_ms": p90(times),
                   "ms_per_question": med / n, "usd_per_1k_questions": per_q_cost * 1000, "reps": len(times)}
            rows.append(row)
            print(f"{n:>9} {med:>10.0f} {row['p90_ms']:>8.0f} {med / n:>12.1f} {per_q_cost * 1000:>15.4f}")
        if args.concurrency:
            qs = solver.pair_questions(pairs[:64])
            total = args.concurrency * 2
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                ok = sum(1 for r in pool.map(lambda _: _try(dec, state, qs), range(total)) if r)
            wall = time.perf_counter() - started
            print(f"throughput at {args.concurrency} in flight (64-question requests): "
                  f"{ok / wall:.1f} req/s, {ok * 64 / wall:.0f} questions/s, {total - ok} failed")
            rows.append({"model": model, "concurrency": args.concurrency, "requests_per_sec": ok / wall,
                         "questions_per_sec": ok * 64 / wall, "failed": total - ok})
    print(f"\nsaved {save_run('batch_scaling', 'all' if ',' in args.models else args.models, {'rows': rows})}")


def _try(dec: Decisions, state: dict, qs: dict) -> bool:
    try:
        dec.ask(state, qs)
        return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    main()
