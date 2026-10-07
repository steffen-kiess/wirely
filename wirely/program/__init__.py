"""Resolved program definitions shared by the compiler and runtime."""

from .procedures import (
    ARITHMETIC_OPCODES, CONVERSION_OPCODES, BranchTarget, Constant, Function, Instruction, Opcode,
    Operand, OperandKind, ProcedureBody,
)
from .program import (
    InputSourceBinding, NativeInstance, Procedure, Program, ResultBinding,
    RunStage, Variable, VariableBlock,
)
from .validation import validate_program
