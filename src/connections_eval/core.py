"""Core game logic and metrics for Connections puzzles."""

import random
import time
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass

from .utils.timing import Timer
from .utils.tokens import count_tokens, extract_token_usage, extract_cost_info
from .utils.logging import log_exchange, log_summary, setup_logger
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
    VERSION = "2.0.2"  # Fixed reasoning field extraction for thinking models
    
    # Model configuration loaded from YAML file
    MODEL_CONFIG = {}
    
    MAX_GUESSES = 6
    MAX_MISTAKES = 4
    MAX_INVALID = 3
    
    def __init__(self, inputs_path: Path, log_path: Path, seed: Optional[int] = None, verbose: bool = False):
        """
        Initialize the game engine.
        
        Args:
            inputs_path: Path to inputs directory
            log_path: Path to logs directory
            seed: Random seed for reproducibility
            verbose: Whether to print logs to console
        """
        self.inputs_path = inputs_path
        self.log_path = log_path
        self.seed = seed or int(time.time())
        self.verbose = verbose
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
                groups=groups
            )
            puzzles.append(puzzle)
        
        return puzzles
    
    def _load_prompt_template(self) -> str:
        """Load prompt template from XML file."""
        template_file = self.inputs_path / "prompt_template.xml"
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
    
    def run_evaluation(
        self,
        model_name: str,
        max_puzzles: Optional[int] = None,
        is_interactive: bool = False
    ) -> Dict[str, Any]:
        """
        Run evaluation on puzzles.
        
        Args:
            model_name: Name of model to evaluate (or label for interactive)
            max_puzzles: Maximum number of puzzles to run
            is_interactive: Whether to run in interactive mode
            
        Returns:
            Summary statistics
        """
        start_timestamp = datetime.utcnow().isoformat() + "Z"
        self.run_id = f"{datetime.utcnow().strftime('%Y-%m-%dT%H-%M-%S')}_{model_name}"
        self.logger = setup_logger(self.log_path, self.run_id, verbose=self.verbose)
        # Initialize controllog SDK (JSONL transport under logs/controllog)
        try:
            cl.init(project_id="connections_eval", log_dir=self.log_path)
        except Exception:
            # Do not fail the run if telemetry init fails
            pass
        
        # Randomize puzzle order
        puzzles_to_run = self.puzzles.copy()
        self.rng.shuffle(puzzles_to_run)
        
        if max_puzzles:
            puzzles_to_run = puzzles_to_run[:max_puzzles]
        
        # Run puzzles
        total_stats = {
            "puzzles_attempted": 0,
            "puzzles_solved": 0,
            "total_guesses": 0,
            "correct_guesses": 0,
            "incorrect_guesses": 0,
            "invalid_responses": 0,
            "total_time_sec": 0.0,
            "total_tokens": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "token_count_method": "APPROXIMATE",
            "total_cost": 0.0,
            "total_upstream_cost": 0.0,
        }
        
        for puzzle in puzzles_to_run:
            try:
                if is_interactive:
                    stats = self._run_puzzle_interactive(puzzle)
                else:
                    stats = self._run_puzzle_ai(puzzle, model_name)
                
                # Update totals
                total_stats["puzzles_attempted"] += 1
                if stats["won"]:
                    total_stats["puzzles_solved"] += 1
                total_stats["total_guesses"] += stats["guess_count"]
                total_stats["correct_guesses"] += len(stats["solved_groups"])
                total_stats["incorrect_guesses"] += stats["mistake_count"]
                total_stats["invalid_responses"] += stats["invalid_count"]
                total_stats["total_time_sec"] += stats["time_sec"]
                total_stats["total_tokens"] += stats["total_tokens"]
                total_stats["total_prompt_tokens"] += stats.get("total_prompt_tokens", 0)
                total_stats["total_completion_tokens"] += stats.get("total_completion_tokens", 0)
                total_stats["total_cost"] += stats.get("total_cost", 0.0)
                total_stats["total_upstream_cost"] += stats.get("total_upstream_cost", 0.0)
                
                if stats["token_count_method"] == "API":
                    total_stats["token_count_method"] = "API"
                    
            except Exception as e:
                self.logger.error(f"Error running puzzle {puzzle.id}: {e}")
                break
        
        # Calculate averages
        end_timestamp = datetime.utcnow().isoformat() + "Z"
        avg_time = (total_stats["total_time_sec"] / total_stats["puzzles_attempted"] 
                   if total_stats["puzzles_attempted"] > 0 else 0.0)
        
        summary = {
            "run_id": self.run_id,
            "model": model_name,
            "version": self.VERSION,
            "seed": self.seed,
            "avg_time_sec": round(avg_time, 1),
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            **total_stats
        }
        
        log_summary(self.logger, summary)
        return summary
    
    def _run_puzzle_ai(self, puzzle: Puzzle, model_name: str) -> Dict[str, Any]:
        """Run a single puzzle with AI model."""
        model_id = self.MODEL_CONFIG[model_name]
        adapter = openrouter_adapter
        
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
        
        messages = []
        total_tokens = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        token_method = "APPROXIMATE"
        total_cost = 0.0
        total_upstream_cost = 0.0
        task_id = f"T{puzzle.id}:{self.run_id}"
        final_state_emitted = False
        
        # Create first prompt
        shuffled_words = puzzle.words.copy()
        self.rng.shuffle(shuffled_words)
        
        first_prompt = self._render_prompt_template(
            puzzle.id, puzzle.difficulty, shuffled_words
        )
        messages.append({"role": "system", "content": first_prompt.split('<user>')[0].replace('<system>', '').replace('</system>', '').strip()})
        # Get user content and include the puzzle words
        user_section = first_prompt.split('<user>')[1]
        rules_content = user_section.split('</user>')[0].strip()
        puzzle_section = user_section.split('</user>')[1].strip()
        
        # Combine rules and puzzle info
        user_content = rules_content
        if puzzle_section and '<puzzle>' in puzzle_section and '</puzzle>' in puzzle_section:
            words_content = puzzle_section.split('<puzzle>')[1].split('</puzzle>')[0].strip()
            user_content += f"\n\nAvailable words: {words_content}"
        
        messages.append({"role": "user", "content": user_content})
        
        state.start_time = time.time()
        # Emit initial state NEW->WIP for this task
        try:
            cl.state_move(
                task_id=task_id,
                from_="NEW",
                to="WIP",
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
                    response = adapter.chat(messages, model_id)
                    
                    # DEBUG: Log raw response structure for debugging empty responses
                    choice = response["choices"][0]
                    message = choice["message"]
                    content = message.get("content", "")
                    finish_reason = choice.get("finish_reason", "unknown")
                    
                    # Check for extended reasoning in thinking models
                    if not content or content.strip() == "":
                        # Log the full message structure to debug
                        self.logger.warning(f"Empty content field. finish_reason: {finish_reason}")
                        self.logger.warning(f"Message keys: {list(message.keys())}")
                        self.logger.warning(f"Full message: {message}")
                        self.logger.warning(f"Full choice: {choice}")
                        
                        # Check if response was truncated
                        if finish_reason == "length":
                            self.logger.error("Response truncated due to token limit!")
                        
                        # Try to extract from reasoning field if it exists
                        if "reasoning" in message:
                            self.logger.info("Found reasoning field, using it as content")
                            content = message["reasoning"]
                        elif "extended_thinking" in message:
                            self.logger.info("Found extended_thinking field, using it as content")
                            content = message["extended_thinking"]
                    
                    content = content.strip() if content else ""
                    
                    # Parse structured response
                    structured_response = self._parse_structured_response(content)
                    
                    # Track tokens
                    prompt_tokens, completion_tokens, method = extract_token_usage(response)
                    if prompt_tokens and completion_tokens:
                        total_prompt_tokens += prompt_tokens
                        total_completion_tokens += completion_tokens
                        total_tokens += prompt_tokens + completion_tokens
                        token_method = method
                    else:
                        # Approximate
                        prompt_text = " ".join([msg["content"] for msg in messages])
                        approx_prompt = count_tokens(prompt_text)
                        approx_completion = count_tokens(content)
                        total_prompt_tokens += approx_prompt
                        total_completion_tokens += approx_completion
                        total_tokens += approx_prompt + approx_completion
                    
                    # Track costs
                    cost, upstream_cost = extract_cost_info(response)
                    if cost is not None:
                        total_cost += cost
                    if upstream_cost is not None:
                        total_upstream_cost += upstream_cost
                    
                    result = self._process_guess(state, content)
                    
                except Exception as e:
                    # Calculate elapsed time manually since timer context was interrupted
                    elapsed_ms = int((time.time() - timer.start_time) * 1000) if timer.start_time else 0
                    
                    log_exchange(self.logger, {
                        "run_id": self.run_id,
                        "model": model_name,
                        "puzzle_id": puzzle.id,
                        "guess_index": state.guess_count,
                        "request": messages[-1]["content"] if messages else first_prompt,
                        "response": str(e),
                        "latency_ms": elapsed_ms,
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "result": "API_ERROR"
                    })
                    
                    # Log the actual error for debugging
                    self.logger.error(f"API call failed: {str(e)}")
                    # Emit controllog failure event (state move WIP->FAILED)
                    try:
                        cl.event(
                            kind="model_response_error",
                            actor={"agent_id": "agent:connections_eval", "task_id": task_id},
                            run_id=self.run_id,
                            payload={
                                "model": model_name,
                                "puzzle_id": puzzle.id,
                                "error": str(e),
                                "latency_ms": elapsed_ms,
                            },
                            postings=[
                                cl.post("truth.state", f"task:{task_id}", "tasks", -1, {"from": "WIP"}),
                                cl.post("truth.state", f"task:{task_id}", "tasks", +1, {"to": "FAILED"}),
                            ],
                            project_id="connections_eval",
                            source="runtime",
                        )
                        final_state_emitted = True
                    except Exception:
                        pass
                    state.finished = True
                    break
            
            # Log exchange and emit controllog model_response even if this guess finished the game
            exchange_data = {
                    "run_id": self.run_id,
                    "model": model_name,
                    "puzzle_id": puzzle.id,
                    "guess_index": state.guess_count,
                    "request": messages[-1]["content"] if len(messages) > 1 else first_prompt,
                    "response": content,
                    "thinking": structured_response.get('thinking', ''),
                    "guess": structured_response.get('guess', ''),
                    "confidence": structured_response.get('confidence', ''),
                    "latency_ms": timer.elapsed_ms,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "result": result
            }
            
            # Add cost information if available
            if cost is not None:
                exchange_data["cost"] = cost
            if upstream_cost is not None:
                exchange_data["upstream_cost"] = upstream_cost
                
            log_exchange(self.logger, exchange_data)
            # Emit controllog prompt and completion as separate events for auditability
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
                    request_text=messages[-1]["content"] if len(messages) > 1 else first_prompt,
                    payload={"puzzle_id": puzzle.id, "guess_index": state.guess_count},
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
                    wall_ms=timer.elapsed_ms,
                    response_text=content,
                    cost_money=cost,
                    upstream_cost_money=upstream_cost,
                    payload={
                        "puzzle_id": puzzle.id,
                        "guess_index": state.guess_count,
                        "result": result,
                    },
                    exchange_id=exchange_id,
                )
            except Exception:
                pass
                
            # Add response and result to conversation
            messages.append({"role": "assistant", "content": content})
            if not state.finished:
                messages.append({"role": "user", "content": result})
        
        state.end_time = time.time()
        time_sec = state.end_time - state.start_time
        
        result_payload = {
            "won": state.won,
            "guess_count": state.guess_count,
            "mistake_count": state.mistake_count,
            "invalid_count": state.invalid_count,
            "solved_groups": list(state.solved_groups),
            "time_sec": time_sec,
            "total_tokens": total_tokens,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "token_count_method": token_method,
            "total_cost": total_cost,
            "total_upstream_cost": total_upstream_cost
        }
        # Emit final state transition event (WIP->DONE or WIP->FAILED) once per puzzle, unless already emitted on error
        if not final_state_emitted:
            final_state = "DONE" if state.won else "FAILED"
            try:
                cl.state_move(
                    task_id=task_id,
                    from_="WIP",
                    to=final_state,
                    project_id="connections_eval",
                    agent_id="agent:connections_eval",
                    run_id=self.run_id,
                    payload={"puzzle_id": puzzle.id},
                )
            except Exception:
                pass
        return result_payload
    
    def _run_puzzle_interactive(self, puzzle: Puzzle) -> Dict[str, Any]:
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
        
        return {
            "won": state.won,
            "guess_count": state.guess_count,
            "mistake_count": state.mistake_count,
            "invalid_count": state.invalid_count,
            "solved_groups": list(state.solved_groups),
            "time_sec": time_sec,
            "total_tokens": 0,
            "token_count_method": "N/A",
            "total_cost": 0.0,
            "total_upstream_cost": 0.0
        }
    
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
                    # If not finished, ask for next guess
                    return "CORRECT. NEXT GUESS?"
        
        # Incorrect guess
        state.mistake_count += 1
        if state.mistake_count >= self.MAX_MISTAKES:
            state.finished = True
            
        remaining_guesses = self.MAX_MISTAKES - state.mistake_count
        return f"INCORRECT. {remaining_guesses} INCORRECT GUESSES REMAINING"
    
    def _parse_response(self, response: str) -> List[str]:
        """Parse response into list of words, handling structured XML format."""
        import re
        
        # First try to extract from <guess> tags
        guess_match = re.search(r'<guess>(.*?)</guess>', response, re.IGNORECASE | re.DOTALL)
        if guess_match:
            guess_text = guess_match.group(1).strip()
            words = [word.strip().upper() for word in guess_text.split(',')]
            return [word for word in words if word]
        
        # Fallback: try to find 4 comma-separated words in ALL CAPS
        caps_pattern = r'\b[A-Z][A-Z\s]*\b(?:\s*,\s*[A-Z][A-Z\s]*\b){3}'
        caps_match = re.search(caps_pattern, response)
        if caps_match:
            words = [word.strip().upper() for word in caps_match.group().split(',')]
            return [word for word in words if word]
        
        # Final fallback: original comma-split logic
        words = [word.strip().upper() for word in response.split(',')]
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
        
        # Extract thinking section
        thinking_match = re.search(r'<thinking>(.*?)</thinking>', response, re.IGNORECASE | re.DOTALL)
        if thinking_match:
            result['thinking'] = thinking_match.group(1).strip()
        
        # Extract guess section
        guess_match = re.search(r'<guess>(.*?)</guess>', response, re.IGNORECASE | re.DOTALL)
        if guess_match:
            result['guess'] = guess_match.group(1).strip()
        
        # Extract confidence section
        confidence_match = re.search(r'<confidence>(.*?)</confidence>', response, re.IGNORECASE | re.DOTALL)
        if confidence_match:
            result['confidence'] = confidence_match.group(1).strip()
        
        return result
    
    def _validate_guess(self, state: GameState, words: List[str]) -> Optional[str]:
        """
        Validate a guess.
        
        Returns:
            Error message if invalid, None if valid
        """
        # Check word count
        if len(words) != 4:
            return f"Expected 4 words, got {len(words)}"
        
        # Check for duplicates
        if len(set(words)) != 4:
            return "Duplicate words not allowed"
        
        # Check if words are in puzzle
        puzzle_words = set(word.upper() for word in state.puzzle.words)
        for word in words:
            if word not in puzzle_words:
                return f"Word '{word}' not in puzzle"
        
        # Check if words are from already solved groups
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
    
    def _get_remaining_words(self, state: GameState) -> List[str]:
        """Get words that are still available (not from solved groups)."""
        solved_words = set()
        for group in state.puzzle.groups:
            if group.color in state.solved_groups:
                solved_words.update(word.upper() for word in group.words)
        
        all_words = set(word.upper() for word in state.puzzle.words)
        remaining_words = all_words - solved_words
        return list(remaining_words)
    

