# Connections Eval

Evaluate AI models (or humans) on New York Times *Connections* puzzles.

## Overview

This project provides a comprehensive evaluation framework for testing linguistic reasoning capabilities using *Connections* puzzles. It supports 200+ AI models through a unified OpenRouter integration and includes both batch evaluation and interactive modes.

## Features

- **Multi-model support**: Access 200+ AI models through OpenRouter (OpenAI, Anthropic, xAI, Google Gemini, and more)
- **Reasoning model support**: Full support for reasoning models (GPT-5, o3, Grok-4, etc.) with proper parameter handling
- **Parallel execution**: Run puzzles concurrently with configurable thread count (default 8)
- **Provider pinning**: Automatic prompt caching by pinning to native providers on multi-turn calls
- **Canonical puzzle sets**: Define consistent puzzle sets for reproducible model comparisons
- **Difficulty ranking**: Rank puzzle difficulty by running puzzles multiple times
- **Interactive mode**: Human players can test their skills
- **Cost tracking**: Separate tracking of OpenRouter and upstream provider costs, with cache discount visibility
- **Detailed token metrics**: Breakdown of prompt vs completion tokens with cache hit tracking
- **Verbose logging**: Real-time exchange logging with `--verbose` flag
- **Reproducible**: Controlled randomization with optional seeds and deterministic puzzle selection
- **Detailed logging**: JSONL format for analysis

## Changelog

