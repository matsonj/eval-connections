"""Timing utilities for measuring performance."""

import time
from typing import Optional


class Timer:
    """Context manager for timing operations."""
    
    def __init__(self):
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
    
    def __enter__(self) -> 'Timer':
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.time()
    
    @property
    def elapsed_seconds(self) -> float:
        """Get elapsed time in seconds."""
        if self.start_time is None:
            raise ValueError("Timer not started")
        if self.end_time is None:
            raise ValueError("Timer not finished")
        return self.end_time - self.start_time
    
    @property
    def elapsed_ms(self) -> int:
        """Get elapsed time in milliseconds."""
        return int(self.elapsed_seconds * 1000)
