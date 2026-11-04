"""Agent Board: a local project board for AI agents and their human owner."""

from .core import Board, BoardError, find_default_db

__all__ = ["Board", "BoardError", "find_default_db"]
__version__ = "0.1.0"
