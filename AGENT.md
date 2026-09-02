# AGENT.md - Connections Eval Project

## Commands
- **Run tests**: `uv run pytest` (all tests) or `uv run pytest tests/test_cli.py::test_specific` (single test)
- **Run app**: `uv run connections_eval run --model MODEL_NAME` or `uv run connections_eval run --interactive`
- **Run parallel**: `uv run connections_eval run --model MODEL_NAME --puzzles 10 --threads 8`
- **Run specific puzzles**: `uv run connections_eval run --model MODEL_NAME --puzzle-ids 246,283,477`
- **Run canonical set**: `uv run connections_eval run --model MODEL_NAME --canonical`
- **Run one-shot mode**: `uv run connections_eval run --model MODEL_NAME --mode oneshot --canonical` (single submission of all 4 groups per puzzle, base 0/1/2/3 + 2-pt trap bonus)
- **Set reasoning effort**: `uv run connections_eval run --model MODEL_NAME --reasoning-effort high` (thinking models only; default: xhigh)
- **Backfill one-shot runs**: `uv run python scripts/backfill_oneshot.py --dry-run` (preview), then without the flag to run (models first seen <90d ago OR ≥75% classic solve rate)
- **List models**: `uv run connections_eval list-models`
- **List puzzles**: `uv run connections_eval list-puzzles` (add `--difficulty` for ratings)
- **Rank puzzles**: `uv run connections_eval rank --model MODEL_NAME --runs 5 --threads 4`
- **Rank single puzzle**: `uv run connections_eval rank --puzzle-id 246 --runs 10`
- **Install deps**: `uv sync`
- **Extract data**: `uv run python scripts/extract_summaries.py` (creates results/run_summaries.csv)
- **Generate leaderboards**: `uv run python scripts/create_results_mviz.py` (docs/index.html = one-shot, docs/classic.html = classic multi-turn)

## Architecture
- **Core**: `src/connections_eval/core.py` - Game logic, puzzle handling, metrics. Both runners (`_run_puzzle_ai` classic, `_run_puzzle_oneshot_ai` one-shot) share `_run_exchange()` for transport + accounting + telemetry (including the API-error path) and supply only a per-mode verdict callback returning a `_Verdict`; keep mode-specific result strings and log fields in those callbacks
- **Linter**: `src/connections_eval/linter.py` - Structural-only checks of a one-shot response against the RESPONSE FORMAT (`lint_oneshot`, `feedback_message`, `splice_segment`); never reveals correctness. One-shot mode spends up to `ConnectionsGame.MAX_LINT_RETRIES` extra exchanges: a missing answer gets a full-response continuation telling the model to finish, while other failures ask for just the failed segment (logged as `LINT_RETRY_<rule>`, then scored)
- **Structured output (opt-in)**: `--structured-output` sends an OpenRouter `response_format` JSON schema (`src/connections_eval/structured.py`), swaps the prompt's RESPONSE FORMAT section for JSON instructions, caps `max_tokens` for thinking models, and renders the JSON back into the XML-ish text protocol in `_extract_content` so parsers, linter and logs are unchanged. Not comparable with default runs
- **Partial-response retry**: `openrouter_adapter.chat` raises `PartialResponseError` (retried by `retry_with_backoff`) when a 200 carries `choices` but `usage.completion_tokens == 0` — a transient provider fault (seen with inception/mercury-2.5-preview). Every exchange log record carries `finish_reason`, `native_finish_reason`, `provider`, `usage`
- **CLI**: `src/connections_eval/cli.py` - Typer-based command interface
- **Adapters**: `src/connections_eval/adapters/openrouter_adapter.py` - Unified OpenRouter integration for 200+ AI models
- **Utils**: `src/connections_eval/utils/` - Timing, tokens, logging, retry utilities
- **Data**: `inputs/connections_puzzles.yml` (puzzles; canonical ones carry `valid_trap_groups` — human-reviewed one-shot trap annotations, size >= 4, supersets allowed), `inputs/prompt_template.xml` (classic prompts), `inputs/prompt_template_oneshot.xml` (one-shot prompts), `inputs/model_mappings.yml` (model ID mappings), `inputs/test_battery.yml` (test model list)
- **Logs**: JSONL format in `logs/` directory with detailed exchange and summary data
- **Scripts**: `scripts/` - Analysis and visualization tools for processing evaluation results
- **Results**: `results/` - Generated CSV data and HTML reports

## Key Data Types
- **`PuzzleResult`**: Dataclass returned by `_run_puzzle_ai()` — per-puzzle outcome (won, guesses, tokens, cost); one-shot mode also sets `score`/`groups_correct`
- **`EvalStats`**: Dataclass with `accumulate(result)` method — aggregates `PuzzleResult`s across a run
- **`PuzzleDifficultyResult`**: Dataclass returned by `rank_puzzle()` — solve rate, avg guesses/mistakes
- **`GameState`**: Mutable dataclass tracking in-progress game state

## Code Style
- **Imports**: Standard library first, then third-party, then local imports
- **Types**: Use `typing` annotations (Dict, List, Optional, etc.) with dataclasses for structured data
- **Naming**: Snake_case for functions/variables, PascalCase for classes
- **Strings**: Use f-strings for formatting, XML templates for prompts
- **Error handling**: Retry with exponential backoff for API calls, fail-fast for missing API keys
- **CLI**: Use Typer with rich console output; validation logic in `_validate_run_args()`
- **Thread safety**: `_run_puzzle_ai()` takes an explicit `rng: random.Random` parameter — never mutate `self.rng` from threads
- **Provider pinning**: `extract_provider_slug()` maps model ID prefix to OpenRouter provider slug; only known first-party providers are pinned (anthropic, openai, google-ai-studio, xai)
