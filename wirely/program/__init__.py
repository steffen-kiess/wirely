"""Resolved program definitions shared by the compiler and runtime."""

from .procedures import Expression, ProcedureBody
from .program import (
    InputSourceBinding, NativeInstance, Procedure, Program, ResultBinding,
    RunStage, Variable, VariableBlock,
)
