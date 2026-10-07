"""Play NYT Connections in classic mode with a decisions model (Jev, GPT-6 Luna, ...) through OpenRouter.

A decisions model never writes text: it takes a state plus typed questions and returns probabilities. So
this script plays the game and the model only judges relatedness. Each turn:

  1. Ask the model to score every pair of remaining words (0 unrelated .. 2 same group), in one request.
  2. Rank every four-word set by the sum of its six pair scores.
  3. Drop sets the feedback so far rules out, and boost sets that complete a ONE AWAY guess.
  4. Ask the model to score the top 60 sets directly ("do these four form a group?"), in one request.
  5. Blend the two scores and guess the best set; our code judges it against the answer key.

Every model gets identical questions and rules. Only --model changes.

    uv run python experimental/decisions_classic_solver.py                                  # GPT-6 Luna Decisions
    uv run python experimental/decisions_classic_solver.py --model typesafe/jev-1.13
    uv run python experimental/decisions_classic_solver.py --model typesafe/jev-1.13 --seed 43 --puzzle-ids 246,304
"""
import argparse
import itertools
import json
import os
import random
import re
import statistics
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PUZZLES_FILE = REPO_ROOT / "inputs" / "connections_puzzles.yml"
ENV_FILE = REPO_ROOT / ".env"
OUTPUT_DIR = REPO_ROOT / "experimental" / "runs"

API_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL_ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"
DEFAULT_MODEL = "openai/gpt-6-luna-decisions"
REQUEST_TIMEOUT_SEC = 120
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504, 529}
MAX_ATTEMPTS = 5
BACKOFF_BASE_SEC = 1.0
BACKOFF_MAX_SEC = 30.0

MAX_MISTAKES = 4
GROUP_SIZE = 4
PAIRS_PER_SET = 6
# Tuned on Jev, 2026-09-17: more candidates or a different bonus did not help.
CANDIDATE_SETS_FOR_DIRECT_SCORE = 60
ONE_AWAY_BONUS = 1.0

PUZZLE_BLURB = "NYT Connections: the sixteen words form four hidden groups of four related words each."
PAIR_TASK = "Judge whether the two words belong to the same group of four in this Connections puzzle."
SET_TASK = "Judge whether these four words together form one of the real groups in this Connections puzzle."
RELATEDNESS_LEVELS = [
    "Unrelated, or only coincidentally related",
    "Loosely related; could share a broad theme",
    "Clearly members of the same specific four-word category",
]


Words = tuple[str, ...]
WordSet = frozenset[str]


@dataclass
class Puzzle:
    id: int
    words: list[str]
    groups: list[WordSet]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    http_ms: list[float] = field(default_factory=list)

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        self.http_ms.extend(other.http_ms)


@dataclass
class Feedback:
    wrong: list[WordSet] = field(default_factory=list)
    one_away: list[WordSet] = field(default_factory=list)

    @property
    def plain_wrong(self) -> list[WordSet]:
        return [g for g in self.wrong if g not in self.one_away]


@dataclass
class Turn:
    guess: Words
    result: str


@dataclass
class GameResult:
    puzzle_id: int
    won: bool
    groups_found: int
    guesses: int
    mistakes: int
    first_guess_correct: bool
    turns: list[Turn]
    usage: Usage
    seconds: float
    error: str | None = None


def load_puzzles(only_ids: Sequence[int] | None, canonical_only: bool) -> list[Puzzle]:
    data = yaml.safe_load(PUZZLES_FILE.read_text())
    puzzles = []
    for raw in data["puzzles"]:
        if only_ids is not None and raw["id"] not in only_ids:
            continue
        if only_ids is None and canonical_only and not raw.get("canonical"):
            continue
        puzzles.append(Puzzle(
            id=raw["id"],
            words=[w.upper() for w in raw["words"]],
            groups=[frozenset(w.upper() for w in g["words"]) for g in raw["groups"]],
        ))
    return puzzles


