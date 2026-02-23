# AGENT.md - Connections Eval Project

## Commands
- **Run tests**: `uv run pytest` (all tests) or `uv run pytest tests/test_cli.py::test_specific` (single test)
- **Run app**: `uv run connections_eval run --model MODEL_NAME` or `uv run connections_eval run --interactive`
- **Run parallel**: `uv run connections_eval run --model MODEL_NAME --puzzles 10 --threads 8`
- **Run specific puzzles**: `uv run connections_eval run --model MODEL_NAME --puzzle-ids 246,283,477`
- **Run canonical set**: `uv run connections_eval run --model MODEL_NAME --canonical`
- **List models**: `uv run connections_eval list-models`
- **List puzzles**: `uv run connections_eval list-puzzles` (add `--difficulty` for ratings)
- **Rank puzzles**: `uv run connections_eval rank --model MODEL_NAME --runs 5 --threads 4`
- **Rank single puzzle**: `uv run connections_eval rank --puzzle-id 246 --runs 10`
- **Install deps**: `uv sync`
- **Extract data**: `uv run python scripts/extract_summaries.py` (creates results/run_summaries.csv)
- **Generate table**: `uv run python scripts/create_results_table_gt.py` (creates results/results_table_gt.html)

## Architecture
- **Core**: `src/connections_eval/core.py` - Game logic, puzzle handling, metrics
- **CLI**: `src/connections_eval/cli.py` - Typer-based command interface
- **Adapters**: `src/connections_eval/adapters/openrouter_adapter.py` - Unified OpenRouter integration for 200+ AI models
- **Utils**: `src/connections_eval/utils/` - Timing, tokens, logging, retry utilities
- **Data**: `inputs/connections_puzzles.yml` (puzzles), `inputs/prompt_template.xml` (prompts), `inputs/model_mappings.yml` (model ID mappings), `inputs/test_battery.yml` (test model list)
- **Logs**: JSONL format in `logs/` directory with detailed exchange and summary data
- **Scripts**: `scripts/` - Analysis and visualization tools for processing evaluation results
- **Results**: `results/` - Generated CSV data and HTML reports

## Key Data Types
- **`PuzzleResult`**: Dataclass returned by `_run_puzzle_ai()` — per-puzzle outcome (won, guesses, tokens, cost)
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
