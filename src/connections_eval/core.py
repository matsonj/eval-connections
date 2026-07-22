"""Core game logic and metrics for Connections puzzles."""

import random
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any

from .utils.timing import Timer
from .utils.tokens import count_tokens, extract_token_usage, extract_cost_info, extract_cache_info
from .utils.logging import log_exchange, log_summary, setup_logger
from .utils.retry import get_last_backoff_sec
import controllog as cl
from .adapters import openrouter_adapter


@dataclass
class PuzzleGroup:
    """Represents a group in a Connections puzzle."""
    name: str
    color: str
    words: List[str]


@dataclass
class Puzzle:
    """Represents a complete Connections puzzle."""
    id: int
    date: str
    difficulty: float
    words: List[str]
    groups: List[PuzzleGroup]
    canonical: bool = False
    # One-shot trap ground truth. None = puzzle not reviewed for traps
    # (trap scoring inactive); [] = reviewed, no traps ("N/A" is correct);
    # each entry is a word set of size >= 4 — any 4-word subset that isn't
    # a real group is a valid trap claim.
    trap_groups: Optional[List[List[str]]] = None


@dataclass
class PuzzleResult:
    """Result of running a single puzzle."""
    won: bool
    guess_count: int
    mistake_count: int
    invalid_count: int
    solved_groups: List[str]
    time_sec: float
    total_tokens: int
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    token_count_method: str = "APPROXIMATE"
    total_cached_tokens: int = 0
    total_cost: float = 0.0
    total_upstream_cost: float = 0.0
    total_backoff_sec: float = 0.0
    # One-shot mode only. score = base (0/1/2, perfect=3) + trap_bonus (0 or 2).
    # max_score is 5 when the puzzle has trap annotations, else 3 (0 in classic).
    score: int = 0
    groups_correct: int = 0
    trap_bonus: int = 0
    max_score: int = 0


@dataclass
class EvalStats:
    """Aggregated evaluation statistics across puzzles."""
    puzzles_attempted: int = 0
    puzzles_solved: int = 0
    total_guesses: int = 0
    correct_guesses: int = 0
    incorrect_guesses: int = 0
    invalid_responses: int = 0
    total_time_sec: float = 0.0
    total_tokens: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    token_count_method: str = "APPROXIMATE"
    total_cached_tokens: int = 0
    total_cost: float = 0.0
    total_upstream_cost: float = 0.0
    total_backoff_sec: float = 0.0
    # One-shot mode only: sum of per-puzzle scores and per-puzzle maxima
    total_score: int = 0
    max_score: int = 0
    total_trap_bonus: int = 0

    def accumulate(self, result: PuzzleResult) -> None:
        """Accumulate a single puzzle result into totals."""
        self.puzzles_attempted += 1
        if result.won:
            self.puzzles_solved += 1
        self.total_guesses += result.guess_count
        self.correct_guesses += len(result.solved_groups)
        self.incorrect_guesses += result.mistake_count
        self.invalid_responses += result.invalid_count
        self.total_time_sec += result.time_sec
        self.total_tokens += result.total_tokens
        self.total_prompt_tokens += result.total_prompt_tokens
        self.total_completion_tokens += result.total_completion_tokens
        self.total_cached_tokens += result.total_cached_tokens
        self.total_cost += result.total_cost
        self.total_upstream_cost += result.total_upstream_cost
        self.total_backoff_sec += result.total_backoff_sec
        # One-shot fields always accumulate (all stay 0 in classic mode)
        self.total_score += result.score
        self.max_score += result.max_score
        self.total_trap_bonus += result.trap_bonus
        if result.token_count_method == "API":
            self.token_count_method = "API"


@dataclass
class PuzzleDifficultyResult:
    """Result of difficulty ranking for a single puzzle."""
    puzzle_id: int
    runs: int
    wins: int
    solve_rate: float
    avg_guesses: float
    avg_mistakes: float
    model: str


@dataclass
class GameState:
    """Tracks the state of a game in progress."""
    puzzle: Puzzle
    solved_groups: Set[str]  # group colors that have been solved
    guess_count: int
    mistake_count: int
    invalid_count: int
    finished: bool
    won: bool
    start_time: Optional[float]
    end_time: Optional[float]