### 4.0.0 (2026-07-21)
- **One-shot mode** (`--mode oneshot`) — model submits all 4 groups in a single response instead of guessing one group at a time; one API call per puzzle, no feedback loop
- **One-shot scoring** — 1 point per correctly matched group, 3 points for a perfect solve (exactly-3 is impossible), giving base scores of 0/1/2/3 per puzzle
- **Trap detection bonus** — canonical puzzles are annotated with `valid_trap_groups` (human-reviewed "trap sets": coherent cross-cutting categories that aren't real groups; supersets of 5+ mark overloaded categories where any qualifying 4-subset counts). A trap never takes 3+ words from a single real group — "a real group with one word swapped" is not a trap, and the scorer enforces this. Models claim their single most likely trap set of exactly 4 words (or `N/A`); only the first claim is judged: +2 if it matches a designed trap or `N/A` is correctly claimed on a trap-free puzzle; an incorrect claim earns nothing. Max 5/puzzle → 100 over the 20-puzzle canonical set
- Only one guess is allowed per puzzle in one-shot mode; a structurally invalid submission (wrong word count, duplicate/foreign words, etc.) scores 0 including the trap bonus — there is no retry
- Trap scoring applies only to puzzles reviewed for traps (all 20 canonical puzzles are). Unreviewed puzzles score base-only with a per-puzzle max of 3; the prompt's trap offer is inert there — run `--canonical` for comparable scores
- `--mode classic|oneshot` flag on `run` (default `classic`); classic multi-turn behavior is unchanged
- **One-shot is now the primary eval** — the leaderboard (`docs/index.html`) shows one-shot runs only; the classic multi-turn leaderboard moved to `docs/classic.html`, and the GitHub Action defaults to `mode: oneshot`
- **Backfill driver** (`scripts/backfill_oneshot.py`) — runs the canonical set in one-shot mode for models first seen in the last 90 days or scoring ≥75% in classic; `--dry-run` to preview
- `--reasoning-effort minimal|low|medium|high|xhigh` flag on `run` — overrides the reasoning effort for thinking models (previously hardcoded to `minimal`; non-thinking models ignore it)

### 3.0.0 (2026-02-23)
- **Provider pinning** for prompt caching — pins OpenRouter requests to the native provider (Anthropic, OpenAI, Google, xAI) on calls 2+, enabling prompt cache hits across multi-turn puzzle conversations
- **Parallel execution** — run puzzles concurrently with `--threads N` (default 8); thread-safe RNG per puzzle
- **Canonical puzzle sets** — mark puzzles with `canonical: true` in YAML; run with `--canonical` for consistent model comparisons
- **Puzzle selection** — `--puzzle-ids 246,283,477` for specific puzzles in order
- **Difficulty ranking** — `rank` command to measure puzzle difficulty across multiple runs
- **`list-puzzles` command** — browse puzzles with optional difficulty ratings
- **Typed internals** — `PuzzleResult` and `EvalStats` dataclasses replace raw dicts; thread-safe architecture
- **Cache visibility** — logs `cached_tokens` and `cache_discount` when providers report cache hits

### 2.0.1 (2025-10-01)
- Improved logging docs to reflect the current JSONL file naming and controllog outputs
- Clarified CLI examples for reasoning models and verbose logging
- Version bump; no breaking API changes

## Installation

Requires Python ≥3.12 and [uv](https://github.com/astral-sh/uv).

```bash
git clone <repository>
cd eval-connections
uv sync
```

## Quick Start

### List Available Models

```bash
uv run connections_eval list-models
```

### Run AI Model Evaluation

```bash
# Set OpenRouter API key
export OPENROUTER_API_KEY="your-key-here"

# Run evaluation with verbose logging (8 threads by default)
uv run connections_eval run --model gpt5 --puzzles 5 --verbose

# Run specific puzzles for consistent comparison
uv run connections_eval run --model grok4 --puzzle-ids 246,283,477

# Run canonical puzzle set
uv run connections_eval run --model gemini --canonical
```

### List and Rank Puzzles

```bash
# Browse available puzzles with difficulty ratings
uv run connections_eval list-puzzles --difficulty

# Rank puzzle difficulty (5 runs per puzzle)
uv run connections_eval rank --model sonnet-4 --runs 5 --threads 4
```

### Interactive Mode

```bash
uv run connections_eval run --interactive
```

### Custom Configuration

```bash
uv run connections_eval run \
  --model gemini \
  --puzzles 3 \
  --threads 4 \
  --seed 42 \
  --inputs-path ./custom-inputs \
  --log-path ./custom-logs
```

## Supported Models

All models are accessed through OpenRouter using a single API key. Set your OpenRouter API key:

```bash
export OPENROUTER_API_KEY="your-openrouter-key"
```

### Available Models

Models are configured in `inputs/model_mappings.yml`. Here are some popular options:

| CLI Name      | OpenRouter Model ID           | Type                   | Description                     |
|---------------|-------------------------------|------------------------|---------------------------------|
| `gpt5`        | `openai/gpt-5`               | Reasoning              | OpenAI GPT-5 (latest)          |
| `gpt5-mini`   | `openai/gpt-5-mini`          | Reasoning              | OpenAI GPT-5 Mini               |
| `gpt5-nano`   | `openai/gpt-5-nano`          | Reasoning              | OpenAI GPT-5 Nano               |
| `gpt-oss-120b`| `openai/gpt-oss-120b`        | Reasoning              | OpenAI GPT OSS 120B             |
| `gpt-oss-20b` | `openai/gpt-oss-20b`         | Reasoning              | OpenAI GPT OSS 20B              |
| `o3`          | `openai/o3`                  | Reasoning              | OpenAI o3                       |
| `o3-mini`     | `openai/o3-mini`             | Reasoning              | OpenAI o3-mini                  |
| `grok4`       | `x-ai/grok-4`                | Reasoning              | xAI Grok-4                      |
| `grok3`       | `x-ai/grok-3`                | Standard               | xAI Grok-3                      |
| `grok3-mini`  | `x-ai/grok-3-mini`           | Reasoning              | xAI Grok-3 Mini                 |
| `opus-4.1`    | `anthropic/claude-opus-4.1`  | Standard               | Anthropic Claude Opus 4.1       |
| `fable-5`     | `anthropic/claude-fable-5`   | Reasoning              | Anthropic Claude Fable 5        |
| `sonnet`      | `anthropic/claude-3.5-sonnet`| Standard               | Anthropic Claude 3.5 Sonnet     |
| `gemini`      | `google/gemini-2.5-pro`      | Reasoning              | Google Gemini 2.5 Pro           |

**Reasoning Models**: Automatically handled with special parameter configurations (no `max_tokens`, `temperature`, etc.)

## Game Rules

Each puzzle contains 16 words that form 4 groups of 4 related words:

1. **Objective**: Find all 4 groups by guessing 4 related words at a time
2. **Attempts**: Maximum 6 total guesses (4 mistakes allowed)
3. **Response Format**: Exactly 4 words, ALL CAPS, comma-separated
4. **Feedback**: Only "CORRECT" or "INCORRECT" (no hints)
5. **Invalid Responses**: Wrong word count, duplicates, or words not in puzzle

## Input Files

### Puzzles (`inputs/connections_puzzles.yml`)

```yaml
puzzles:
  - id: 476
    date: 2024-09-29
    difficulty: 4.5
    canonical: true        # Include in canonical puzzle set (optional)
    words: [BLANKET, SHAM, SHEET, THROW, ...]
    groups:
      - name: Bedding
        color: green
        words: [BLANKET, SHAM, SHEET, THROW]
      # ... 3 more groups
```

### Prompt Template (`inputs/prompt_template.xml`)

```xml
<system>
You are an expert puzzle-solver. Follow the rules strictly.
</system>
<user>
HOW TO PLAY
1. Guess 4 related words.
2. You'll be told only "Correct" or "Incorrect".
3. You have at most 6 total guesses (2 correct + 4 mistakes).

Respond with EXACTLY four words, ALL CAPS, comma-separated.
</user>

<puzzle>{{WORDS}}</puzzle>
<id>{{PUZZLE_ID}}</id>
<difficulty>{{DIFFICULTY}}</difficulty>
```

## Output & Logging

### Console Output

Results are displayed in a formatted table showing:
- Solve rate and guess accuracy  
- Performance metrics (time, tokens)
- Detailed token breakdown (prompt vs completion tokens)
- Cost tracking (OpenRouter cost vs upstream provider cost)
- Comprehensive evaluation statistics

Example output:
```
📊 Evaluation Results
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Metric            ┃ Value      ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ Puzzles Solved    │ 4          │
│ Solve Rate        │ 80.0%      │
│ Total Tokens      │ 12847      │
│ Prompt Tokens     │ 1203       │
│ Completion Tokens │ 11644      │
│ OpenRouter Cost   │ $0.001354  │
│ OpenAI Cost       │ $0.027087  │
└───────────────────┴────────────┘
```

### JSONL Logs

Detailed logs are saved to `logs/connections_eval_<timestamp>.jsonl`:

```json
{"timestamp": "2025-07-31T17:23:08Z", "run_id": "...", "model": "o3", ...}
```

Each line contains either:
- **Exchange log**: Individual guess with request/response/timing
- **Summary log**: Final run statistics

### Controllable Logging (controllog)

This project also emits accounting-style telemetry as structured, balanced events:

- Files: `logs/controllog/YYYY-MM-DD/events.jsonl` and `logs/controllog/YYYY-MM-DD/postings.jsonl`
- IDs: UUIDv7 for `event_id` and `posting_id` (sortable by time)
- Run/Task identifiers:
  - `run_id`: e.g., `2025-10-01T12-30-00_grok3`
  - `task_id`: `T{puzzle_id}:{run_id}` (one task per puzzle attempt)
  - `agent_id`: `agent:connections_eval`
- Accounts used (balanced per event):
  - `resource.tokens` (provider ↔ project)
  - `resource.time_ms` (agent ↔ project)
  - `resource.money` (vendor ↔ project) for OpenRouter and optional upstream
  - `truth.state` (task WIP→DONE/FAILED)
  - `value.utility` (optional reward; task ↔ project)

The SDK initializes automatically at run start; raw payloads are preserved.

#### Automatic Upload to MotherDuck

The evaluation automatically uploads controllog files to MotherDuck after each run (if configured). This includes validation and trial balance checks.

**Setup:**
1. Set your MotherDuck token in your environment (e.g., in a `.env` file):
   ```bash
   export MOTHERDUCK_TOKEN="your-token-here"
   export MOTHERDUCK_DB="md:"  # Optional: "md:" for default database, or "md:database_name" for a specific database
   ```

   **Note:** If `MOTHERDUCK_DB` is not set, the upload step will be skipped. Use `"md:"` to connect to your default MotherDuck database, or `"md:database_name"` if you've created a specific database.

2. Run an evaluation - upload happens automatically:
   ```bash
   uv run connections_eval run --model grok3 --puzzles 2
   ```

The upload process:
- Uploads controllog events and postings to MotherDuck
- Validates that the run's data exists in the database
- Runs a trial balance check to ensure data integrity
- Optionally deletes local controllog files (use `--keep-local-files` to retain them)

**Manual Upload (if needed):**

If you need to manually upload controllog files:

```bash
# Set your token and database
export MOTHERDUCK_DB="md:"  # or "md:database_name" for a specific database
export CTRL_LOG_DIR="logs"
uv run python scripts/load_controllog_to_motherduck.py

# Alternatively, load into a local DuckDB file
export MOTHERDUCK_DB="controllog.duckdb"
uv run python scripts/load_controllog_to_motherduck.py
```

#### Reports and Trial Balance

Run a fast trial balance check (double-entry invariants) and example reports:

```bash
export MOTHERDUCK_DB="md:"   # or "md:database_name" or a local .duckdb path
uv run python scripts/reports_controllog.py
```

Outputs include:
- Trial balance PASS/FAIL
- Cost and utility flows per project
- Average wall latency by model

## CLI Reference

### `run` — Run evaluation

```bash
uv run connections_eval run [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--model` | str | None | Model to evaluate (required unless `--interactive`) |
| `--interactive` | flag | False | Run in interactive mode |
| `--puzzles` | int | All | Maximum puzzles to run (random subset) |
| `--puzzle-ids` | str | None | Comma-separated puzzle IDs to run (e.g. `246,283,477`) |
| `--canonical` | flag | False | Run only puzzles marked `canonical: true` |
| `--mode` | str | `classic` | Evaluation mode: `classic` (multi-turn guessing) or `oneshot` (single submission of all 4 groups) |
| `--threads` | int | 8 | Number of parallel threads (forced to 1 for interactive) |
| `--seed` | int | Random | Random seed for reproducibility |
| `--verbose` | flag | False | Enable real-time exchange logging |
| `--inputs-path` | path | `inputs/` | Input files directory |
| `--log-path` | path | `logs/` | Log output directory |
| `--prompt-file` | str | `prompt_template.xml` | Prompt template filename |
| `--keep-local-files` | flag | False | Keep local controllog files after MotherDuck upload |

`--puzzles`, `--puzzle-ids`, and `--canonical` are mutually exclusive.

### `rank` — Rank puzzle difficulty

```bash
uv run connections_eval rank [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--puzzle-id` | int | None | Rank a single puzzle (omit to rank all) |
| `--runs` | int | 5 | Number of evaluation runs per puzzle |
| `--model` | str | `sonnet-4` | Model to use for ranking |
| `--threads` | int | 4 | Number of parallel threads |
| `--output` | path | None | Save results to YAML file |

### `list-puzzles` — Browse puzzles

```bash
uv run connections_eval list-puzzles [--difficulty]
```

### `list-models` — Show available models

```bash
uv run connections_eval list-models
```

### Examples

```bash
# Basic evaluation (8 threads, all puzzles)
uv run connections_eval run --model gpt5

# Limited run with verbose logging
uv run connections_eval run --model gemini --puzzles 3 --verbose

# Specific puzzles for model comparison
uv run connections_eval run --model grok4 --puzzle-ids 246,283,477,826

# Canonical puzzle set
uv run connections_eval run --model sonnet-4 --canonical

# One-shot mode: single submission of all 4 groups per puzzle (base 0/1/2/3 + 2-pt trap bonus, max 100 over canonical set)
uv run connections_eval run --model sonnet-4 --mode oneshot --canonical

# Single-threaded with fixed seed (fully reproducible)
uv run connections_eval run --model o3 --puzzles 5 --threads 1 --seed 42

# Interactive play
uv run connections_eval run --interactive

# Rank all puzzles and save results
uv run connections_eval rank --model sonnet-4 --runs 10 --output rankings.yml
```

## Development

### Running Tests

```bash
uv run pytest
```

### Project Structure

```
src/connections_eval/
├── cli.py              # Typer CLI interface
├── core.py             # Game logic & metrics
├── adapters/           # AI model adapters
│   └── openrouter_adapter.py  # Unified OpenRouter adapter (200+ models)
└── utils/              # Utilities
    ├── timing.py       # Timer utilities
    ├── tokens.py       # Token counting & cost extraction
    ├── logging.py      # JSON logging
    ├── retry.py        # Retry with backoff
    └── motherduck.py   # MotherDuck upload and validation utilities

inputs/
├── connections_puzzles.yml    # Puzzle database
├── model_mappings.yml         # Model configuration
└── prompt_template.xml        # Prompt template
```

## Error Handling

- **API Failures**: Automatic retry with exponential backoff (3 attempts)
- **Missing API Keys**: Fail fast with clear error message for `OPENROUTER_API_KEY`
- **Invalid Responses**: Track and limit (max 3 per puzzle)
- **Network Issues**: Graceful degradation with detailed logging
- **Reasoning Models**: Special parameter handling for reasoning models (o1, o3, o4) that don't support `max_tokens` or `temperature`

## Metrics

The evaluation tracks comprehensive metrics:

- **Success Rate**: Puzzles solved / attempted
- **Guess Accuracy**: Correct guesses / total guesses  
- **Response Validity**: Invalid responses per puzzle
- **Performance**: Average time per puzzle
- **Token Usage**: 
  - Total tokens consumed
  - Prompt tokens (input to model)
  - Completion tokens (generated by model)
  - Token counting method (API vs approximate)
- **Cost Tracking**:
  - OpenRouter cost (what you pay)
  - Upstream cost (what OpenRouter pays the provider)
  - Per-exchange cost breakdown in logs

## GitHub Actions

You can run evaluations automatically via GitHub Actions workflow dispatch. This is useful for testing new models and adding them to the result set.

### Setup

1. **Configure GitHub Secrets:**
   - `OPENROUTER_API_KEY`: Your OpenRouter API key
   - `MOTHERDUCK_TOKEN`: Your MotherDuck authentication token
   - `MOTHERDUCK_DB`: (Optional) MotherDuck database connection string (defaults to `md:` if not set)

2. **Run the Workflow:**
   - Go to the "Actions" tab in your GitHub repository
   - Select "Run Model Evaluation" workflow
   - Click "Run workflow"
   - Enter the model name (e.g., `gpt5`, `grok4`)
   - Optionally specify the number of puzzles to run
   - Click "Run workflow"

The workflow will:
- Run the evaluation with the specified model
- Upload results to MotherDuck
- Update the docs folder (run summaries and log views)
- Commit and push the updated docs back to the repository

### Example

```yaml
# Workflow dispatch with:
# model: "gpt5"
# puzzles: "10"
```

This will run 10 puzzles with the GPT-5 model and automatically update the documentation.

## Latest Results

[📊 View Interactive Results Table](https://matsonj.github.io/eval-connections/) - Sports-style box score showing latest one-shot model performance ([classic leaderboard](https://matsonj.github.io/eval-connections/classic.html))

*Table includes points scored, costs, token usage, and timing metrics formatted like sports statistics.*

## License

MIT License - see [LICENSE](LICENSE) file.

## Contributing

1. Ensure Python ≥3.12 and `uv` are installed
2. Run tests: `uv run pytest`
3. Follow existing code patterns and add tests for new features
4. Update documentation as needed

---

For questions or issues, please check the logs in `logs/` directory for detailed debugging information.
