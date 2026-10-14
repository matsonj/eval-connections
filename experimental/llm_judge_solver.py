"""Same solver, LLM as the judge.

Runs decisions_classic_solver.py unchanged, but every typed question (pair Score, set Score) is answered by a
chat LLM instead of a decisions model. The method is held constant, so the difference against a decisions model
is the judge alone. Questions are batched (64 per call by default, like the solver) and answered as JSON ratings
on the same 0-2 scale; decimals are allowed so the LLM can express uncertainty the way a Score's
probability-weighted mean does.

    uv run python experimental/llm_judge_solver.py --model anthropic/claude-haiku-4.5
    uv run python experimental/llm_judge_solver.py --model google/gemini-2.5-flash --batch-size 32 --threads 10
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decisions_classic_solver as solver  # noqa: E402
from _common import LLM, extract_json, load_api_key  # noqa: E402


class LLMScoreClient:
    """Drop-in for solver.DecisionsClient: ask(state, questions) -> (answers, solver.Usage)."""

    def __init__(self, llm: LLM, batch_size: int):
        self.llm = llm
        self.batch_size = batch_size
        self.missing = 0

    def _prompt(self, state: dict, questions: dict[str, dict]) -> str:
        levels = next(iter(questions.values()))["criteria"]
        scale = "\n".join(f"{i} = {c['summary']}" for i, c in enumerate(levels))
        body = {name: q["instructions"] for name, q in questions.items()}
        return (
            "You are answering typed judgment questions about a NYT Connections puzzle.\n\n"
            f"State:\n{json.dumps(state)}\n\n"
            f"Rate every question on this scale:\n{scale}\n"
            f"You may use decimals between 0 and {len(levels) - 1} to express uncertainty.\n\n"
            f"Questions:\n{json.dumps(body)}\n\n"
            'Reply with only a JSON object mapping every question name to its rating, e.g. {"p0": 1.5, "p1": 0}.'
        )

    def _rate(self, state: dict, questions: dict[str, dict]) -> dict[str, float]:
        top = len(next(iter(questions.values()))["criteria"]) - 1
        try:
            parsed = extract_json(self.llm.complete(self._prompt(state, questions)))
        except Exception as exc:  # noqa: BLE001 - treat as unanswered; retried below
            print(f"llm call failed: {exc}", file=sys.stderr)
            parsed = None
        out = {}
        if isinstance(parsed, dict):
            for name in questions:
                try:
                    out[name] = min(max(float(parsed[name]), 0.0), float(top))
                except (KeyError, TypeError, ValueError):
                    pass
        return out

    def ask(self, state: dict, questions: dict[str, dict]):
        names = list(questions)
        ratings: dict[str, float] = {}
        for start in range(0, len(names), self.batch_size):
            chunk = {n: questions[n] for n in names[start:start + self.batch_size]}
            got = self._rate(state, chunk)
            missing = {n: chunk[n] for n in chunk if n not in got}
            if missing:
                got.update(self._rate(state, missing))
            ratings.update(got)
        lost = [n for n in names if n not in ratings]
        self.missing += len(lost)
        for n in lost:
            ratings[n] = 0.0  # unanswered counts as unrelated; reported below
        return {n: {"score": v} for n, v in ratings.items()}, solver.Usage()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", required=True, help="an OpenRouter chat model, e.g. anthropic/claude-haiku-4.5")
    p.add_argument("--puzzle-ids", type=lambda s: [int(x) for x in s.split(",")], default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--threads", type=int, default=10)
    p.add_argument("--batch-size", type=int, default=64)
    p.add_argument("--reasoning-effort", default=None)
    p.add_argument("--max-tokens", type=int, default=4096)
    args = p.parse_args()

    puzzles = solver.load_puzzles(args.puzzle_ids, canonical_only=not args.all)
    llm = LLM(load_api_key(), args.model, args.reasoning_effort, args.max_tokens)
    client = LLMScoreClient(llm, args.batch_size)
    started = time.perf_counter()
    results = solver.play_all(client, puzzles, args.seed, args.threads)
    wall = time.perf_counter() - started

    label = f"{args.model} (as judge in decisions solver)"
    solver.print_report(results, wall, label, 0.0)
    print(f"llm usage:            {llm.totals.line()}")
    if client.missing:
        print(f"unanswered questions: {client.missing} (scored as 0)")
    path = solver.save_results(results, f"judge_{args.model.split('/')[-1]}", args.seed, 0.0)
    data = json.loads(path.read_text())
    data.update(judge_model=args.model, method="decisions_classic_solver with LLM judge",
                llm_cost_usd=llm.totals.cost_usd, llm_calls=llm.totals.calls, unanswered_questions=client.missing,
                batch_size=args.batch_size, wall_seconds=wall)
    path.write_text(json.dumps(data, indent=1))
    print(f"saved:                {path}")


if __name__ == "__main__":
    main()
