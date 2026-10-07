"""Typed instructions for loop-free Wirely procedure bodies."""

import enum
from dataclasses import dataclass

from ..types import Type


class Opcode(enum.Enum):
    COPY = enum.auto()
    SET_DEFAULT = enum.auto()
    GET_FIELD = enum.auto()
    SET_FIELD = enum.auto()
    MAKE_SOME = enum.auto()
    GET_OPTION_VALUE = enum.auto()
    IS_SOME = enum.auto()
    NOT = enum.auto()
    AND = enum.auto()
    OR = enum.auto()
    EQUAL = enum.auto()
    LESS = enum.auto()
    JUMP = enum.auto()
    JUMP_IF_TRUE = enum.auto()
    JUMP_IF_FALSE = enum.auto()
    RETURN = enum.auto()


class OperandKind(enum.Enum):
    INPUT = enum.auto()
    RESULT = enum.auto()
    LOCAL = enum.auto()
    CONST = enum.auto()


@dataclass(frozen=True, slots=True)
class Operand:
    kind: OperandKind
    index: int

    def __str__(self):
        return f'{self.kind.name.lower()}.{self.index}'


@dataclass(frozen=True, slots=True)
class BranchTarget:
    index: int

    def __str__(self):
        return f'idx{self.index}'


@dataclass(frozen=True, slots=True)
class Instruction:
    opcode: Opcode
    operands: tuple[Operand | BranchTarget | int, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'operands', tuple(self.operands))

    def __str__(self):
        operands = ', '.join(str(operand) for operand in self.operands)
        return self.opcode.name + (' ' + operands if operands else '')


@dataclass(frozen=True, slots=True)
class ProcedureBody:
    instructions: tuple[Instruction, ...]
    local_types: tuple[Type, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'instructions', tuple(self.instructions))
        object.__setattr__(self, 'local_types', tuple(self.local_types))


@dataclass(frozen=True, slots=True)
class Constant:
    value_type: Type
    value: object
