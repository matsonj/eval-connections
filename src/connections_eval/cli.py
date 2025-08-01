"""CLI interface for connections_eval."""

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .core import ConnectionsGame

app = typer.Typer(help="Evaluate AI models on New York Times Connections puzzles")
console = Console()

def main():
    """Entry point for CLI."""
    app()

@app.command()
def run(
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Model to evaluate (grok3, grok4, o3, o4-mini, gpt4, gpt4-turbo, gemini, sonnet, opus)"
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        help="Run in interactive mode (human player)"
    ),
    puzzles: Optional[int] = typer.Option(
        None,
        "--puzzles",
        help="Maximum number of puzzles to run (default: all)"
    ),
    seed: Optional[int] = typer.Option(
        None,
        "--seed",
        help="Random seed for reproducibility"
    ),
    inputs_path: Path = typer.Option(
        Path("inputs"),
        "--inputs-path",
        help="Path to inputs directory"
    ),
    log_path: Path = typer.Option(
        Path("logs"),
        "--log-path", 
        help="Path to logs directory"
    ),
    prompt_file: str = typer.Option(
        "prompt_template.xml",
        "--prompt-file",
        help="Prompt template file name"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose", "-v",
        help="Print logs to terminal for debugging"
    )
):
    """Run connections evaluation."""
    
    # Validate arguments
    if not interactive and not model:
        console.print("❌ Either --model or --interactive must be specified", style="red")
        raise typer.Exit(1)
    
    if interactive and model:
        console.print("❌ Cannot specify both --model and --interactive", style="red") 
        raise typer.Exit(1)
    
    # Validate model
    if model and model not in ConnectionsGame.MODEL_CONFIG:
        console.print(f"❌ Unknown model: {model}", style="red")
        console.print("Available models:", style="yellow")
        for model_name in ConnectionsGame.MODEL_CONFIG.keys():
            console.print(f"  - {model_name}")
        raise typer.Exit(2)
    
    # Check OpenRouter API key for non-interactive mode
    if not interactive:
        if not os.getenv("OPENROUTER_API_KEY"):
            console.print("❌ OPENROUTER_API_KEY environment variable not set", style="red")
            raise typer.Exit(1)
    
    # Validate inputs path
    if not inputs_path.exists():
        console.print(f"❌ Inputs path does not exist: {inputs_path}", style="red")
        raise typer.Exit(1)
    
    puzzles_file = inputs_path / "connections_puzzles.yml"
    template_file = inputs_path / prompt_file
    
    if not puzzles_file.exists():
        console.print(f"❌ Puzzles file not found: {puzzles_file}", style="red")
        raise typer.Exit(1)
    
    if not template_file.exists():
        console.print(f"❌ Prompt template not found: {template_file}", style="red")
        raise typer.Exit(1)
    
    # Get model name for interactive mode
    if interactive:
        model_name = typer.prompt("Enter a label for this run (for logging)")
    else:
        model_name = model
    
    # Initialize game
    try:
        game = ConnectionsGame(inputs_path, log_path, seed, verbose=verbose)
        
        # Show run info
        console.print(f"🎮 Starting Connections evaluation", style="bold blue")
        console.print(f"Mode: {'Interactive' if interactive else f'AI Model ({model})'}")
        console.print(f"Puzzles: {puzzles or 'all'}")
        console.print(f"Seed: {game.seed}")
        console.print(f"Log path: {log_path}")
        console.print()
        
        # Run evaluation
        summary = game.run_evaluation(model_name, puzzles, interactive)
        
        # Display results
        _display_summary(summary, interactive)
        
    except KeyboardInterrupt:
        console.print("\n❌ Evaluation interrupted", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"❌ Error: {e}", style="red")
        raise typer.Exit(1)


def _display_summary(summary: dict, interactive: bool):
    """Display evaluation summary."""
    console.print("📊 Evaluation Results", style="bold green")
    
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    
    table.add_row("Model/Run", summary["model"])
    table.add_row("Puzzles Attempted", str(summary["puzzles_attempted"]))
    table.add_row("Puzzles Solved", str(summary["puzzles_solved"]))
    
    if summary["puzzles_attempted"] > 0:
        solve_rate = summary["puzzles_solved"] / summary["puzzles_attempted"] * 100
        table.add_row("Solve Rate", f"{solve_rate:.1f}%")
    
    table.add_row("Total Guesses", str(summary["total_guesses"]))
    table.add_row("Correct Guesses", str(summary["correct_guesses"]))
    table.add_row("Incorrect Guesses", str(summary["incorrect_guesses"]))
    table.add_row("Invalid Responses", str(summary["invalid_responses"]))
    
    if summary["total_guesses"] > 0:
        accuracy = summary["correct_guesses"] / summary["total_guesses"] * 100
        table.add_row("Guess Accuracy", f"{accuracy:.1f}%")
    
    table.add_row("Average Time", f"{summary['avg_time_sec']:.1f}s")
    
    if not interactive:
        table.add_row("Total Tokens", str(summary["total_tokens"]))
        table.add_row("Token Method", summary["token_count_method"])
    
    table.add_row("Seed", str(summary["seed"]))
    
    console.print(table)
    console.print()
    
    # Show log file location
    console.print(f"📝 Detailed logs saved to: {summary['run_id']}", style="dim")


@app.command()
def list_models():
    """List available models."""
    console.print("Available models:", style="bold blue")
    
    for model_name in ConnectionsGame.MODEL_CONFIG.keys():
        console.print(f"  {model_name}")


if __name__ == "__main__":
    main()
