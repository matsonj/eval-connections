"""Relatedness probe: can a model tell a real Connections group from a fake one?

One question, asked the same way of every model: "do these four words share one specific connection?"
The sets come from the puzzles themselves, in four kinds:

  true       the puzzle's four real groups                                  (should be YES)
  near_miss  a real group with one word swapped for a word from another group (should be NO, hard)
  mixed      one word from each of the four groups                          (should be NO, easy)
  trap       human-reviewed trap groups from `valid_trap_groups`            (genuinely related but not a
             solution; reported on its own and kept out of accuracy and AUROC)

No set that sits inside a trap group is used as a negative, so every negative really is unrelated per the
game's answer key. Works for decisions models (one 2-level Score question per set, so the answer is P(yes))
and for chat LLMs (asked for a 0-100 probability) through OpenRouter, so the two families get one rubric.

    uv run python experimental/relatedness_probe.py --model typesafe/jev-1.13
    uv run python experimental/relatedness_probe.py --model anthropic/claude-haiku-4.5 --kind llm
    uv run python experimental/relatedness_probe.py --model typesafe/jev-1.13 --context board --repeats 3
    uv run python experimental/relatedness_probe.py --dry-run          # build the item set, no API calls
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PUZZLES_FILE = REPO_ROOT / "inputs" / "connections_puzzles.yml"
ENV_FILE = REPO_ROOT / ".env"
OUTPUT_DIR = REPO_ROOT / "experimental" / "runs"

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"
KNOWN_DECISIONS_MODELS = {
    "typesafe/jev-1.13", "perplexity/pplx-decider-v1.1-27b", "inception/mercury-decide",
    "cloudflare/clef", "openai/gpt-6-luna-decisions", "microsoft/microsoft-decision-1",
}
REQUEST_TIMEOUT_SEC = 120
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504, 529}
MAX_QUESTIONS_PER_REQUEST = 64  # Clef's limit; applied to every decisions model
MAX_ATTEMPTS = 5
THRESHOLD = 0.5

BLURB = "NYT Connections: a group is four words that share one specific connection."
QUESTION = ("Do these four words share one specific connection, the way the four words "
            "in a single NYT Connections group do?")
LEVELS = ["No: they do not form one group", "Yes: they form one group"]
NEGATIVE_KINDS = ("near_miss", "mixed")
ALL_KINDS = ("true", "near_miss", "mixed", "trap")


@dataclass
class Item:
    item_id: str
    puzzle_id: int
    kind: str
    words: tuple[str, ...]
    board: tuple[str, ...]
    color: str | None = None
    group_name: str | None = None


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    cost_usd: float = 0.0
    http_ms: list[float] = field(default_factory=list)

    def add(self, other: "Usage") -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.cost_usd += other.cost_usd
        self.http_ms.extend(other.http_ms)


# ---------------------------------------------------------------- items

def build_items(only_ids: list[int] | None, all_puzzles: bool, rng: random.Random,
                near_miss_per_group: int, mixed_per_puzzle: int) -> list[Item]:
    data = yaml.safe_load(PUZZLES_FILE.read_text())
    items: list[Item] = []
    for raw in data["puzzles"]:
        if only_ids is not None and raw["id"] not in only_ids:
            continue
        if only_ids is None and not all_puzzles and not raw.get("canonical"):
            continue
        pid = raw["id"]
        board = tuple(w.upper() for w in raw["words"])
        groups = [(g["name"], g.get("color"), frozenset(w.upper() for w in g["words"])) for g in raw["groups"]]
        traps = [frozenset(w.upper() for w in t) for t in (raw.get("valid_trap_groups") or [])]

        def inside_trap(s: frozenset) -> bool:
            return any(s <= t for t in traps)

        def add(kind: str, words: frozenset, n: int, color=None, name=None) -> None:
            ordered = sorted(words)
            rng.shuffle(ordered)
            items.append(Item(f"{pid}-{kind}-{n}", pid, kind, tuple(ordered), board, color, name))

        for i, (name, color, words) in enumerate(groups):
            add("true", words, i, color, name)

        for i, trap in enumerate(traps):
            add("trap", trap if len(trap) == 4 else frozenset(rng.sample(sorted(trap), 4)), i)

        n = 0
        for _, _, words in groups:
            others = [w for w in board if w not in words]
            swaps = [(words - {out}) | {inn} for out in sorted(words) for inn in others]
            swaps = [s for s in swaps if not inside_trap(s)]
            for s in rng.sample(swaps, min(near_miss_per_group, len(swaps))):
                add("near_miss", s, n)
                n += 1

        mixes = [frozenset(c) for c in itertools.product(*(sorted(g[2]) for g in groups))]
        mixes = [m for m in mixes if not inside_trap(m)]
        for i, m in enumerate(rng.sample(mixes, min(mixed_per_puzzle, len(mixes)))):
            add("mixed", m, i)
    return items


def reshuffled(items: list[Item], rng: random.Random) -> list[Item]:
    """Same sets, new word order: answers that change here are order sensitivity, not knowledge."""
    out = []
    for it in items:
        words = list(it.words)
        rng.shuffle(words)
        out.append(Item(it.item_id, it.puzzle_id, it.kind, tuple(words), it.board, it.color, it.group_name))
    return out


# ---------------------------------------------------------------- transport

def load_api_key() -> str:
    if os.getenv("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"].strip()
    if ENV_FILE.exists():
        m = re.search(r"^\s*(?:export\s+)?OPENROUTER_API_KEY=[\"']?([^\"'\r\n]+)", ENV_FILE.read_text(), re.MULTILINE)
        if m:
            return m.group(1).strip()
    sys.exit(f"No OpenRouter API key: set OPENROUTER_API_KEY or add it to {ENV_FILE}")


def post_with_retry(url: str, body: dict, headers: dict, usage: Usage) -> dict:
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        started = time.perf_counter()
        try:
            response = requests.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT_SEC)
        except requests.RequestException as exc:
            response, last_error = None, exc
        usage.http_ms.append((time.perf_counter() - started) * 1000)
        usage.calls += 1
        if response is not None and response.ok:
            return response.json()
        if response is not None and response.status_code not in RETRYABLE_STATUSES:
            raise requests.HTTPError(f"HTTP {response.status_code}: {response.text[:500]}", response=response)
        if response is not None:
            last_error = requests.HTTPError(f"HTTP {response.status_code}", response=response)
        time.sleep(min(2 ** attempt, 30) * random.uniform(0.5, 1.0))
    raise RuntimeError(f"gave up after {MAX_ATTEMPTS} attempts: {last_error}")


def state_for(item: Item, context: str) -> dict:
    return {"puzzle": BLURB, "words": list(item.board)} if context == "board" else {"puzzle": BLURB}


def decisions_question(item: Item) -> dict:
    return {
        "type": "score",
        "instructions": {"task": QUESTION, "words": list(item.words)},
        "criteria": [{"summary": level} for level in LEVELS],
    }


def llm_prompt(item: Item, context: str) -> str:
    board = f"\nThe full puzzle board is: {', '.join(item.board)}\n" if context == "board" else ""
    return (f"{BLURB}\n{board}\n{QUESTION}\n\nWords: {', '.join(item.words)}\n\n"
            "Reply with only a whole number from 0 to 100: your probability that they do.")


def score_decisions(items: list[Item], model: str, api_key: str, context: str, threads: int,
                    price: float) -> tuple[dict[str, float | None], Usage]:
    headers = {"Authorization": f"Bearer {api_key}"}
    # Items that share a state can share a request; with no board context that is every item.
    by_state: dict[str, list[Item]] = {}
    for it in items:
        by_state.setdefault(json.dumps(state_for(it, context), sort_keys=True), []).append(it)
    chunks = [(json.loads(k), group[i:i + MAX_QUESTIONS_PER_REQUEST])
              for k, group in by_state.items() for i in range(0, len(group), MAX_QUESTIONS_PER_REQUEST)]

    def run(chunk) -> tuple[dict[str, float | None], Usage]:
        state, chunk_items = chunk
        usage = Usage()
        questions = {f"q{i}": decisions_question(it) for i, it in enumerate(chunk_items)}
        try:
            payload = post_with_retry(DECISIONS_URL, {"model": model, "state": state, "questions": questions},
                                      headers, usage)
        except Exception as exc:  # noqa: BLE001 - one failed chunk should not sink the run
            print(f"request failed: {exc}", file=sys.stderr)
            return {it.item_id: None for it in chunk_items}, usage
        usage.input_tokens += payload["usage"]["input_tokens"]
        usage.cost_usd += payload["usage"]["input_tokens"] * price
        answers = payload["answers"]
        return {it.item_id: answers[f"q{i}"]["score"] / (len(LEVELS) - 1) for i, it in enumerate(chunk_items)}, usage

    scores: dict[str, float | None] = {}
    total = Usage()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        for chunk_scores, usage in pool.map(run, chunks):
            scores.update(chunk_scores)
            total.add(usage)
    return scores, total


def score_llm(items: list[Item], model: str, api_key: str, context: str, threads: int,
              reasoning_effort: str | None, max_tokens: int) -> tuple[dict[str, float | None], Usage]:
    headers = {"Authorization": f"Bearer {api_key}"}

    def run(it: Item) -> tuple[str, float | None, Usage]:
        usage = Usage()
        body = {"model": model, "messages": [{"role": "user", "content": llm_prompt(it, context)}],
                "max_tokens": max_tokens, "usage": {"include": True}}
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
        try:
            payload = post_with_retry(CHAT_URL, body, headers, usage)
        except Exception as exc:  # noqa: BLE001
            print(f"{it.item_id}: request failed: {exc}", file=sys.stderr)
            return it.item_id, None, usage
        u = payload.get("usage") or {}
        usage.input_tokens += u.get("prompt_tokens", 0)
        usage.cost_usd += u.get("cost", 0.0) or 0.0
        text = (payload["choices"][0]["message"].get("content") or "").strip()
        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        if not numbers:
            print(f"{it.item_id}: unparseable reply {text[:80]!r}", file=sys.stderr)
            return it.item_id, None, usage
        return it.item_id, min(max(float(numbers[-1]), 0.0), 100.0) / 100, usage

    scores: dict[str, float | None] = {}
    total = Usage()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        for item_id, p, usage in pool.map(run, items):
            scores[item_id] = p
            total.add(usage)
    return scores, total


def price_per_input_token(api_key: str, model: str) -> float:
    r = requests.get(MODEL_ENDPOINTS_URL.format(model=model), headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    r.raise_for_status()
    return float(r.json()["data"]["endpoints"][0]["pricing"]["prompt"])


# ---------------------------------------------------------------- metrics

def auroc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def summarize(items: list[Item], runs: list[dict[str, float | None]]) -> dict:
    """Average each item's P(yes) over repeats, then compute metrics on the averages."""
    mean_p: dict[str, float] = {}
    spread: list[float] = []
    for it in items:
        ps = [r[it.item_id] for r in runs if r.get(it.item_id) is not None]
        if ps:
            mean_p[it.item_id] = statistics.mean(ps)
            if len(ps) > 1:
                spread.append(max(ps) - min(ps))

    def ps_of(kind: str, color: str | None = None) -> list[float]:
        return [mean_p[it.item_id] for it in items if it.kind == kind and it.item_id in mean_p
                and (color is None or it.color == color)]

    true_p = ps_of("true")
    neg_p = [p for k in NEGATIVE_KINDS for p in ps_of(k)]
    out = {
        "by_kind": {k: {"n": len(ps_of(k)), "mean_p_yes": statistics.mean(ps_of(k)) if ps_of(k) else None,
                        "share_yes": sum(p >= THRESHOLD for p in ps_of(k)) / len(ps_of(k)) if ps_of(k) else None}
                    for k in ALL_KINDS},
        "true_recall": sum(p >= THRESHOLD for p in true_p) / len(true_p) if true_p else None,
        "false_positive_rate": sum(p >= THRESHOLD for p in neg_p) / len(neg_p) if neg_p else None,
        "balanced_accuracy": None,
        "auroc_true_vs_mixed": auroc(true_p, ps_of("mixed")),
        "auroc_true_vs_near_miss": auroc(true_p, ps_of("near_miss")),
        "auroc_true_vs_all_negatives": auroc(true_p, neg_p),
        "true_recall_by_color": {c: (sum(p >= THRESHOLD for p in ps_of("true", c)) / len(ps_of("true", c)))
                                 for c in ("yellow", "green", "blue", "purple") if ps_of("true", c)},
        "missed_true_groups": sorted(
            [{"puzzle_id": it.puzzle_id, "group": it.group_name, "color": it.color, "words": list(it.words),
              "p_yes": round(mean_p[it.item_id], 3)}
             for it in items if it.kind == "true" and it.item_id in mean_p and mean_p[it.item_id] < THRESHOLD],
            key=lambda d: d["p_yes"]),
        "unanswered": sum(1 for it in items if it.item_id not in mean_p),
        "mean_max_minus_min_across_repeats": statistics.mean(spread) if spread else None,
    }
    if out["true_recall"] is not None and out["false_positive_rate"] is not None:
        out["balanced_accuracy"] = (out["true_recall"] + 1 - out["false_positive_rate"]) / 2
    return out


