"""Shared plumbing for the experimental scripts: puzzles, API key, OpenRouter clients, retries, metrics.

decisions_classic_solver.py and relatedness_probe.py predate this module and stay self-contained.
"""
import json
import os
import random
import re
import sys
import threading
import time
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
KNOWN_DECISIONS_MODELS = [
    "typesafe/jev-1.13", "perplexity/pplx-decider-v1.1-27b", "inception/mercury-decide",
    "cloudflare/clef", "openai/gpt-6-luna-decisions", "microsoft/microsoft-decision-1",
]
REQUEST_TIMEOUT_SEC = 120
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504, 529}
MAX_QUESTIONS_PER_REQUEST = 64
MAX_ATTEMPTS = 5
BLURB = "NYT Connections: a group is four words that share one specific connection."


def load_raw_puzzles(only_ids: list[int] | None = None, all_puzzles: bool = False) -> list[dict]:
    data = yaml.safe_load(PUZZLES_FILE.read_text())
    out = []
    for raw in data["puzzles"]:
        if only_ids is not None and raw["id"] not in only_ids:
            continue
        if only_ids is None and not all_puzzles and not raw.get("canonical"):
            continue
        out.append(raw)
    return out


def all_raw_puzzles() -> list[dict]:
    return yaml.safe_load(PUZZLES_FILE.read_text())["puzzles"]


def upper_words(words) -> list[str]:
    return [w.upper() for w in words]


def load_api_key() -> str:
    if os.getenv("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"].strip()
    if ENV_FILE.exists():
        m = re.search(r"^\s*(?:export\s+)?OPENROUTER_API_KEY=[\"']?([^\"'\r\n]+)", ENV_FILE.read_text(), re.MULTILINE)
        if m:
            return m.group(1).strip()
    sys.exit(f"No OpenRouter API key: set OPENROUTER_API_KEY or add it to {ENV_FILE}")


def is_decisions_model(model: str, kind: str = "auto") -> bool:
    return kind == "decisions" or (kind == "auto" and model in KNOWN_DECISIONS_MODELS)


class Totals:
    """Thread-safe running totals for one client."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self.http_ms: list[float] = []
        self.failures = 0

    def record(self, ms: float = 0.0, calls: int = 0, inp: int = 0, out: int = 0, cost: float = 0.0,
               failures: int = 0) -> None:
        with self.lock:
            self.calls += calls
            self.input_tokens += inp
            self.output_tokens += out
            self.cost_usd += cost
            self.failures += failures
            if ms:
                self.http_ms.append(ms)

    def line(self) -> str:
        import statistics
        med = statistics.median(self.http_ms) if self.http_ms else 0
        return (f"api calls {self.calls}   median http {med:.0f} ms   tokens {self.input_tokens:,} in / "
                f"{self.output_tokens:,} out   cost ${self.cost_usd:.4f}" +
                (f"   failed requests {self.failures}" if self.failures else ""))


def post_with_retry(url: str, body: dict, headers: dict, totals: Totals) -> dict:
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        started = time.perf_counter()
        try:
            response = requests.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT_SEC)
        except requests.RequestException as exc:
            response, last_error = None, exc
        totals.record(ms=(time.perf_counter() - started) * 1000, calls=1)
        if response is not None and response.ok:
            return response.json()
        if response is not None and response.status_code not in RETRYABLE_STATUSES:
            raise requests.HTTPError(f"HTTP {response.status_code}: {response.text[:500]}", response=response)
        if response is not None:
            last_error = requests.HTTPError(f"HTTP {response.status_code}", response=response)
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.replace(".", "", 1).isdigit():
                time.sleep(min(float(retry_after), 30))
                continue
        time.sleep(min(2 ** attempt, 30) * random.uniform(0.5, 1.0))
    raise RuntimeError(f"gave up after {MAX_ATTEMPTS} attempts: {last_error}")


def price_per_input_token(api_key: str, model: str) -> float:
    r = requests.get(MODEL_ENDPOINTS_URL.format(model=model), headers={"Authorization": f"Bearer {api_key}"},
                     timeout=30)
    r.raise_for_status()
    return float(r.json()["data"]["endpoints"][0]["pricing"]["prompt"])


class Decisions:
    """OpenRouter decisions endpoint. ask() splits at 64 questions per request; answers keyed by question name."""

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.price = price_per_input_token(api_key, model)
        self.totals = Totals()

    def ask(self, state: dict, questions: dict[str, dict]) -> dict[str, dict]:
        names = list(questions)
        answers: dict[str, dict] = {}
        for start in range(0, len(names), MAX_QUESTIONS_PER_REQUEST):
            chunk = {n: questions[n] for n in names[start:start + MAX_QUESTIONS_PER_REQUEST]}
            payload = post_with_retry(DECISIONS_URL, {"model": self.model, "state": state, "questions": chunk},
                                      self.headers, self.totals)
            tokens = payload["usage"]["input_tokens"]
            self.totals.record(inp=tokens, out=payload["usage"].get("output_tokens", 0), cost=tokens * self.price)
            answers.update(payload["answers"])
        return answers


class LLM:
    """OpenRouter chat completions; cost comes from OpenRouter's own usage accounting."""

    def __init__(self, api_key: str, model: str, reasoning_effort: str | None = None, max_tokens: int = 2048):
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        self.totals = Totals()

    def complete(self, prompt: str, max_tokens: int | None = None) -> str:
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens or self.max_tokens, "usage": {"include": True}}
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}
        payload = post_with_retry(CHAT_URL, body, self.headers, self.totals)
        u = payload.get("usage") or {}
        self.totals.record(inp=u.get("prompt_tokens", 0), out=u.get("completion_tokens", 0),
                           cost=u.get("cost", 0.0) or 0.0)
        return (payload["choices"][0]["message"].get("content") or "").strip()


def score_question(task: str, levels: list[str], **fields) -> dict:
    return {"type": "score", "instructions": {"task": task, **fields},
            "criteria": [{"summary": level} for level in levels]}


def normalized(answer: dict, n_levels: int) -> float:
    return answer["score"] / (n_levels - 1)


def extract_json(text: str):
    """First JSON object or array in a reply, tolerating code fences and chatter."""
    text = re.sub(r"```(?:json)?", "", text)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


def parse_letter(text: str, n_options: int) -> int | None:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:n_options]
    m = re.search(rf"\b([{letters}])\b", text.strip().upper())
    return letters.index(m.group(1)) if m else None


def auroc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def save_run(name: str, model: str, payload: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{time.strftime('%Y-%m-%dT%H-%M-%S')}_{name}_{model.split('/')[-1]}.json"
    path.write_text(json.dumps(payload, indent=1, default=str))
    return path


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"