class ConnectionsGame:
    """Main game engine for Connections puzzles."""

    # Version for tracking evaluation framework changes
    VERSION = "4.0.0"  # One-shot mode becomes the primary eval (single submission, base 0/1/2/3 + 2-pt trap bonus)

    # Model configuration loaded from YAML file
    MODEL_CONFIG = {}

    MAX_GUESSES = 6
    MAX_MISTAKES = 4
    MAX_INVALID = 3

    def __init__(self, inputs_path: Path, log_path: Path, seed: Optional[int] = None, verbose: bool = False,
                 mode: str = "classic", reasoning_effort: Optional[str] = None):
        """
        Initialize the game engine.

        Args:
            inputs_path: Path to inputs directory
            log_path: Path to logs directory
            seed: Random seed for reproducibility
            verbose: Whether to print logs to console
            mode: Evaluation mode, "classic" (multi-turn) or "oneshot" (single submission)
            reasoning_effort: Reasoning effort for thinking models (e.g. 'minimal',
                'low', 'medium', 'high'); adapter defaults to 'minimal' when unset.
                Ignored for non-thinking models.
        """
        self.inputs_path = inputs_path
        self.log_path = log_path
        self.seed = seed or int(time.time())
        self.verbose = verbose
        self.mode = mode
        self.reasoning_effort = reasoning_effort
        self.rng = random.Random(self.seed)

        self.puzzles = self._load_puzzles()
        self.prompt_template = self._load_prompt_template()
        self.MODEL_CONFIG = self._load_model_mappings()

        # Will be set when starting a run
        self.logger = None
        self.run_id = None

    def _load_puzzles(self) -> List[Puzzle]:
        """Load puzzles from YAML file."""
        puzzles_file = self.inputs_path / "connections_puzzles.yml"
        with open(puzzles_file, 'r') as f:
            data = yaml.safe_load(f)

        puzzles = []
        for puzzle_data in data["puzzles"]:
            groups = [
                PuzzleGroup(
                    name=group["name"],
                    color=group["color"],
                    words=group["words"]
                )
                for group in puzzle_data["groups"]
            ]

            puzzle = Puzzle(
                id=puzzle_data["id"],
                date=puzzle_data["date"],
                difficulty=puzzle_data["difficulty"],
                words=puzzle_data["words"],
                groups=groups,
                canonical=puzzle_data.get("canonical", False),
                trap_groups=puzzle_data.get("valid_trap_groups"),
            )
            puzzles.append(puzzle)

        return puzzles

    def _load_prompt_template(self) -> str:
        """Load prompt template from XML file (default filename depends on mode)."""
        filename = "prompt_template_oneshot.xml" if self.mode == "oneshot" else "prompt_template.xml"
        template_file = self.inputs_path / filename
        with open(template_file, 'r') as f:
            return f.read()

    def _load_model_mappings(self) -> Dict[str, str]:
        """Load model mappings from YAML file."""
        mappings_file = self.inputs_path / "model_mappings.yml"
        try:
            with open(mappings_file, 'r') as f:
                data = yaml.safe_load(f)

            # Flatten the nested structure (thinking + non_thinking)
            models = {}
            models.update(data["models"]["thinking"])
            models.update(data["models"]["non_thinking"])
            return models
        except (FileNotFoundError, KeyError, yaml.YAMLError) as e:
            raise FileNotFoundError(f"Could not load model mappings from {mappings_file}: {e}")

    def get_canonical_puzzle_ids(self) -> List[int]:
        """Return IDs of puzzles marked canonical."""
        return [p.id for p in self.puzzles if p.canonical]

    def _build_initial_messages(self, puzzle: Puzzle, rng: random.Random) -> List[Dict]:
        """Build the initial system and user messages for a puzzle."""
        shuffled_words = puzzle.words.copy()
        rng.shuffle(shuffled_words)

        first_prompt = self._render_prompt_template(
            puzzle.id, puzzle.difficulty, shuffled_words
        )

        system_content = (first_prompt.split('<user>')[0]
                          .replace('<system>', '').replace('</system>', '').strip())

        user_section = first_prompt.split('<user>')[1]
        rules_content = user_section.split('</user>')[0].strip()
        puzzle_section = user_section.split('</user>')[1].strip()

        user_content = rules_content
        if puzzle_section and '<puzzle>' in puzzle_section and '</puzzle>' in puzzle_section:
            words_content = puzzle_section.split('<puzzle>')[1].split('</puzzle>')[0].strip()
            user_content += f"\n\nAvailable words: {words_content}"

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    def _emit_model_telemetry(
        self, task_id: str, model_id: str, puzzle_id: int, guess_count: int,
        request_text: str, content: str, prompt_tokens: Optional[int],
        completion_tokens: Optional[int], elapsed_ms: int,
        cost: Optional[float], upstream_cost: Optional[float], result: str,
        backoff_ms: int = 0,
        extra_payload: Optional[Dict] = None,
    ) -> None:
        """Emit controllog prompt and completion events.

        extra_payload, when given, is merged into the completion event payload
        (used by one-shot mode to attach score/groups_correct without changing
        classic call sites).
        """
        try:
            exchange_id = cl.new_id()
            cl.model_prompt(
                task_id=task_id,
                agent_id="agent:connections_eval",
                run_id=self.run_id,
                project_id="connections_eval",
                provider="openrouter",
                model=model_id,
                prompt_tokens=prompt_tokens or 0,
                request_text=request_text,
                payload={"puzzle_id": puzzle_id, "guess_index": guess_count},
                exchange_id=exchange_id,
            )
            cl.model_completion(
                task_id=task_id,
                agent_id="agent:connections_eval",
                run_id=self.run_id,
                project_id="connections_eval",
                provider="openrouter",
                model=model_id,
                completion_tokens=completion_tokens or 0,
                wall_ms=elapsed_ms,
                response_text=content,
                cost_money=cost,
                upstream_cost_money=upstream_cost,
                payload={
                    "puzzle_id": puzzle_id,
                    "guess_index": guess_count,
                    "result": result,
                    **(extra_payload or {}),
                },
                exchange_id=exchange_id,
            )
            # Separate posting for time spent in retry backoff (upstream 429s, etc.),
            # so reporting can split inference-time from queue-wait-time.
            if backoff_ms > 0:
                cl.event(
                    kind="model_backoff",
                    actor={"agent_id": "agent:connections_eval", "task_id": task_id},
                    run_id=self.run_id,
                    payload={
                        "puzzle_id": puzzle_id,
                        "guess_index": guess_count,
                        "model": model_id,
                        "backoff_ms": backoff_ms,
                    },
                    postings=[
                        cl.post("resource.time_ms", "agent:agent:connections_eval", "ms", -int(backoff_ms), {"kind": "backoff"}),
                        cl.post("resource.time_ms", "project:connections_eval", "ms", +int(backoff_ms), {"kind": "backoff"}),
                    ],
                    project_id="connections_eval",
                    source="runtime",
                )
        except Exception:
            pass

    def run_evaluation(
        self,
        model_name: str,
        max_puzzles: Optional[int] = None,
        is_interactive: bool = False,
        threads: int = 1,
        puzzle_ids: Optional[List[int]] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run evaluation on puzzles.

        Args:
            model_name: Name of model to evaluate (or label for interactive)
            max_puzzles: Maximum number of puzzles to run
            is_interactive: Whether to run in interactive mode
            threads: Number of parallel threads (forced to 1 for interactive)
            puzzle_ids: Specific puzzle IDs to run (preserves order; mutually exclusive with max_puzzles)
            mode: Evaluation mode ("classic" or "oneshot"); defaults to self.mode

        Returns:
            Summary statistics dict
        """
        mode = mode or self.mode
        if mode != self.mode:
            # The prompt template is chosen at construction time from self.mode,
            # so overriding here would send one mode's prompt and score the other.
            raise ValueError(
                f"run_evaluation mode {mode!r} conflicts with game mode {self.mode!r}; "
                f"construct ConnectionsGame(mode={mode!r}) so the matching prompt template is loaded"
            )
        is_oneshot = mode == "oneshot"

        # Interactive mode is inherently single-threaded (requires stdin)
        if is_interactive:
            threads = 1

        start_timestamp = datetime.utcnow().isoformat() + "Z"
        self.run_id = f"{datetime.utcnow().strftime('%Y-%m-%dT%H-%M-%S')}_{model_name}"
        self.logger = setup_logger(self.log_path, self.run_id, verbose=self.verbose)
        try:
            cl.init(project_id="connections_eval", log_dir=self.log_path)
        except Exception:
            pass

        # Build puzzle list
        if puzzle_ids is not None:
            puzzle_map = {p.id: p for p in self.puzzles}
            puzzles_to_run = [puzzle_map[pid] for pid in puzzle_ids if pid in puzzle_map]
            missing = set(puzzle_ids) - set(puzzle_map.keys())
            if missing:
                self.logger.warning(f"Puzzle IDs not found: {sorted(missing)}")
        else:
            puzzles_to_run = self.puzzles.copy()
            self.rng.shuffle(puzzles_to_run)
            if max_puzzles:
                puzzles_to_run = puzzles_to_run[:max_puzzles]

        stats = EvalStats()

        if threads <= 1:
            # Sequential: stop on first error so the user can investigate
            for puzzle in puzzles_to_run:
                try:
                    if is_interactive:
                        result = (self._run_puzzle_oneshot_interactive(puzzle) if is_oneshot
                                  else self._run_puzzle_interactive(puzzle))
                    else:
                        result = (self._run_puzzle_oneshot_ai(puzzle, model_name, self.rng) if is_oneshot
                                  else self._run_puzzle_ai(puzzle, model_name, self.rng))
                    stats.accumulate(result)
                except Exception as e:
                    self.logger.error(f"Error running puzzle {puzzle.id}: {e}")
                    break
        else:
            # Parallel: jobs already submitted, skip failures and continue
            def _run_one(p: Puzzle) -> Tuple[int, Optional[PuzzleResult], Optional[Exception]]:
                thread_rng = random.Random(self.seed + p.id)
                try:
                    run = self._run_puzzle_oneshot_ai if is_oneshot else self._run_puzzle_ai
                    return (p.id, run(p, model_name, thread_rng), None)
                except Exception as e:
                    return (p.id, None, e)

            results: List[Tuple[int, Optional[PuzzleResult], Optional[Exception]]] = []
            with ThreadPoolExecutor(max_workers=threads) as executor:
                futures = {executor.submit(_run_one, p): p for p in puzzles_to_run}
                for future in as_completed(futures):
                    results.append(future.result())

            for puzzle_id, result, exc in results:
                if exc is not None:
                    self.logger.error(f"Error running puzzle {puzzle_id}: {exc}")
                    continue
                stats.accumulate(result)

        # Build summary
        end_timestamp = datetime.utcnow().isoformat() + "Z"
        total_inference_sec = max(0.0, stats.total_time_sec - stats.total_backoff_sec)
        avg_time = (stats.total_time_sec / stats.puzzles_attempted
                    if stats.puzzles_attempted > 0 else 0.0)
        avg_inference = (total_inference_sec / stats.puzzles_attempted
                         if stats.puzzles_attempted > 0 else 0.0)

        # max_score accumulates per puzzle in stats (5 with trap annotations,
        # 3 without), so nothing to recompute here.

        summary = {
            "run_id": self.run_id,
            "model": model_name,
            "version": self.VERSION,
            "seed": self.seed,
            "threads": threads,
            "mode": mode,
            "reasoning_effort": self.reasoning_effort,
            "avg_time_sec": round(avg_time, 1),
            "avg_inference_sec": round(avg_inference, 1),
            "total_inference_sec": round(total_inference_sec, 3),
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            **asdict(stats),
        }

        if is_oneshot:
            summary["total_score"] = stats.total_score
            summary["max_score"] = stats.max_score
            summary["avg_score"] = (round(stats.total_score / stats.puzzles_attempted, 2)
                                    if stats.puzzles_attempted > 0 else 0.0)

        if puzzle_ids is not None:
            summary["puzzle_ids"] = puzzle_ids

        log_summary(self.logger, summary)
        return summary

    def _run_puzzle_ai(self, puzzle: Puzzle, model_name: str, rng: random.Random,
                       attempt: Optional[int] = None) -> PuzzleResult:
        """Run a single puzzle with AI model.

        attempt: optional trial index. The normal eval path runs each puzzle once
            per run_id, so task_id is already unique. Ranking re-runs the same
            puzzle under one run_id, so callers pass the trial index to keep each
            attempt's sticky-routing session_id distinct — otherwise repeated
            trials would share a routing session and lose independence.
        """
        model_id = self.MODEL_CONFIG[model_name]
        adapter = openrouter_adapter
        pinned_provider = adapter.extract_provider_slug(model_id)

        state = GameState(
            puzzle=puzzle,
            solved_groups=set(),
            guess_count=0,
            mistake_count=0,
            invalid_count=0,
            finished=False,
            won=False,
            start_time=None,
            end_time=None
        )

        messages = self._build_initial_messages(puzzle, rng)
        total_tokens = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        token_method = "APPROXIMATE"
        total_cached_tokens = 0
        total_cost = 0.0
        total_upstream_cost = 0.0
        total_backoff_sec = 0.0
        task_id = f"T{puzzle.id}:{self.run_id}"
        # Sticky-routing key shared by every turn of this puzzle. Append the trial
        # index when ranking so repeated attempts get independent routing sessions.
        session_id = task_id if attempt is None else f"{task_id}:a{attempt}"
        final_state_emitted = False

        state.start_time = time.time()
        try:
            cl.state_move(
                task_id=task_id, from_="NEW", to="WIP",
                project_id="connections_eval",
                agent_id="agent:connections_eval",
                run_id=self.run_id,
                payload={"puzzle_id": puzzle.id},
            )
        except Exception:
            pass

        while not state.finished:
            with Timer() as timer:
                try:
                    # Pin to provider on all calls to enable prompt caching
                    # (requires provider + cache_control + prefix >= 1024 tokens).
                    # session_id keeps every turn of this puzzle on one upstream
                    # provider (sticky routing) so caching also works for cloaked /
                    # non-pinnable models that have no provider slug.
                    response = adapter.chat(
                        messages, model_id, provider=pinned_provider, session_id=session_id,
                        reasoning_effort=self.reasoning_effort,
                    )

                    backoff_sec = float(response.pop("_backoff_sec", 0.0))
                    total_backoff_sec += backoff_sec

                    choice = response["choices"][0]
                    message = choice["message"]
                    content = message.get("content", "")
                    finish_reason = choice.get("finish_reason", "unknown")

                    # Handle empty content (thinking models may put output in reasoning fields)
                    if not content or content.strip() == "":
                        self.logger.warning(f"Empty content field. finish_reason: {finish_reason}")
                        self.logger.warning(f"Message keys: {list(message.keys())}")
                        self.logger.warning(f"Full message: {message}")
                        self.logger.warning(f"Full choice: {choice}")

                        if finish_reason == "length":
                            self.logger.error("Response truncated due to token limit!")

                        if "reasoning" in message:
                            self.logger.info("Found reasoning field, using it as content")
                            content = message["reasoning"]
                        elif "extended_thinking" in message:
                            self.logger.info("Found extended_thinking field, using it as content")
                            content = message["extended_thinking"]

                    content = content.strip() if content else ""
                    structured_response = self._parse_structured_response(content)

                    # Track tokens
                    prompt_tokens, completion_tokens, method = extract_token_usage(response)
                    if prompt_tokens and completion_tokens:
                        total_prompt_tokens += prompt_tokens
                        total_completion_tokens += completion_tokens
                        total_tokens += prompt_tokens + completion_tokens
                        token_method = method
                    else:
                        prompt_text = " ".join([msg["content"] for msg in messages])
                        approx_prompt = count_tokens(prompt_text)
                        approx_completion = count_tokens(content)
                        total_prompt_tokens += approx_prompt
                        total_completion_tokens += approx_completion
                        total_tokens += approx_prompt + approx_completion

                    # Track costs and cache info
                    cost, upstream_cost = extract_cost_info(response)
                    if cost is not None:
                        total_cost += cost
                    if upstream_cost is not None:
                        total_upstream_cost += upstream_cost

                    cache_info = extract_cache_info(response)
                    if cache_info.get("cached_tokens"):
                        total_cached_tokens += cache_info["cached_tokens"]

                    result = self._process_guess(state, content)

                except Exception as e:
                    elapsed_ms = int((time.time() - timer.start_time) * 1000) if timer.start_time else 0
                    backoff_sec = get_last_backoff_sec()
                    total_backoff_sec += backoff_sec
                    backoff_ms = int(backoff_sec * 1000)
                    inference_ms = max(0, elapsed_ms - backoff_ms)

                    log_exchange(self.logger, {
                        "run_id": self.run_id,
                        "model": model_name,
                        "puzzle_id": puzzle.id,
                        "guess_index": state.guess_count,
                        "request": messages[-1]["content"],
                        "response": str(e),
                        "latency_ms": elapsed_ms,
                        "backoff_ms": backoff_ms,
                        "inference_ms": inference_ms,
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "result": "API_ERROR"
                    })

                    self.logger.error(f"API call failed: {str(e)}")
                    try:
                        # Diagnostic event: stash error text in response_text and elapsed in wall_ms
                        # because the events.payload_json STRUCT schema has no `error`/`latency_ms` fields,
                        # and unknown fields are silently dropped at ingest.
                        cl.event(
                            kind="model_response_error",
                            actor={"agent_id": "agent:connections_eval", "task_id": task_id},
                            run_id=self.run_id,
                            payload={
                                "model": model_name,
                                "puzzle_id": puzzle.id,
                                "guess_index": state.guess_count,
                                "phase": "error",
                                "wall_ms": elapsed_ms,
                                "response_text": str(e),
                            },
                            project_id="connections_eval",
                            source="runtime",
                        )
                        # Canonical state transition — must be a state_move event so the
                        # renderer (and any other state_move consumer) sees WIP → FAILED.
                        cl.state_move(
                            task_id=task_id, from_="WIP", to="FAILED",
                            project_id="connections_eval",
                            agent_id="agent:connections_eval",
                            run_id=self.run_id,
                            payload={"puzzle_id": puzzle.id, "reason": "api_error"},
                        )
                        final_state_emitted = True
                    except Exception:
                        pass
                    state.finished = True
                    break

            # Log exchange
            backoff_ms = int(backoff_sec * 1000)
            inference_ms = max(0, timer.elapsed_ms - backoff_ms)
            exchange_data = {
                "run_id": self.run_id,
                "model": model_name,
                "puzzle_id": puzzle.id,
                "guess_index": state.guess_count,
                "request": messages[-1]["content"],
                "response": content,
                "thinking": structured_response.get('thinking', ''),
                "guess": structured_response.get('guess', ''),
                "confidence": structured_response.get('confidence', ''),
                "latency_ms": timer.elapsed_ms,
                "backoff_ms": backoff_ms,
                "inference_ms": inference_ms,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "result": result
            }

            if cost is not None:
                exchange_data["cost"] = cost
            if upstream_cost is not None:
                exchange_data["upstream_cost"] = upstream_cost
            if cache_info.get("cached_tokens") is not None:
                exchange_data["cached_tokens"] = cache_info["cached_tokens"]
            if cache_info.get("cache_discount") is not None:
                exchange_data["cache_discount"] = cache_info["cache_discount"]

            log_exchange(self.logger, exchange_data)

            self._emit_model_telemetry(
                task_id, model_id, puzzle.id, state.guess_count,
                messages[-1]["content"], content,
                prompt_tokens, completion_tokens, timer.elapsed_ms,
                cost, upstream_cost, result,
                backoff_ms=backoff_ms,
            )

            # Append response and result to conversation
            messages.append({"role": "assistant", "content": content})
            if not state.finished:
                messages.append({"role": "user", "content": result})

        state.end_time = time.time()
        time_sec = state.end_time - state.start_time

        # Emit final state transition
        if not final_state_emitted:
            final_state = "DONE" if state.won else "FAILED"
            try:
                cl.state_move(
                    task_id=task_id, from_="WIP", to=final_state,
                    project_id="connections_eval",
                    agent_id="agent:connections_eval",
                    run_id=self.run_id,
                    payload={"puzzle_id": puzzle.id},
                )
            except Exception:
                pass

        return PuzzleResult(
            won=state.won,
            guess_count=state.guess_count,
            mistake_count=state.mistake_count,
            invalid_count=state.invalid_count,
            solved_groups=list(state.solved_groups),
            time_sec=time_sec,
            total_tokens=total_tokens,
            total_prompt_tokens=total_prompt_tokens,
            total_completion_tokens=total_completion_tokens,
            token_count_method=token_method,
            total_cached_tokens=total_cached_tokens,
            total_cost=total_cost,
            total_upstream_cost=total_upstream_cost,
            total_backoff_sec=total_backoff_sec,
        )

    def _run_puzzle_interactive(self, puzzle: Puzzle) -> PuzzleResult:
        """Run a single puzzle in interactive mode."""
        state = GameState(
            puzzle=puzzle,
            solved_groups=set(),
            guess_count=0,
            mistake_count=0,
            invalid_count=0,
            finished=False,
            won=False,
            start_time=None,
            end_time=None
        )

        # Shuffle words for display (same as AI models see)
        shuffled_words = puzzle.words.copy()
        self.rng.shuffle(shuffled_words)

        # Render and display the full prompt template that AI models see
        first_prompt = self._render_prompt_template(
            puzzle.id, puzzle.difficulty, shuffled_words
        )

        print(f"\n{'='*60}")
        print("PROMPT (same as AI models receive):")
        print('='*60)
        print(first_prompt)
        print('='*60)
        print("\nYou are now playing as the AI model. Respond exactly as instructed above.")
        print("Enter 4 words separated by commas, or 'quit' to exit.\n")

        state.start_time = time.time()

        while not state.finished:
            prompt = f"Guess {state.guess_count + 1}/6 (Mistakes: {state.mistake_count}/4): "
            try:
                user_input = input(prompt).strip()
                if user_input.lower() == 'quit':
                    state.finished = True
                    break

                result = self._process_guess(state, user_input)
                print(f"Result: {result}")

                if result == "CORRECT":
                    print("Congratulations! You won!")
                elif result == "CORRECT. NEXT GUESS?":
                    remaining_groups = 4 - len(state.solved_groups)
                    print(f"Great! {remaining_groups} groups remaining.")
                elif result.startswith("INCORRECT"):
                    print("Not quite.")
                elif result.startswith("INVALID_RESPONSE"):
                    print("Please try again with a valid guess.")

            except KeyboardInterrupt:
                print("\nQuitting...")
                state.finished = True
                break

        state.end_time = time.time()
        time_sec = state.end_time - state.start_time

        if state.won:
            print(f"\nCongratulations! You solved the puzzle in {state.guess_count} guesses!")
        elif state.mistake_count >= self.MAX_MISTAKES:
            print(f"\nGame over! You made {self.MAX_MISTAKES} mistakes.")
        elif state.invalid_count >= self.MAX_INVALID:
            print(f"\nGame over! Too many invalid responses.")

        # Show solution
        print("\nSolution:")
        for group in puzzle.groups:
            print(f"  {group.color.upper()}: {group.name} - {', '.join(group.words)}")

        return PuzzleResult(
            won=state.won,
            guess_count=state.guess_count,
            mistake_count=state.mistake_count,
            invalid_count=state.invalid_count,
            solved_groups=list(state.solved_groups),
            time_sec=time_sec,
            total_tokens=0,
            token_count_method="N/A",
        )

    def _run_puzzle_oneshot_ai(self, puzzle: Puzzle, model_name: str, rng: random.Random,
                               attempt: Optional[int] = None) -> PuzzleResult:
        """Run a single puzzle in one-shot mode with an AI model.

        Modeled on _run_puzzle_ai but makes a single API call: the model submits
        all 4 groups at once and there is no feedback loop. Base score 0/1/2/3
        (perfect = 3) plus a 2-point trap bonus.

        attempt: optional trial index. See _run_puzzle_ai for the session_id
            rationale — repeated attempts get independent sticky-routing sessions.
        """
        model_id = self.MODEL_CONFIG[model_name]
        adapter = openrouter_adapter
        pinned_provider = adapter.extract_provider_slug(model_id)

        messages = self._build_initial_messages(puzzle, rng)
        total_tokens = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        token_method = "APPROXIMATE"
        total_cached_tokens = 0
        total_cost = 0.0
        total_upstream_cost = 0.0
        total_backoff_sec = 0.0
        task_id = f"T{puzzle.id}:{self.run_id}"
        # Sticky-routing key. Append the trial index when ranking so repeated
        # attempts get independent routing sessions.
        session_id = task_id if attempt is None else f"{task_id}:a{attempt}"
        final_state_emitted = False

        start_time = time.time()
        try:
            cl.state_move(
                task_id=task_id, from_="NEW", to="WIP",
                project_id="connections_eval",
                agent_id="agent:connections_eval",
                run_id=self.run_id,
                payload={"puzzle_id": puzzle.id},
            )
        except Exception:
            pass

        with Timer() as timer:
            try:
                # Pin to provider on all calls to enable prompt caching; session_id
                # keeps this puzzle on one upstream provider (sticky routing).
                response = adapter.chat(
                    messages, model_id, provider=pinned_provider, session_id=session_id,
                    reasoning_effort=self.reasoning_effort,
                )

                backoff_sec = float(response.pop("_backoff_sec", 0.0))
                total_backoff_sec += backoff_sec

                choice = response["choices"][0]
                message = choice["message"]
                content = message.get("content", "")
                finish_reason = choice.get("finish_reason", "unknown")

                # Handle empty content (thinking models may put output in reasoning fields)
                if not content or content.strip() == "":
                    self.logger.warning(f"Empty content field. finish_reason: {finish_reason}")
                    self.logger.warning(f"Message keys: {list(message.keys())}")
                    self.logger.warning(f"Full message: {message}")
                    self.logger.warning(f"Full choice: {choice}")

                    if finish_reason == "length":
                        self.logger.error("Response truncated due to token limit!")

                    if "reasoning" in message:
                        self.logger.info("Found reasoning field, using it as content")
                        content = message["reasoning"]
                    elif "extended_thinking" in message:
                        self.logger.info("Found extended_thinking field, using it as content")
                        content = message["extended_thinking"]

                content = content.strip() if content else ""
                structured_response = self._parse_structured_response(content)

                # Track tokens
                prompt_tokens, completion_tokens, method = extract_token_usage(response)
                if prompt_tokens and completion_tokens:
                    total_prompt_tokens += prompt_tokens
                    total_completion_tokens += completion_tokens
                    total_tokens += prompt_tokens + completion_tokens
                    token_method = method
                else:
                    prompt_text = " ".join([msg["content"] for msg in messages])
                    approx_prompt = count_tokens(prompt_text)
                    approx_completion = count_tokens(content)
                    total_prompt_tokens += approx_prompt
                    total_completion_tokens += approx_completion
                    total_tokens += approx_prompt + approx_completion

                # Track costs and cache info
                cost, upstream_cost = extract_cost_info(response)
                if cost is not None:
                    total_cost += cost
                if upstream_cost is not None:
                    total_upstream_cost += upstream_cost

                cache_info = extract_cache_info(response)
                if cache_info.get("cached_tokens"):
                    total_cached_tokens += cache_info["cached_tokens"]

                # Parse and score the single submission
                groups = self._parse_oneshot_response(content)
                groups_correct, score = self._score_oneshot(puzzle, groups)
                won = (groups_correct == 4)
                puzzle_max = 5 if puzzle.trap_groups is not None else 3

                # Structural validity mirrors _score_oneshot's gate: exactly 4
                # groups of 4 words that together equal the puzzle's 16 words.
                submitted_words = [w for g in groups for w in g]
                is_valid = (
                    len(groups) == 4
                    and all(len(g) == 4 for g in groups)
                    and len(submitted_words) == 16
                    and set(submitted_words) == set(w.upper() for w in puzzle.words)
                )

                if not is_valid:
                    # A structurally invalid answer scores 0 overall — the trap
                    # bonus is forfeited along with the base score.
                    invalid_count = 1
                    mistake_count = 0
                    trap_bonus = 0
                    trap_claims = None
                    solved_groups: List[str] = []
                    result = f"ONESHOT_INVALID_MAX_{puzzle_max}"
                else:
                    invalid_count = 0
                    mistake_count = 4 - groups_correct
                    # Colors of matched puzzle groups
                    submitted_sets = [set(w.upper() for w in g) for g in groups]
                    solved_groups = [
                        grp.color for grp in puzzle.groups
                        if set(w.upper() for w in grp.words) in submitted_sets
                    ]
                    trap_claims = self._parse_oneshot_traps(content)
                    trap_bonus = self._score_trap_claims(puzzle, trap_claims)
                    score += trap_bonus
                    # MAX carries the per-puzzle ceiling (5 reviewed / 3 unreviewed)
                    # so downstream aggregation doesn't have to guess it.
                    result = f"ONESHOT_SCORE_{score}_GROUPS_{groups_correct}_TRAP_{trap_bonus}_MAX_{puzzle_max}"

            except Exception as e:
                elapsed_ms = int((time.time() - timer.start_time) * 1000) if timer.start_time else 0
                backoff_sec = get_last_backoff_sec()
                total_backoff_sec += backoff_sec
                backoff_ms = int(backoff_sec * 1000)
                inference_ms = max(0, elapsed_ms - backoff_ms)

                log_exchange(self.logger, {
                    "run_id": self.run_id,
                    "model": model_name,
                    "puzzle_id": puzzle.id,
                    "guess_index": 0,
                    "request": messages[-1]["content"],
                    "response": str(e),
                    "latency_ms": elapsed_ms,
                    "backoff_ms": backoff_ms,
                    "inference_ms": inference_ms,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "result": "API_ERROR"
                })

                self.logger.error(f"API call failed: {str(e)}")
                try:
                    cl.event(
                        kind="model_response_error",
                        actor={"agent_id": "agent:connections_eval", "task_id": task_id},
                        run_id=self.run_id,
                        payload={
                            "model": model_name,
                            "puzzle_id": puzzle.id,
                            "guess_index": 0,
                            "phase": "error",
                            "wall_ms": elapsed_ms,
                            "response_text": str(e),
                            # Mode marker: without it, a run whose every call
                            # errors has no ONESHOT_* completion strings and the
                            # MotherDuck aggregation would misclassify it as
                            # classic. MAX carries the per-puzzle score ceiling.
                            "result": f"ONESHOT_API_ERROR_MAX_{5 if puzzle.trap_groups is not None else 3}",
                        },
                        project_id="connections_eval",
                        source="runtime",
                    )
                    cl.state_move(
                        task_id=task_id, from_="WIP", to="FAILED",
                        project_id="connections_eval",
                        agent_id="agent:connections_eval",
                        run_id=self.run_id,
                        payload={"puzzle_id": puzzle.id, "reason": "api_error"},
                    )
                    final_state_emitted = True
                except Exception:
                    pass

                return PuzzleResult(
                    won=False,
                    guess_count=0,
                    mistake_count=0,
                    invalid_count=0,
                    solved_groups=[],
                    time_sec=time.time() - start_time,
                    total_tokens=0,
                    total_prompt_tokens=0,
                    total_completion_tokens=0,
                    token_count_method=token_method,
                    total_cached_tokens=0,
                    total_cost=0.0,
                    total_upstream_cost=0.0,
                    total_backoff_sec=total_backoff_sec,
                    score=0,
                    groups_correct=0,
                    trap_bonus=0,
                    max_score=5 if puzzle.trap_groups is not None else 3,
                )

        # Log exchange. _parse_structured_response covers thinking/confidence;
        # extract the <answer> block content for the 'guess' field. Strip
        # thinking blocks first so a decoy <answer> example inside reasoning
        # isn't logged as the guess (scoring already does the same).
        import re
        log_cleaned = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', content, flags=re.IGNORECASE | re.DOTALL)
        log_cleaned = re.sub(r'<think(?:ing)?>.*', '', log_cleaned, flags=re.IGNORECASE | re.DOTALL)
        answer_match = re.search(r'<answer>(.*?)</answer>', log_cleaned, re.IGNORECASE | re.DOTALL)
        answer_text = answer_match.group(1).strip() if answer_match else content
        backoff_ms = int(backoff_sec * 1000)
        inference_ms = max(0, timer.elapsed_ms - backoff_ms)
        exchange_data = {
            "run_id": self.run_id,
            "model": model_name,
            "puzzle_id": puzzle.id,
            "guess_index": 0,
            "request": messages[-1]["content"],
            "response": content,
            "thinking": structured_response.get('thinking', ''),
            "guess": answer_text,
            "confidence": structured_response.get('confidence', ''),
            "latency_ms": timer.elapsed_ms,
            "backoff_ms": backoff_ms,
            "inference_ms": inference_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "result": result,
            "score": score,
            "groups_correct": groups_correct,
            "trap_bonus": trap_bonus,
            "trap_claims": ([] if trap_claims is None
                            else [", ".join(c) for c in trap_claims] or ["N/A"]),
        }

        if cost is not None:
            exchange_data["cost"] = cost
        if upstream_cost is not None:
            exchange_data["upstream_cost"] = upstream_cost
        if cache_info.get("cached_tokens") is not None:
            exchange_data["cached_tokens"] = cache_info["cached_tokens"]
        if cache_info.get("cache_discount") is not None:
            exchange_data["cache_discount"] = cache_info["cache_discount"]

        log_exchange(self.logger, exchange_data)

        self._emit_model_telemetry(
            task_id, model_id, puzzle.id, 0,
            messages[-1]["content"], content,
            prompt_tokens, completion_tokens, timer.elapsed_ms,
            cost, upstream_cost, result,
            backoff_ms=backoff_ms,
            extra_payload={"score": score, "groups_correct": groups_correct,
                           "trap_bonus": trap_bonus},
        )

        time_sec = time.time() - start_time

        # Emit final state transition
        if not final_state_emitted:
            final_state = "DONE" if won else "FAILED"
            try:
                cl.state_move(
                    task_id=task_id, from_="WIP", to=final_state,
                    project_id="connections_eval",
                    agent_id="agent:connections_eval",
                    run_id=self.run_id,
                    payload={"puzzle_id": puzzle.id},
                )
            except Exception:
                pass

        return PuzzleResult(
            won=won,
            guess_count=1,
            mistake_count=mistake_count,
            invalid_count=invalid_count,
            solved_groups=solved_groups,
            time_sec=time_sec,
            total_tokens=total_tokens,
            total_prompt_tokens=total_prompt_tokens,
            total_completion_tokens=total_completion_tokens,
            token_count_method=token_method,
            total_cached_tokens=total_cached_tokens,
            total_cost=total_cost,
            total_upstream_cost=total_upstream_cost,
            total_backoff_sec=total_backoff_sec,
            score=score,
            groups_correct=groups_correct,
            trap_bonus=trap_bonus,
            max_score=puzzle_max,
        )

    def _run_puzzle_oneshot_interactive(self, puzzle: Puzzle) -> PuzzleResult:
        """Run a single puzzle in one-shot interactive mode."""
        # Shuffle words for display (same as AI models see)
        shuffled_words = puzzle.words.copy()
        self.rng.shuffle(shuffled_words)

        # Render and display the full prompt template that AI models see
        first_prompt = self._render_prompt_template(
            puzzle.id, puzzle.difficulty, shuffled_words
        )

        print(f"\n{'='*60}")
        print("PROMPT (same as AI models receive):")
        print('='*60)
        print(first_prompt)
        print('='*60)
        print("\nYou are now playing as the AI model. Submit all 4 groups.")
        print("Enter each group as 4 words separated by commas.\n")

        start_time = time.time()

        groups: List[List[str]] = []
        for i in range(4):
            try:
                user_input = input(f"Group {i + 1}/4: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nQuitting...")
                break
            words = [word.strip().upper() for word in user_input.split(',')]
            words = [word for word in words if word]
            groups.append(words)

        groups_correct, score = self._score_oneshot(puzzle, groups)
        won = (groups_correct == 4)

        # Structural validity mirrors _score_oneshot's gate.
        submitted_words = [w for g in groups for w in g]
        is_valid = (
            len(groups) == 4
            and all(len(g) == 4 for g in groups)
            and len(submitted_words) == 16
            and set(submitted_words) == set(w.upper() for w in puzzle.words)
        )

        # Trap claim (only when the puzzle has been reviewed for traps)
        trap_bonus = 0
        if puzzle.trap_groups is not None and is_valid:
            print("\nTrap: enter your single most likely trap set (4 comma-separated words),")
            print("'N/A' if you believe there are none, or blank to skip.")
            try:
                line = input("Trap set: ").strip()
            except (KeyboardInterrupt, EOFError):
                line = ""
            trap_text = f"<traps>\n{line}\n</traps>" if line else ""
            trap_claims = self._parse_oneshot_traps(trap_text)
            trap_bonus = self._score_trap_claims(puzzle, trap_claims)
            score += trap_bonus

        time_sec = time.time() - start_time
        puzzle_max = 5 if puzzle.trap_groups is not None else 3
        print(f"\nScore: {score}/{puzzle_max} ({groups_correct}/4 groups correct, trap bonus {trap_bonus})")

        # Show solution
        print("\nSolution:")
        for group in puzzle.groups:
            print(f"  {group.color.upper()}: {group.name} - {', '.join(group.words)}")

        # Colors of matched puzzle groups (empty when invalid)
        submitted_sets = [set(w.upper() for w in g) for g in groups]
        solved_groups = [
            grp.color for grp in puzzle.groups
            if set(w.upper() for w in grp.words) in submitted_sets
        ] if is_valid else []

        return PuzzleResult(
            won=won,
            guess_count=1,
            mistake_count=4 - groups_correct if is_valid else 0,
            invalid_count=0 if is_valid else 1,
            solved_groups=solved_groups,
            time_sec=time_sec,
            total_tokens=0,
            token_count_method="N/A",
            score=score,
            groups_correct=groups_correct,
            trap_bonus=trap_bonus,
            max_score=puzzle_max,
        )

    def _process_guess(self, state: GameState, response: str) -> str:
        """
        Process a guess and update game state.

        Args:
            state: Current game state
            response: Raw response from model or user

        Returns:
            Result string: "CORRECT", "INCORRECT", or detailed invalid response message
        """
        # Parse response
        words = self._parse_response(response)

        # Validate response
        validation_error = self._validate_guess(state, words)
        if validation_error:
            state.invalid_count += 1
            # Get remaining words (not from solved groups)
            remaining_words = self._get_remaining_words(state)
            invalid_message = f"INVALID_RESPONSE: {validation_error}. Available words: {', '.join(sorted(remaining_words))}. You provided: {', '.join(words) if words else 'no valid words'}"
            if state.invalid_count >= self.MAX_INVALID:
                state.finished = True
            return invalid_message

        # Check if guess is correct
        state.guess_count += 1

        for group in state.puzzle.groups:
            if set(words) == set(group.words):
                state.solved_groups.add(group.color)
                if len(state.solved_groups) >= 4:
                    state.finished = True
                    state.won = True
                    return "CORRECT"
                else:
                    return "CORRECT. NEXT GUESS?"

        # Incorrect guess — check if one away from any unsolved group
        one_away = False
        for group in state.puzzle.groups:
            if group.color not in state.solved_groups:
                overlap = len(set(words) & set(w.upper() for w in group.words))
                if overlap == 3:
                    one_away = True
                    break

        state.mistake_count += 1
        if state.mistake_count >= self.MAX_MISTAKES:
            state.finished = True

        remaining_guesses = self.MAX_MISTAKES - state.mistake_count
        if one_away:
            return f"INCORRECT - ONE AWAY. {remaining_guesses} INCORRECT GUESSES REMAINING."
        return f"INCORRECT. {remaining_guesses} INCORRECT GUESSES REMAINING."

    def _parse_response(self, response: str) -> List[str]:
        """Parse response into list of words, handling structured XML format."""
        import re

        # Strip <thinking>/<think> blocks first so that any <guess> examples
        # inside reasoning don't get picked up by the guess regex.
        # Also handle unclosed tags (truncated responses).
        cleaned = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', response, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r'<think(?:ing)?>.*', '', cleaned, flags=re.IGNORECASE | re.DOTALL)

        # First try to extract from <guess> tags
        guess_match = re.search(r'<guess>(.*?)</guess>', cleaned, re.IGNORECASE | re.DOTALL)
        if guess_match:
            guess_text = guess_match.group(1).strip()
            words = [word.strip().upper() for word in guess_text.split(',')]
            return [word for word in words if word]

        # Fallback: try to find 4 comma-separated words in ALL CAPS
        caps_pattern = r'\b[A-Z][A-Z\s]*\b(?:\s*,\s*[A-Z][A-Z\s]*\b){3}'
        caps_match = re.search(caps_pattern, cleaned)
        if caps_match:
            words = [word.strip().upper() for word in caps_match.group().split(',')]
            return [word for word in words if word]

        # Final fallback: original comma-split logic
        words = [word.strip().upper() for word in cleaned.split(',')]
        return [word for word in words if word]

    def _parse_structured_response(self, response: str) -> Dict[str, str]:
        """
        Parse structured response into components.

        Returns:
            Dict with 'thinking', 'guess', and 'confidence' keys
        """
        import re

        result = {
            'thinking': '',
            'guess': '',
            'confidence': ''
        }

        thinking_match = re.search(r'<think(?:ing)?>(.*?)</think(?:ing)?>', response, re.IGNORECASE | re.DOTALL)
        if thinking_match:
            result['thinking'] = thinking_match.group(1).strip()

        guess_match = re.search(r'<guess>(.*?)</guess>', response, re.IGNORECASE | re.DOTALL)
        if guess_match:
            result['guess'] = guess_match.group(1).strip()

        confidence_match = re.search(r'<confidence>(.*?)</confidence>', response, re.IGNORECASE | re.DOTALL)
        if confidence_match:
            result['confidence'] = confidence_match.group(1).strip()

        return result

    def _parse_oneshot_response(self, response: str) -> List[List[str]]:
        """
        Parse a one-shot response into a list of word-groups.

        Extracts the <answer> block, splitting it into one group per non-empty
        line and each line into comma-separated, upper-cased words. Falls back to
        scanning for lines of four comma-separated ALL CAPS words when no <answer>
        tag is present. The returned groups may be malformed — structural
        validation happens in _score_oneshot.
        """
        import re

        # Strip <thinking>/<think> blocks first (including unclosed ones) so that
        # any example answers inside reasoning don't get picked up below.
        cleaned = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', response, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r'<think(?:ing)?>.*', '', cleaned, flags=re.IGNORECASE | re.DOTALL)

        # First try to extract from the <answer> block
        answer_match = re.search(r'<answer>(.*?)</answer>', cleaned, re.IGNORECASE | re.DOTALL)
        if answer_match:
            answer_text = answer_match.group(1).strip()
            groups = []
            for line in answer_text.splitlines():
                if not line.strip():
                    continue
                words = [word.strip().upper() for word in line.split(',')]
                words = [word for word in words if word]
                groups.append(words)
            return groups

        # Fallback: scan for lines that look like 4 comma-separated ALL CAPS words.
        # Strip <traps>/<confidence> blocks first — trap-claim lines look exactly
        # like answer lines and would otherwise be scanned as extra groups,
        # turning a valid tagless answer into a structural invalid.
        cleaned = re.sub(r'<traps>.*?</traps>', '', cleaned, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r'<traps>.*', '', cleaned, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r'<confidence>.*?</confidence>', '', cleaned, flags=re.IGNORECASE | re.DOTALL)
        # Allow hyphens/apostrophes so words like FLEUR-DE-LIS survive intact.
        caps_pattern = r"\b[A-Z][A-Z\s'\-]*\b(?:\s*,\s*[A-Z][A-Z\s'\-]*\b){3}"
        groups = []
        for line in cleaned.splitlines():
            caps_match = re.search(caps_pattern, line)
            if caps_match:
                words = [word.strip().upper() for word in caps_match.group().split(',')]
                words = [word for word in words if word]
                groups.append(words)
        return groups

    def _score_oneshot(self, puzzle: Puzzle, groups: List[List[str]]) -> Tuple[int, int]:
        """
        Score a one-shot submission.

        Returns:
            (groups_correct, base_score). Any structural failure returns (0, 0):
            not exactly 4 groups, not exactly 4 words per group, or the 16 words
            (upper-cased) not exactly equal to the puzzle's words. Otherwise
            base_score = matches for 0/1/2, and 3 for a perfect solve (exactly
            3 matches is impossible — the 4th group is forced). Trap bonus is
            scored separately by _score_trap_claims.
        """
        # Structural validation: exactly 4 groups of 4 words
        if len(groups) != 4 or any(len(group) != 4 for group in groups):
            return (0, 0)

        # The 16 submitted words must exactly equal the puzzle's words (covers
        # duplicates and non-puzzle words in a single set comparison).
        submitted_words = [word for group in groups for word in group]
        puzzle_words = set(word.upper() for word in puzzle.words)
        if len(submitted_words) != 16 or set(submitted_words) != puzzle_words:
            return (0, 0)

        # Count submitted groups whose word-set matches some puzzle group's word-set
        puzzle_group_sets = [set(word.upper() for word in group.words) for group in puzzle.groups]
        matches = 0
        for group in groups:
            if set(group) in puzzle_group_sets:
                matches += 1

        score = 3 if matches == 4 else matches
        return (matches, score)

    def _parse_oneshot_traps(self, response: str) -> Optional[List[List[str]]]:
        """
        Parse the optional <traps> block from a one-shot response.

        Returns:
            None when no <traps> block is present (no claim made — no bonus,
            not voided). [] when the model explicitly claims there are no traps
            (a line reading N/A, NA, or NONE). Otherwise one word-list per
            non-empty line (validity is judged in _score_trap_claims).
        """
        import re

        cleaned = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', response, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r'<think(?:ing)?>.*', '', cleaned, flags=re.IGNORECASE | re.DOTALL)

        traps_match = re.search(r'<traps>(.*?)</traps>', cleaned, re.IGNORECASE | re.DOTALL)
        if not traps_match:
            return None

        lines = [ln.strip() for ln in traps_match.group(1).splitlines() if ln.strip()]
        if not lines:
            return None
        # Sentinel on the FIRST line wins regardless of trailing lines — only
        # the first claim is ever judged, so extra lines after N/A are ignored
        # just like extra claims after a word set.
        if re.fullmatch(r'(?:N/?A|NONE)\.?', lines[0], re.IGNORECASE):
            return []

        claims = []
        for line in lines:
            words = [w.strip().upper() for w in line.split(',')]
            claims.append([w for w in words if w])
        return claims

    def _score_trap_claims(self, puzzle: Puzzle, claims: Optional[List[List[str]]]) -> int:
        """
        Score trap claims against the puzzle's annotated trap ground truth.

        Rules (max one bonus of 2 per puzzle):
        - Puzzle not reviewed for traps (trap_groups is None) or no claim made
          (claims is None): 0.
        - Explicit "no traps" claim ([]): +2 iff the puzzle truly has no traps.
        - Otherwise ONLY THE FIRST claim is judged (the prompt asks for a single
          best trap set; extra lines are ignored). The claim is correct — and
          earns +2 — when it is exactly 4 words, is a subset of an annotated
          trap set (supersets of 5+ mark overloaded categories), is not a real
          group, and takes at most 2 words from any single real group (a "real
          group with one swap" is not a trap). An incorrect claim scores 0.
        """
        if puzzle.trap_groups is None or claims is None:
            return 0

        trap_sets = [frozenset(w.upper() for w in t) for t in puzzle.trap_groups]
        if claims == []:
            return 2 if not trap_sets else 0

        group_sets = [frozenset(w.upper() for w in g.words) for g in puzzle.groups]
        claim = frozenset(claims[0])
        correct = (
            # Exactly 4 words as SUBMITTED — a 5-token line with a duplicate
            # collapses to a 4-element set and must not sneak past the gate.
            len(claims[0]) == 4
            and len(claim) == 4
            and claim not in group_sets
            # A trap is a cross-cutting category: never 3+ words from a single
            # real group. Enforced here so annotation mistakes can't score.
            and all(len(claim & g) <= 2 for g in group_sets)
            and any(claim <= trap for trap in trap_sets)
        )
        return 2 if correct else 0

    def _validate_guess(self, state: GameState, words: List[str]) -> Optional[str]:
        """
        Validate a guess.

        Returns:
            Error message if invalid, None if valid
        """
        if len(words) != 4:
            return f"Expected 4 words, got {len(words)}"

        if len(set(words)) != 4:
            return "Duplicate words not allowed"

        puzzle_words = set(word.upper() for word in state.puzzle.words)
        for word in words:
            if word not in puzzle_words:
                return f"Word '{word}' not in puzzle"

        solved_words = set()
        for group in state.puzzle.groups:
            if group.color in state.solved_groups:
                solved_words.update(word.upper() for word in group.words)

        for word in words:
            if word in solved_words:
                return f"Word '{word}' is from an already solved group"

        return None

    def _render_prompt_template(self, puzzle_id: int, difficulty: float, words: List[str]) -> str:
        """Render the prompt template with puzzle data."""
        words_str = ", ".join(words)
        return (self.prompt_template
                .replace("{{WORDS}}", words_str)
                .replace("{{PUZZLE_ID}}", str(puzzle_id))
                .replace("{{DIFFICULTY}}", str(difficulty)))

    def rank_puzzle(
        self, puzzle_id: int, runs: int, model_name: str
    ) -> PuzzleDifficultyResult:
        """
        Rank a puzzle's difficulty by running it multiple times.

        Public API that accepts a puzzle_id. Delegates to _rank_puzzle().
        """
        if self.logger is None:
            # Timestamp the base id so each rank invocation gets its own run_id
            # (and OpenRouter session key), matching the eval `run` path.
            self.run_id = f"rank_{datetime.utcnow().strftime('%Y-%m-%dT%H-%M-%S')}_{model_name}"
            self.logger = setup_logger(self.log_path, self.run_id, verbose=self.verbose)

        puzzle_map = {p.id: p for p in self.puzzles}
        if puzzle_id not in puzzle_map:
            raise ValueError(f"Puzzle ID {puzzle_id} not found")
        return self._rank_puzzle(puzzle_map[puzzle_id], runs, model_name)

    def _rank_puzzle(
        self, puzzle: Puzzle, runs: int, model_name: str
    ) -> PuzzleDifficultyResult:
        """Rank a single puzzle by running it multiple times."""
        wins = 0
        total_guesses = 0
        total_mistakes = 0

        for i in range(runs):
            run_rng = random.Random(self.seed + puzzle.id + i)
            stats = self._run_puzzle_ai(puzzle, model_name, run_rng, attempt=i)

            if stats.won:
                wins += 1
            total_guesses += stats.guess_count
            total_mistakes += stats.mistake_count

        return PuzzleDifficultyResult(
            puzzle_id=puzzle.id,
            runs=runs,
            wins=wins,
            solve_rate=wins / runs if runs > 0 else 0.0,
            avg_guesses=total_guesses / runs if runs > 0 else 0.0,
            avg_mistakes=total_mistakes / runs if runs > 0 else 0.0,
            model=model_name,
        )

    def rank_all_puzzles(
        self, runs_per_puzzle: int, model_name: str, threads: int = 1
    ) -> List[PuzzleDifficultyResult]:
        """
        Rank all puzzles by difficulty.

        Returns:
            List of PuzzleDifficultyResult sorted by solve_rate ascending (hardest first)
        """
        # Ensure logger is set up for ranking
        if self.logger is None:
            # Timestamp the base id so each rank invocation gets its own run_id
            # (and OpenRouter session key), matching the eval `run` path.
            self.run_id = f"rank_{datetime.utcnow().strftime('%Y-%m-%dT%H-%M-%S')}_{model_name}"
            self.logger = setup_logger(self.log_path, self.run_id, verbose=self.verbose)

        all_puzzles = list(self.puzzles)

        if threads <= 1:
            results = [self._rank_puzzle(p, runs_per_puzzle, model_name) for p in all_puzzles]
            return sorted(results, key=lambda r: r.solve_rate)

        results: List[PuzzleDifficultyResult] = []
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = {
                executor.submit(self._rank_puzzle, p, runs_per_puzzle, model_name): p
                for p in all_puzzles
            }
            for future in as_completed(futures):
                results.append(future.result())

        return sorted(results, key=lambda r: r.solve_rate)

    def _get_remaining_words(self, state: GameState) -> List[str]:
        """Get words that are still available (not from solved groups)."""
        solved_words = set()
        for group in state.puzzle.groups:
            if group.color in state.solved_groups:
                solved_words.update(word.upper() for word in group.words)

        all_words = set(word.upper() for word in state.puzzle.words)
        remaining_words = all_words - solved_words
        return list(remaining_words)
