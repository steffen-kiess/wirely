"""Execution stages shared by programs and the compiler."""

import enum


class RunStage(enum.Enum):
    INPUT = enum.auto()
    DEFAULT = enum.auto()
    OUTPUT = enum.auto()
