import argparse
import itertools
import json
import os
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

import requests
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
PUZZLES_FILE = REPO_ROOT / "inputs" / "connections_puzzles.yml"
KEY_FILE = REPO_ROOT / ".env.jev"
OUTPUT_DIR = REPO_ROOT / "experimental" / "runs"

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
PRICE_PER_INPUT_TOKEN = 42 / 1_000_000_000
REQUEST_TIMEOUT_SEC = 120
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 529}
MAX_ATTEMPTS = 5
BACKOFF_BASE_SEC = 1.0
BACKOFF_MAX_SEC = 30.0

MAX_MISTAKES = 4
GROUP_SIZE = 4
ENDGAME_SIZE = 8
ENDGAME_MODE = "planner"
CANDIDATE_SETS_FOR_DIRECT_SCORE = 60
ONE_AWAY_BONUS = 1.0
PAIR_WEIGHT = 1.0
SET_WEIGHT = 1.0

PUZZLE_BLURB = "NYT Connections: the sixteen words form four hidden groups of four related words each."
PAIR_TASK = "Judge whether the two words belong to the same group of four in this Connections puzzle."
SET_TASK = "Judge whether these four words together form one of the real groups in this Connections puzzle."
RELATEDNESS_LEVELS = [
    "Unrelated, or only coincidentally related",
    "Loosely related; could share a broad theme",
    "Clearly members of the same specific four-word category",
]


Words = Tuple[str, ...]
WordSet = FrozenSet[str]


@dataclass
class Puzzle:
    id: int
    words: List[str]
    groups: List[WordSet]
    difficulty: Optional[float]
    date: Optional[str] = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    http_ms: List[float] = field(default_factory=list)

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        self.http_ms.extend(other.http_ms)

    @property
    def cost_usd(self) -> float:
        return self.input_tokens * PRICE_PER_INPUT_TOKEN


@dataclass
class Feedback:
    wrong: List[WordSet] = field(default_factory=list)
    one_away: List[WordSet] = field(default_factory=list)

    @property
    def plain_wrong(self) -> List[WordSet]:
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
    turns: List[Turn]
    usage: Usage
    seconds: float
    error: Optional[str] = None


def load_puzzles(path: Path, only_ids: Optional[Sequence[int]], canonical_only: bool) -> List[Puzzle]:
    data = yaml.safe_load(path.read_text())
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
            difficulty=raw.get("difficulty"),
            date=str(raw["date"]) if raw.get("date") else None,
        ))
    return puzzles


def load_api_key() -> str:
    from_env = os.getenv("TYPESAFE_API_KEY")
    if from_env:
        return from_env.strip()
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    sys.exit(f"No TypeSafe API key: set TYPESAFE_API_KEY or create {KEY_FILE}")


def relatedness_question(task: str, **fields) -> Dict:
    return {
        "type": "score",
        "instructions": {"task": task, **fields},
        "criteria": [{"summary": level} for level in RELATEDNESS_LEVELS],
    }


def pair_questions(words: Sequence[str]) -> Dict[str, Dict]:
    return {
        f"{a}|{b}": relatedness_question(PAIR_TASK, word_a=a, word_b=b)
        for a, b in itertools.combinations(words, 2)
    }


def set_questions(candidate_sets: Sequence[Words]) -> Dict[str, Dict]:
    return {
        "|".join(words): relatedness_question(SET_TASK, words=list(words))
        for words in candidate_sets
    }


def puzzle_state(words: Sequence[str]) -> Dict:
    return {"puzzle": PUZZLE_BLURB, "words": list(words)}


class JevClient:
    def __init__(self, api_key: str, model: str):
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.model = model

    def ask(self, state: Dict, questions: Dict[str, Dict]) -> Tuple[Dict[str, Dict], Usage]:
        body = {"model": self.model, "state": state, "questions": questions}
        usage = Usage()
        last_error: Optional[Exception] = None
        for attempt in range(MAX_ATTEMPTS):
            started = time.perf_counter()
            try:
                response = requests.post(API_URL, json=body, headers=self.headers, timeout=REQUEST_TIMEOUT_SEC)
            except requests.RequestException as exc:
                usage.http_ms.append((time.perf_counter() - started) * 1000)
                usage.calls += 1
                last_error = exc
                time.sleep(backoff_seconds(attempt, None))
                continue
            usage.http_ms.append((time.perf_counter() - started) * 1000)
            usage.calls += 1
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


def backoff_seconds(attempt: int, retry_after: Optional[str]) -> float:
    if retry_after:
        try:
            return min(float(retry_after), BACKOFF_MAX_SEC)
        except ValueError:
            pass
    exponential = min(BACKOFF_BASE_SEC * 2 ** attempt, BACKOFF_MAX_SEC)
    return exponential * random.uniform(0.5, 1.0)