def fmt(x: float | None, pct: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:.0%}" if pct else f"{x:.3f}"


def print_report(model: str, kind: str, context: str, s: dict, usage: Usage, wall: float, repeats: int) -> None:
    print(f"model {model}   kind {kind}   context {context}   repeats {repeats}   wall {wall:.1f}s")
    print(f"{'set kind':<10} {'n':>4} {'mean P(yes)':>12} {'said yes':>9}")
    for k in ALL_KINDS:
        row = s["by_kind"][k]
        print(f"{k:<10} {row['n']:>4} {fmt(row['mean_p_yes']):>12} {fmt(row['share_yes'], True):>9}")
    print()
    print(f"true-group recall:      {fmt(s['true_recall'], True)}   (real groups the model calls related)")
    print(f"false positive rate:    {fmt(s['false_positive_rate'], True)}   (near_miss + mixed called related)")
    print(f"balanced accuracy:      {fmt(s['balanced_accuracy'], True)}")
    print(f"AUROC true vs mixed:    {fmt(s['auroc_true_vs_mixed'])}")
    print(f"AUROC true vs near-miss:{fmt(s['auroc_true_vs_near_miss']):>6}")
    print("recall by color:        " + "  ".join(f"{c} {fmt(v, True)}" for c, v in s["true_recall_by_color"].items()))
    if s["mean_max_minus_min_across_repeats"] is not None:
        print(f"repeat spread:          {s['mean_max_minus_min_across_repeats']:.3f} mean max-min P(yes) per set")
    if s["unanswered"]:
        print(f"unanswered sets:        {s['unanswered']}")
    print(f"\nmissed true groups ({len(s['missed_true_groups'])}):")
    for m in s["missed_true_groups"][:15]:
        print(f"  {m['puzzle_id']:>4} {m['color'] or '':<7} P={m['p_yes']:.2f}  {m['group']}: {', '.join(m['words'])}")
    median_ms = statistics.median(usage.http_ms) if usage.http_ms else 0
    print(f"\napi calls {usage.calls}   median http {median_ms:.0f} ms   cost ${usage.cost_usd:.4f}")