def load_api_key() -> str:
    from_env = os.getenv("OPENROUTER_API_KEY")
    if from_env:
        return from_env.strip()
    if ENV_FILE.exists():
        match = re.search(r"^\s*(?:export\s+)?OPENROUTER_API_KEY=[\"']?([^\"'\r\n]+)", ENV_FILE.read_text(), re.MULTILINE)
        if match:
            return match.group(1).strip()
    sys.exit(f"No OpenRouter API key: set OPENROUTER_API_KEY or add it to {ENV_FILE}")


def price_per_input_token(api_key: str, model: str) -> float:
    response = requests.get(MODEL_ENDPOINTS_URL.format(model=model),
                            headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    response.raise_for_status()
    return float(response.json()["data"]["endpoints"][0]["pricing"]["prompt"])


def relatedness_question(task: str, **fields) -> dict:
    return {
        "type": "score",
        "instructions": {"task": task, **fields},
        "criteria": [{"summary": level} for level in RELATEDNESS_LEVELS],
    }


def pair_questions(words: Sequence[str]) -> dict[str, dict]:
    return {
        f"{a}|{b}": relatedness_question(PAIR_TASK, word_a=a, word_b=b)
        for a, b in itertools.combinations(words, 2)
    }


def set_questions(candidate_sets: Sequence[Words]) -> dict[str, dict]:
    return {
        "|".join(words): relatedness_question(SET_TASK, words=list(words))
        for words in candidate_sets
    }


def puzzle_state(words: Sequence[str]) -> dict:
    return {"puzzle": PUZZLE_BLURB, "words": list(words)}


class DecisionsClient:
    def __init__(self, api_key: str, model: str):
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.model = model

    def ask(self, state: dict, questions: dict[str, dict]) -> tuple[dict[str, dict], Usage]:
        body = {"model": self.model, "state": state, "questions": questions}
        usage = Usage()
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            started = time.perf_counter()
            try:
                response = requests.post(API_URL, json=body, headers=self.headers, timeout=REQUEST_TIMEOUT_SEC)
            except requests.RequestException as exc:
                response, last_error = None, exc
            usage.http_ms.append((time.perf_counter() - started) * 1000)
            usage.calls += 1
            if response is None:
                time.sleep(backoff_seconds(attempt, None))
                continue
            if response.status_code in RETRYABLE_STATUSES:
                last_error = requests.HTTPError(f"HTTP {response.status_code}", response=response)
                time.sleep(backoff_seconds(attempt, response.headers.get("Retry-After")))
                continue
            response.raise_for_status()
            payload = response.json()
            usage.input_tokens += payload["usage"]["input_tokens"]
            usage.output_tokens += payload["usage"]["output_tokens"]
            return payload["answers"], usage
        raise RuntimeError(f"gave up after {MAX_ATTEMPTS} attempts: {last_error}")


def backoff_seconds(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), BACKOFF_MAX_SEC)
        except ValueError:
            pass
    exponential = min(BACKOFF_BASE_SEC * 2 ** attempt, BACKOFF_MAX_SEC)
    return exponential * random.uniform(0.5, 1.0)


def normalized_score(answer: dict) -> float:
    return answer["score"] / (len(RELATEDNESS_LEVELS) - 1)


def pair_affinities(client: DecisionsClient, words: Sequence[str]) -> tuple[dict[frozenset[str], float], Usage]:
    answers, usage = client.ask(puzzle_state(words), pair_questions(words))
    affinity = {}
    for key, answer in answers.items():
        a, b = key.split("|")
        affinity[frozenset((a, b))] = normalized_score(answer)
    return affinity, usage


def pair_sum(words: Words, affinity: dict[frozenset[str], float]) -> float:
    return sum(affinity[frozenset(pair)] for pair in itertools.combinations(words, 2))


def consistent_with_feedback(candidate: WordSet, feedback: Feedback) -> bool:
    if any(len(candidate & wrong) > 2 for wrong in feedback.plain_wrong):
        return False
    return all(len(candidate & near) in (0, 1, 3) for near in feedback.one_away)


def one_away_bonus(candidate: WordSet, feedback: Feedback) -> float:
    if any(len(candidate & near) == 3 for near in feedback.one_away):
        return ONE_AWAY_BONUS
    return 0.0


