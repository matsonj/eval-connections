"""Logging utilities with JSON formatting."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs JSON lines."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
        }
        
        # Add any extra fields from the record
        if hasattr(record, 'extra_data'):
            log_data.update(record.extra_data)
            
        return json.dumps(log_data, ensure_ascii=False)


def setup_logger(log_path: Path, run_id: str, verbose: bool = False) -> logging.Logger:
    """
    Set up logger with JSON formatting.
    
    Args:
        log_path: Directory to write logs to
        run_id: Unique run identifier
        verbose: Whether to also log to console
        
    Returns:
        Configured logger
    """
    log_path.mkdir(exist_ok=True)
    
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    log_file = log_path / f"connections_eval_{timestamp}.jsonl"
    
    logger = logging.getLogger("connections_eval")
    logger.setLevel(logging.DEBUG)
    
    # Remove any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # File handler with JSON formatting
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(JSONFormatter())
    logger.addHandler(file_handler)
    
    # Console handler if verbose
    if verbose:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(JSONFormatter())
        logger.addHandler(console_handler)
    
    return logger


def log_exchange(logger: logging.Logger, data: Dict[str, Any]) -> None:
    """Log an exchange with structured data."""
    record = logging.LogRecord(
        name="connections_eval.exchange",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Exchange logged",
        args=(),
        exc_info=None
    )
    record.extra_data = data
    logger.handle(record)


def log_summary(logger: logging.Logger, data: Dict[str, Any]) -> None:
    """Log a run summary with structured data."""
    record = logging.LogRecord(
        name="connections_eval.summary",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Run summary",
        args=(),
        exc_info=None
    )
    record.extra_data = data
    logger.handle(record)
