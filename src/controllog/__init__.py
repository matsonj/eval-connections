"""Controllable logging SDK (events + balanced postings).

Designed to be embedded now and easily extracted into a standalone library.
"""

from .sdk import init, event, post, new_id
from .builders import model_prompt, model_completion, state_move

__all__ = [
    "init",
    "event",
    "post",
    "new_id",
    "model_prompt",
    "model_completion",
    "state_move",
]