def score_candidate_sets(remaining: Sequence[str], affinity: dict[frozenset[str], float],
                         feedback: Feedback) -> dict[Words, float]:
    scored = {}
    for words in itertools.combinations(sorted(remaining), GROUP_SIZE):
        candidate = frozenset(words)
        if consistent_with_feedback(candidate, feedback):
            scored[words] = pair_sum(words, affinity) + one_away_bonus(candidate, feedback)
    return scored


def blend_with_direct_scores(client: DecisionsClient, remaining: Sequence[str], pair_scores: dict[Words, float],
                             rng: random.Random) -> tuple[dict[Words, float], Usage]:
    top = sorted(pair_scores, key=pair_scores.get, reverse=True)[:CANDIDATE_SETS_FOR_DIRECT_SCORE]
    rng.shuffle(top)
    answers, usage = client.ask(puzzle_state(remaining), set_questions(top))
    blended = {}
    for key, answer in answers.items():
        words = tuple(key.split("|"))
        blended[words] = pair_scores[words] / PAIRS_PER_SET + normalized_score(answer)
    return blended, usage


def choose_guess(client: DecisionsClient, remaining: list[str], feedback: Feedback,
                 rng: random.Random) -> tuple[Words, Usage]:
    usage = Usage()
    if len(remaining) == GROUP_SIZE:
        return tuple(remaining), usage
    shuffled = remaining[:]
    rng.shuffle(shuffled)
    affinity, pair_usage = pair_affinities(client, shuffled)
    usage.add(pair_usage)
    pair_scores = score_candidate_sets(remaining, affinity, feedback)
    if len(pair_scores) == 1:
        return next(iter(pair_scores)), usage
    blended, set_usage = blend_with_direct_scores(client, shuffled, pair_scores, rng)
    usage.add(set_usage)
    return max(blended, key=blended.get), usage


def judge(guess: Words, puzzle: Puzzle, solved: list[WordSet]) -> str:
    guessed = frozenset(guess)
    if guessed in puzzle.groups:
        return "CORRECT"
    unsolved = [g for g in puzzle.groups if g not in solved]
    if any(len(guessed & g) == 3 for g in unsolved):
        return "INCORRECT - ONE AWAY"
    return "INCORRECT"


def play(client: DecisionsClient, puzzle: Puzzle, seed: int) -> GameResult:
    rng = random.Random(seed + puzzle.id)
    remaining = puzzle.words[:]
    rng.shuffle(remaining)
    solved: list[WordSet] = []
    feedback = Feedback()
    turns: list[Turn] = []
    usage = Usage()
    mistakes = 0
    started = time.perf_counter()

    while mistakes < MAX_MISTAKES and len(solved) < len(puzzle.groups):
        guess, turn_usage = choose_guess(client, remaining, feedback, rng)
        usage.add(turn_usage)
        result = judge(guess, puzzle, solved)
        turns.append(Turn(guess=tuple(sorted(guess)), result=result))
        guessed = frozenset(guess)
        if result == "CORRECT":
            solved.append(guessed)
            remaining = [w for w in remaining if w not in guessed]
        else:
            mistakes += 1
            feedback.wrong.append(guessed)
            if result == "INCORRECT - ONE AWAY":
                feedback.one_away.append(guessed)

    return GameResult(
        puzzle_id=puzzle.id,
        won=len(solved) == len(puzzle.groups),
        groups_found=len(solved),
        guesses=len(turns),
        mistakes=mistakes,
        first_guess_correct=turns[0].result == "CORRECT",
        turns=turns,
        usage=usage,
        seconds=time.perf_counter() - started,
    )


def play_or_record_failure(client: DecisionsClient, puzzle: Puzzle, seed: int) -> GameResult:
    started = time.perf_counter()
    try:
        return play(client, puzzle, seed)
    except Exception as exc:  # noqa: BLE001 - record the failure, keep the other puzzles running
        return GameResult(
            puzzle_id=puzzle.id, won=False, groups_found=0, guesses=0, mistakes=0,
            first_guess_correct=False, turns=[], usage=Usage(),
            seconds=time.perf_counter() - started, error=f"{type(exc).__name__}: {exc}",
        )