# ---------------------------------------------------------------- main

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ask models whether four words form a real Connections group.")
    p.add_argument("--model", default="typesafe/jev-1.13")
    p.add_argument("--kind", choices=["auto", "decisions", "llm"], default="auto")
    p.add_argument("--context", choices=["none", "board"], default="none",
                   help="none: the four words alone; board: also show all sixteen words")
    p.add_argument("--puzzle-ids", type=lambda s: [int(x) for x in s.split(",")], default=None)
    p.add_argument("--all", action="store_true", help="every puzzle instead of the canonical set")
    p.add_argument("--near-miss-per-group", type=int, default=2)
    p.add_argument("--mixed-per-puzzle", type=int, default=4)
    p.add_argument("--repeats", type=int, default=1, help="re-ask with reshuffled word order")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--threads", type=int, default=10)
    p.add_argument("--reasoning-effort", default=None, help="llm only, e.g. minimal, low, high")
    p.add_argument("--max-tokens", type=int, default=1024, help="llm only")
    p.add_argument("--dry-run", action="store_true", help="build and print the item set, make no API calls")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    items = build_items(args.puzzle_ids, args.all, random.Random(args.seed),
                        args.near_miss_per_group, args.mixed_per_puzzle)
    if not items:
        sys.exit("no puzzles selected")
    kind = args.kind
    if kind == "auto":
        kind = "decisions" if args.model in KNOWN_DECISIONS_MODELS else "llm"

    if args.dry_run:
        counts = {k: sum(it.kind == k for it in items) for k in ALL_KINDS}
        print(f"{len({it.puzzle_id for it in items})} puzzles, {len(items)} sets: {counts}")
        for k in ALL_KINDS:
            sample = next((it for it in items if it.kind == k), None)
            if sample:
                print(f"  {k:<10} {sample.puzzle_id}: {', '.join(sample.words)}")
        first = items[0]
        if kind == "decisions":
            print(json.dumps({"model": args.model, "state": state_for(first, args.context),
                              "questions": {"q0": decisions_question(first)}}, indent=1))
        else:
            print(llm_prompt(first, args.context))
        return

    api_key = load_api_key()
    started = time.perf_counter()
    runs, total = [], Usage()
    price = price_per_input_token(api_key, args.model) if kind == "decisions" else 0.0
    for r in range(args.repeats):
        batch = items if r == 0 else reshuffled(items, random.Random(args.seed * 1000 + r))
        if kind == "decisions":
            scores, usage = score_decisions(batch, args.model, api_key, args.context, args.threads, price)
        else:
            scores, usage = score_llm(batch, args.model, api_key, args.context, args.threads,
                                      args.reasoning_effort, args.max_tokens)
        runs.append(scores)
        total.add(usage)
    wall = time.perf_counter() - started

    summary = summarize(items, runs)
    print_report(args.model, kind, args.context, summary, total, wall, args.repeats)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{time.strftime('%Y-%m-%dT%H-%M-%S')}_probe_{args.model.split('/')[-1]}_{args.context}.json"
    path.write_text(json.dumps({
        "model": args.model, "kind": kind, "context": args.context, "seed": args.seed, "repeats": args.repeats,
        "question": QUESTION, "cost_usd": total.cost_usd, "api_calls": total.calls, "wall_seconds": wall,
        "summary": summary,
        "items": [{"item_id": it.item_id, "puzzle_id": it.puzzle_id, "kind": it.kind, "words": list(it.words),
                   "color": it.color, "group": it.group_name, "p_yes": [r.get(it.item_id) for r in runs]}
                  for it in items],
    }, indent=1))
    print(f"saved {path}")


if __name__ == "__main__":
    main()
