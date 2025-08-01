# AGENT.md - Connections Eval Project

## Commands
- **Run tests**: `uv run pytest` (all tests) or `uv run pytest tests/test_cli.py::test_specific` (single test)  
- **Run app**: `uv run connections_eval run --model MODEL_NAME` or `uv run connections_eval run --interactive`
- **List models**: `uv run connections_eval list-models`
- **Install deps**: `uv sync`

## Architecture
- **Core**: `src/connections_eval/core.py` - Game logic, puzzle handling, metrics
- **CLI**: `src/connections_eval/cli.py` - Typer-based command interface
- **Adapters**: `src/connections_eval/adapters/openrouter_adapter.py` - Unified OpenRouter integration for 200+ AI models
- **Utils**: `src/connections_eval/utils/` - Timing, tokens, logging, retry utilities
- **Data**: `inputs/connections_puzzles.yml` (puzzles), `inputs/prompt_template.xml` (prompts)
- **Logs**: JSONL format in `logs/` directory with detailed exchange and summary data

## Code Style
- **Imports**: Standard library first, then third-party, then local imports
- **Types**: Use `typing` annotations (Dict, List, Optional, etc.) with dataclasses for structured data
- **Naming**: Snake_case for functions/variables, PascalCase for classes
- **Strings**: Use f-strings for formatting, XML templates for prompts  
- **Error handling**: Retry with exponential backoff for API calls, fail-fast for missing API keys
- **CLI**: Use Typer with rich console output, structured options with type hints