def normalized_score(answer: Dict) -> float:
    return answer["score"] / (len(RELATEDNESS_LEVELS) - 1)


def pair_affinities(client: JevClient, words: Sequence[str]) -> Tuple[Dict[FrozenSet[str], float], Usage]:
    answers, usage = client.ask(puzzle_state(words), pair_questions(words))
    affinity = {}
    for key, answer in answers.items():
        a, b = key.split("|")
        affinity[frozenset((a, b))] = normalized_score(answer)
    return affinity, usage


def pair_sum(words: Words, affinity: Dict[FrozenSet[str], float]) -> float:
    return sum(affinity[frozenset(pair)] for pair in itertools.combinations(words, 2))


def consistent_with_feedback(candidate: WordSet, feedback: Feedback) -> bool:
    if any(len(candidate & wrong) > 2 for wrong in feedback.plain_wrong):
        return False
    return all(len(candidate & near) in (0, 1, 3) for near in feedback.one_away)


def one_away_bonus(candidate: WordSet, feedback: Feedback) -> float:
    if any(len(candidate & near) == 3 for near in feedback.one_away):
        return ONE_AWAY_BONUS
    return 0.0


def outstanding_one_aways(feedback: Feedback, solved: Sequence[WordSet]) -> List[WordSet]:
    return [near for near in feedback.one_away if not any(len(near & group) == 3 for group in solved)]


def can_complete(remaining: Sequence[str], feedback: Feedback, open_clues: Sequence[WordSet]) -> bool:
    if not remaining:
        return not open_clues
    first = remaining[0]
    for others in itertools.combinations(remaining[1:], GROUP_SIZE - 1):
        group = frozenset((first, *others))
        if not consistent_with_feedback(group, feedback):
            continue
        rest = [w for w in remaining if w not in group]
        still_open = [near for near in open_clues if len(near & group) != 3]
        if can_complete(rest, feedback, still_open):
            return True
    return False


def first_completable(ranked: Sequence[Words], remaining: Sequence[str], feedback: Feedback,
                      open_clues: Sequence[WordSet]) -> Words:
    for words in ranked:
        group = frozenset(words)
        rest = [w for w in remaining if w not in group]
        still_open = [near for near in open_clues if len(near & group) != 3]
        if can_complete(rest, feedback, still_open):
            return words
    return ranked[0]


def score_candidate_sets(remaining: Sequence[str], affinity: Dict[FrozenSet[str], float],
                         feedback: Feedback) -> Dict[Words, float]:
    scored = {}
    for words in itertools.combinations(sorted(remaining), GROUP_SIZE):
        candidate = frozenset(words)
        if consistent_with_feedback(candidate, feedback):
            scored[words] = pair_sum(words, affinity) + one_away_bonus(candidate, feedback)
    return scored


def blend_with_direct_scores(client: JevClient, remaining: Sequence[str], pair_scores: Dict[Words, float],
                             rng: random.Random) -> Tuple[Dict[Words, float], Usage]:
    top = sorted(pair_scores, key=pair_scores.get, reverse=True)[:CANDIDATE_SETS_FOR_DIRECT_SCORE]
    rng.shuffle(top)
    answers, usage = client.ask(puzzle_state(remaining), set_questions(top))
    blended = {}
    for key, answer in answers.items():
        words = tuple(key.split("|"))
        pair_component = PAIR_WEIGHT * pair_scores[words] / len(list(itertools.combinations(words, 2)))
        set_component = SET_WEIGHT * normalized_score(answer)
        blended[words] = pair_component + set_component
    return blended, usage


def choose_midgame_guess(client: JevClient, remaining: List[str], feedback: Feedback,
                         open_clues: Sequence[WordSet], rng: random.Random) -> Tuple[Words, Usage]:
    usage = Usage()
    shuffled = remaining[:]
    rng.shuffle(shuffled)
    affinity, pair_usage = pair_affinities(client, shuffled)
    usage.add(pair_usage)
    pair_scores = score_candidate_sets(remaining, affinity, feedback)
    if len(pair_scores) == 1:
        return next(iter(pair_scores)), usage
    blended, set_usage = blend_with_direct_scores(client, shuffled, pair_scores, rng)
    usage.add(set_usage)
    scores = blended or pair_scores
    ranked = sorted(scores, key=scores.get, reverse=True)
    return first_completable(ranked, remaining, feedback, open_clues), usage


Split = Tuple[Words, Words]


