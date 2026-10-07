"""jev_classic_solver.py on OpenRouter's decisions endpoint, for any decisions model (GPT-6 Luna, Jev, ...).

OpenRouter's /api/alpha/decisions takes the same body as Jev's /v1/systemone (state plus a map of typed
questions), so everything except the URL, model and key is imported from jev_classic_solver: same pair
and set questions, same blend, same feedback rules, same seeds. A like-for-like benchmark of the two.

    uv run python experimental/decisions_classic_solver.py                 # canonical 20, seed 42
    uv run python experimental/decisions_classic_solver.py --model typesafe/jev-1.13
"""
import argparse
import os
import re
import sys
import time

import requests
from typing import Dict, Tuple

from jev_classic_solver import (
    REPO_ROOT,
    Usage,
    load_puzzles,
    play_all,
    post_with_retries,
    print_report,
    save_results,
)


API_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL_ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"
DEFAULT_MODEL = "openai/gpt-6-luna-decisions"
ENV_FILE = REPO_ROOT / ".env"


def load_api_key() -> str:
    from_env = os.getenv("OPENROUTER_API_KEY")
    if from_env:
        return from_env.strip()
    if ENV_FILE.exists():
        match = re.search(r"^\s*(?:export\s+)?OPENROUTER_API_KEY=[\"']?([^\"'\r\n]+)", ENV_FILE.read_text(), re.M)
        if match:
            return match.group(1).strip()
    sys.exit(f"No OpenRouter API key: set OPENROUTER_API_KEY or add it to {ENV_FILE}")


def price_per_input_token(api_key: str, model: str) -> float:
    response = requests.get(MODEL_ENDPOINTS_URL.format(model=model),
                            headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    response.raise_for_status()
    return float(response.json()["data"]["endpoints"][0]["pricing"]["prompt"])


class DecisionsClient:
    def __init__(self, api_key: str, model: str):
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.model = model

    def ask(self, state: Dict, questions: Dict[str, Dict]) -> Tuple[Dict[str, Dict], Usage]:
        body = {"model": self.model, "state": state, "questions": questions}
        payload, usage = post_with_retries(API_URL, body, self.headers)
        return payload["answers"], usage


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
    label = args.model.split("/")[-1]
    started = time.perf_counter()
    results = play_all(client, puzzles, args.seed, args.threads)
    wall = time.perf_counter() - started
    print_report(results, wall, label, price)
    print(f"saved:                {save_results(results, label, args.seed, price)}")


if __name__ == "__main__":
    main()
