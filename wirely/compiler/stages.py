"""Completion states for compiler preparation operations."""

from enum import Enum, auto


class ResolutionState(Enum):
    PENDING = auto()
    RESOLVING = auto()
    COMPLETE = auto()
    FAILED = auto()