def consistent_splits(remaining: Sequence[str], feedback: Feedback, open_clues: Sequence[WordSet]) -> List[Split]:
    first = remaining[0]
    splits = []
    for others in itertools.combinations(remaining[1:], GROUP_SIZE - 1):
        half_a = tuple(sorted((first, *others)))
        half_b = tuple(sorted(w for w in remaining if w not in half_a))
        halves = (frozenset(half_a), frozenset(half_b))
        if not all(consistent_with_feedback(h, feedback) for h in halves):
            continue
        if all(any(len(near & h) == 3 for h in halves) for near in open_clues):
            splits.append((half_a, half_b))
    return splits


def split_weights(client: JevClient, remaining: Sequence[str], splits: Sequence[Split],
                  rng: random.Random) -> Tuple[List[float], Dict[Words, float], Usage]:
    shuffled = list(remaining)
    rng.shuffle(shuffled)
    questions: Dict[str, Dict] = {}
    for i, split in enumerate(splits):
        for j, half in enumerate(split):
            questions[f"coherent_{i}_{j}"] = {
                "type": "noul",
                "instructions": f"Do these four words share a specific, conventional connection: {json.dumps(list(half))}? "
                                "All four must fit equally. The connection may use secondary meanings, phrases or wordplay.",
            }
    options = {f"p{i}": json.dumps([list(a), list(b)]) for i, (a, b) in enumerate(splits)}
    reversed_options = {f"p{i}": json.dumps([list(b), list(a)]) for i, (a, b) in enumerate(splits)}
    questions["split"] = {"type": "choice", "criteria": options,
                          "instructions": "Which split makes two groups of four, each with a specific shared connection? "
                                          "Consider alternate meanings, common phrases and wordplay."}
    questions["reversed"] = {"type": "choice", "criteria": reversed_options,
                             "instructions": "Select the correct solution to this eight-word Connections puzzle: two coherent categories of four words."}
    answers, usage = client.ask({"words": shuffled}, questions)
    weights, half_scores = [], {}
    for i, (a, b) in enumerate(splits):
        coherence = {a: answers[f"coherent_{i}_0"]["noul"], b: answers[f"coherent_{i}_1"]["noul"]}
        half_scores.update(coherence)
        best = max(coherence.values())
        odds = max(0.001, best) / max(0.001, 1 - best)
        split_probability = (answers["split"]["probabilities"][f"p{i}"] + answers["reversed"]["probabilities"][f"p{i}"]) / 2
        weights.append(odds ** 3 * (split_probability + 0.01))
    return weights, half_scores, usage


def plan_endgame(splits: Sequence[Split], weights: Sequence[float], lives: int) -> int:
    overlaps_one_away = []
    for half_a, _ in splits:
        mask = 0
        for j, (other_a, _) in enumerate(splits):
            if len(set(half_a) & set(other_a)) in (1, 3):
                mask |= 1 << j
        overlaps_one_away.append(mask)
    memo: Dict[Tuple[int, int], Tuple[float, int]] = {}

    def best(alive: int, lives_left: int) -> Tuple[float, int]:
        if alive == 0 or lives_left == 0:
            return 0.0, -1
        key = (alive, lives_left)
        if key in memo:
            return memo[key]
        result = (-1.0, -1)
        for i in range(len(splits)):
            if not alive >> i & 1:
                continue
            rest = alive & ~(1 << i)
            one_away = rest & overlaps_one_away[i]
            wrong = rest & ~overlaps_one_away[i]
            value = weights[i] + best(one_away, lives_left - 1)[0] + best(wrong, lives_left - 1)[0]
            if value > result[0]:
                result = (value, i)
        memo[key] = result
        return result

    return best((1 << len(splits)) - 1, lives)[1]


def choose_endgame_guess(client: JevClient, remaining: List[str], feedback: Feedback,
                         open_clues: Sequence[WordSet], lives: int, rng: random.Random) -> Tuple[Words, Usage]:
    splits = consistent_splits(remaining, feedback, open_clues)
    if not splits:
        return tuple(remaining[:GROUP_SIZE]), Usage()
    if len(splits) == 1:
        return splits[0][0], Usage()
    weights, half_scores, usage = split_weights(client, remaining, splits, rng)
    if ENDGAME_MODE == "greedy":
        chosen = splits[max(range(len(splits)), key=weights.__getitem__)]
    else:
        chosen = splits[plan_endgame(splits, weights, lives)]
    return max(chosen, key=half_scores.get), usage


def choose_guess(client: JevClient, remaining: List[str], feedback: Feedback, solved: Sequence[WordSet],
                 lives: int, rng: random.Random) -> Tuple[Words, Usage]:
    if len(remaining) == GROUP_SIZE:
        return tuple(remaining), Usage()
    open_clues = outstanding_one_aways(feedback, solved)
    if len(remaining) == ENDGAME_SIZE:
        return choose_endgame_guess(client, remaining, feedback, open_clues, lives, rng)
    return choose_midgame_guess(client, remaining, feedback, open_clues, rng)


