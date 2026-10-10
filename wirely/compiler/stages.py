"""Completion states for compiler stages and dependent resolutions."""

from enum import Enum, auto


class ResolutionState(Enum):
    PENDING = auto()
    RESOLVING = auto()
    COMPLETE = auto()
    FAILED = auto()