def play_all(client: DecisionsClient, puzzles: list[Puzzle], seed: int, threads: int) -> list[GameResult]:
    with ThreadPoolExecutor(max_workers=threads) as pool:
        results = list(pool.map(lambda p: play_or_record_failure(client, p, seed), puzzles))
    return sorted(results, key=lambda r: r.puzzle_id)


RESULT_SYMBOLS = {"CORRECT": "C", "INCORRECT - ONE AWAY": "~", "INCORRECT": "x"}


def print_report(results: list[GameResult], wall_seconds: float, model: str, price: float) -> None:
    total = Usage()
    for r in results:
        total.add(r.usage)
    n = len(results)
    print(f"model {model}   puzzles {n}   wall {wall_seconds:.1f}s")
    print(f"{'id':>5} {'won':>4} {'groups':>6} {'guesses':>7} {'mistakes':>8} {'first':>5}  transcript")
    for r in results:
        transcript = " | ".join(f"{RESULT_SYMBOLS[t.result]} {','.join(t.guess)}" for t in r.turns)
        if r.error:
            transcript = f"FAILED: {r.error}"
        print(f"{r.puzzle_id:>5} {'yes' if r.won else '-':>4} {r.groups_found:>6} {r.guesses:>7} {r.mistakes:>8} "
              f"{'yes' if r.first_guess_correct else '-':>5}  {transcript}")
    print()
    print(f"puzzles won:          {sum(r.won for r in results)}/{n}")
    print(f"groups found:         {sum(r.groups_found for r in results)}/{n * GROUP_SIZE}")
    print(f"first guess correct:  {sum(r.first_guess_correct for r in results)}/{n}")
    print(f"mistakes per puzzle:  {statistics.mean(r.mistakes for r in results):.2f}")
    print(f"guesses per puzzle:   {statistics.mean(r.guesses for r in results):.2f}")
    failed = [r.puzzle_id for r in results if r.error]
    if failed:
        print(f"failed puzzles:       {failed}")
    median_http = statistics.median(total.http_ms) if total.http_ms else 0.0
    print(f"api calls:            {total.calls}   median http {median_http:.0f} ms")
    print(f"tokens:               {total.input_tokens:,} in / {total.output_tokens:,} out   "
          f"cost ${total.input_tokens * price:.4f}")


def save_results(results: list[GameResult], model: str, seed: int, price: float) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
    path = OUTPUT_DIR / f"{stamp}_{model.split('/')[-1]}.json"
    payload = {
        "model": model,
        "seed": seed,
        "results": [
            {
                "puzzle_id": r.puzzle_id,
                "won": r.won,
                "groups_found": r.groups_found,
                "guesses": r.guesses,
                "mistakes": r.mistakes,
                "first_guess_correct": r.first_guess_correct,
                "seconds": r.seconds,
                "input_tokens": r.usage.input_tokens,
                "output_tokens": r.usage.output_tokens,
                "cost_usd": r.usage.input_tokens * price,
                "error": r.error,
                "turns": [{"guess": list(t.guess), "result": t.result} for t in r.turns],
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(payload, indent=1))
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Solve Connections puzzles in classic mode with a decisions model via OpenRouter.")
    parser.add_argument("--puzzle-ids", type=lambda s: [int(x) for x in s.split(",")], default=None)
    parser.add_argument("--all", action="store_true", help="every puzzle instead of the canonical set")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threads", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    puzzles = load_puzzles(args.puzzle_ids, canonical_only=not args.all)
    if not puzzles:
        sys.exit("no puzzles selected")
    api_key = load_api_key()
    price = price_per_input_token(api_key, args.model)
    client = DecisionsClient(api_key, args.model)
    started = time.perf_counter()
    results = play_all(client, puzzles, args.seed, args.threads)
    wall = time.perf_counter() - started
    print_report(results, wall, args.model, price)
    print(f"saved:                {save_results(results, args.model, args.seed, price)}")


if __name__ == "__main__":
    main()
