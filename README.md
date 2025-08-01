# Connections Eval

Evaluate AI models (or humans) on New York Times *Connections* puzzles.

## Overview

This project provides a comprehensive evaluation framework for testing linguistic reasoning capabilities using *Connections* puzzles. It supports multiple AI model vendors and includes both batch evaluation and interactive modes.

## Features

- **Multi-model support**: Access 200+ AI models through OpenRouter (OpenAI, Anthropic, xAI, Google Gemini, and more)
- **Interactive mode**: Human players can test their skills
- **Comprehensive metrics**: Track guesses, errors, time, and token usage
- **Reproducible**: Controlled randomization with optional seeds
- **Detailed logging**: JSONL format for analysis

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

# Run evaluation
uv run connections_eval run --model o3 --puzzles 5
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
  --seed 42 \
  --inputs-path ./custom-inputs \
  --log-path ./custom-logs
```

## Supported Models

All models are accessed through OpenRouter using a single API key. Set your OpenRouter API key:

```bash
export OPENROUTER_API_KEY="your-openrouter-key"
```

| CLI Name      | OpenRouter Model ID        | Description                     |
|---------------|----------------------------|---------------------------------|
| `grok3`       | `xai/grok-3`              | xAI Grok-3                      |
| `grok4`       | `xai/grok-4`              | xAI Grok-4                      |
| `o3`          | `openai/o3`               | OpenAI o3                       |
| `o4-mini`     | `openai/o4-mini`          | OpenAI o4-mini                  |
| `gpt4`        | `openai/gpt-4`            | OpenAI GPT-4                    |
| `gpt4-turbo`  | `openai/gpt-4-turbo`      | OpenAI GPT-4 Turbo              |
| `gemini`      | `google/gemini-2.5-pro`   | Google Gemini 2.5 Pro           |
| `sonnet`      | `anthropic/claude-3.5-sonnet` | Anthropic Claude 3.5 Sonnet |
| `opus`        | `anthropic/claude-3-opus` | Anthropic Claude 3 Opus         |

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
- Time and token usage
- Detailed breakdown by puzzle

### JSONL Logs

Detailed logs are saved to `logs/connections_eval_<timestamp>.jsonl`:

```json
{"timestamp": "2025-07-31T17:23:08Z", "run_id": "...", "model": "o3", ...}
```

Each line contains either:
- **Exchange log**: Individual guess with request/response/timing
- **Summary log**: Final run statistics

## CLI Reference

```bash
uv run connections_eval run [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--model` | str | None | Model to evaluate (required unless `--interactive`) |
| `--interactive` | flag | False | Run in interactive mode |
| `--puzzles` | int | All | Maximum puzzles to run |
| `--seed` | int | Random | Random seed for reproducibility |
| `--inputs-path` | path | `inputs/` | Input files directory |
| `--log-path` | path | `logs/` | Log output directory |
| `--prompt-file` | str | `prompt_template.xml` | Prompt template filename |

### Examples

```bash
# Basic evaluation
uv run connections_eval run --model o3

# Limited run with seed
uv run connections_eval run --model gemini --puzzles 3 --seed 42

# Interactive play
uv run connections_eval run --interactive

# Custom paths
uv run connections_eval run --model sonnet --inputs-path ./my-puzzles
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
│   └── openrouter_adapter.py  # OpenRouter unified adapter
└── utils/              # Utilities
    ├── timing.py       # Timer utilities
    ├── tokens.py       # Token counting
    ├── logging.py      # JSON logging
    └── retry.py        # Retry with backoff
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
- **Token Usage**: Total tokens (when available from API)

## License

MIT License - see [LICENSE](LICENSE) file.

## Contributing

1. Ensure Python ≥3.12 and `uv` are installed
2. Run tests: `uv run pytest`
3. Follow existing code patterns and add tests for new features
4. Update documentation as needed

---

For questions or issues, please check the logs in `logs/` directory for detailed debugging information.
