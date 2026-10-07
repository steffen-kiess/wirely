"""Resolved program definitions shared by the compiler and runtime."""

from .procedures import (
    ARITHMETIC_OPCODES, BranchTarget, Constant, Instruction, Opcode, Operand, OperandKind,
    ProcedureBody,
)
from .program import (
    InputSourceBinding, NativeInstance, Procedure, Program, ResultBinding,
    RunStage, Variable, VariableBlock,
)
from .validation import validate_program
