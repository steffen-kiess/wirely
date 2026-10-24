"""Resolved program definitions shared by the compiler and runtime."""

from .container import ContainerError, Section, SectionList
from .procedures import (
    ARITHMETIC_OPCODES, CONVERSION_OPCODES, BranchTarget, Constant, Function, Instruction, Opcode,
    Operand, OperandKind, ProcedureBody,
)
from .program import (
    InputSource, NativeInstance, NativeProcedureInterface, Procedure, Program,
    OutputTarget, ResourceSlot, RunStage, Variable, VariableBank, VariableTable, used_declared_types,
)
from .records import pack_record, record_size, unpack_record
from .validation import validate_program
