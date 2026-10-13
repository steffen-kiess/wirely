"""Resolved program definitions shared by the compiler and runtime."""

from .container import ContainerError, Section, SectionList
from .procedures import (
    ARITHMETIC_OPCODES, CONVERSION_OPCODES, BranchTarget, Constant, Function, Instruction, Opcode,
    Operand, OperandKind, ProcedureBody,
)
from .program import (
    InputSource, NativeInstance, Procedure, Program, OutputTarget,
    ResourceBinding, RunStage, Variable, VariableTable,
)
from .validation import validate_program