def judge(guess: Words, puzzle: Puzzle, solved: List[WordSet]) -> str:
    guessed = frozenset(guess)
    if guessed in puzzle.groups:
        return "CORRECT"
    unsolved = [g for g in puzzle.groups if g not in solved]
    if any(len(guessed & g) == 3 for g in unsolved):
        return "INCORRECT - ONE AWAY"
    return "INCORRECT"


def play(client: JevClient, puzzle: Puzzle, seed: int) -> GameResult:
    rng = random.Random(seed + puzzle.id)
    remaining = puzzle.words[:]
    rng.shuffle(remaining)
    solved: List[WordSet] = []
    feedback = Feedback()
    turns: List[Turn] = []
    usage = Usage()
    mistakes = 0
    started = time.perf_counter()

    while mistakes < MAX_MISTAKES and len(solved) < len(puzzle.groups):
        guess, turn_usage = choose_guess(client, remaining, feedback, solved, MAX_MISTAKES - mistakes, rng)
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


def play_or_record_failure(client: JevClient, puzzle: Puzzle, seed: int) -> GameResult:
    started = time.perf_counter()
    try:
        return play(client, puzzle, seed)
    except Exception as exc:
        return GameResult(
            puzzle_id=puzzle.id, won=False, groups_found=0, guesses=0, mistakes=0,
            first_guess_correct=False, turns=[], usage=Usage(),
            seconds=time.perf_counter() - started, error=f"{type(exc).__name__}: {exc}",
        )


def play_all(client: JevClient, puzzles: List[Puzzle], seed: int, threads: int) -> List[GameResult]:
    with ThreadPoolExecutor(max_workers=threads) as pool:
        results = list(pool.map(lambda p: play_or_record_failure(client, p, seed), puzzles))
    return sorted(results, key=lambda r: r.puzzle_id)


def print_report(results: List[GameResult], wall_seconds: float, model: str) -> None:
    total = Usage()
    for r in results:
        total.add(r.usage)
    n = len(results)
    print(f"model {model}   puzzles {n}   wall {wall_seconds:.1f}s")
    print(f"{'id':>5} {'won':>4} {'groups':>6} {'guesses':>7} {'mistakes':>8} {'first':>5}  transcript")
    for r in results:
        transcript = " | ".join(f"{t.result[0] if t.result == 'CORRECT' else ('~' if 'ONE AWAY' in t.result else 'x')} "
                                f"{','.join(t.guess)}" for t in r.turns)
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
    print(f"tokens:               {total.input_tokens:,} in / {total.output_tokens:,} out   cost ${total.cost_usd:.4f}")


def save_results(results: List[GameResult], model: str, seed: int, puzzles_file: Path) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
    path = OUTPUT_DIR / f"{stamp}_{model}_{puzzles_file.stem}.json"
    payload = {
        "model": model,
        "seed": seed,
        "puzzles_file": str(puzzles_file),
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
                "cost_usd": r.usage.cost_usd,
                "error": r.error,
                "turns": [{"guess": list(t.guess), "result": t.result} for t in r.turns],
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(payload, indent=1))
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Solve Connections puzzles in classic mode with TypeSafe Jev.")
    parser.add_argument("--puzzle-ids", type=lambda s: [int(x) for x in s.split(",")], default=None)
    parser.add_argument("--all", action="store_true", help="every puzzle instead of the canonical set")
    parser.add_argument("--puzzles", type=Path, default=PUZZLES_FILE,
                        help="puzzle YAML to load; a non-default file plays all of its puzzles")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threads", type=int, default=20)
    parser.add_argument("--endgame", choices=["planner", "greedy"], default=ENDGAME_MODE,
                        help="planner spends mistakes to maximise the chance of finishing; greedy guesses the likeliest split")
    return parser.parse_args()


def main() -> None:
    global ENDGAME_MODE
    args = parse_args()
    ENDGAME_MODE = args.endgame
    canonical_only = not args.all and args.puzzles == PUZZLES_FILE
    puzzles = load_puzzles(args.puzzles, args.puzzle_ids, canonical_only)
    if not puzzles:
        sys.exit("no puzzles selected")
    client = JevClient(load_api_key(), args.model)
    started = time.perf_counter()
    results = play_all(client, puzzles, args.seed, args.threads)
    wall = time.perf_counter() - started
    print_report(results, wall, args.model)
    print(f"saved:                {save_results(results, args.model, args.seed, args.puzzles)}")


if __name__ == "__main__":
    main()
