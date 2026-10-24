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
    ADD_WRAPPING = enum.auto()
    ADD_SATURATING = enum.auto()
    ADD_CHECKED = enum.auto()
    SUB_WRAPPING = enum.auto()
    SUB_SATURATING = enum.auto()
    SUB_CHECKED = enum.auto()
    MUL_WRAPPING = enum.auto()
    MUL_SATURATING = enum.auto()
    MUL_CHECKED = enum.auto()
    DIV_CHECKED = enum.auto()
    REM_CHECKED = enum.auto()
    CONVERT_WRAPPING = enum.auto()
    CONVERT_SATURATING = enum.auto()
    CONVERT_CHECKED = enum.auto()
    # CALL destination, function, count, arguments...: function is a
    # Function; count repeats its parameter count so instructions decode
    # without tables.
    CALL = enum.auto()
    JUMP = enum.auto()
    JUMP_IF_TRUE = enum.auto()
    JUMP_IF_FALSE = enum.auto()
    RETURN = enum.auto()
    # FAIL format, count, values...: format is a List[string] Constant.
    FAIL = enum.auto()
    XOR = enum.auto()
    SHIFT_LEFT = enum.auto()
    SHIFT_RIGHT = enum.auto()


# Integer opcodes mapped to TypeInt.arithmetic() operation and overflow mode.
ARITHMETIC_OPCODES = {
    opcode: tuple(opcode.name.lower().split('_'))
    for opcode in Opcode if opcode.name.split('_')[0] in ('ADD', 'SUB', 'MUL', 'DIV', 'REM')
}

# Conversion opcodes mapped to their convert() overflow mode.
CONVERSION_OPCODES = {
    opcode: opcode.name.split('_')[1].lower() for opcode in Opcode if opcode.name.startswith('CONVERT_')
}


@dataclass(frozen=True, slots=True)
class Constant:
    value_type: Type
    value: object


@dataclass(frozen=True, slots=True)
class Function:
    """A native function called by procedure bodies, linked by name at runtime."""

    name: str  # Native name, such as 'Std.Duration.CheckedAdd'.
    parameter_types: tuple[Type, ...]
    return_type: Type

    def __post_init__(self) -> None:
        object.__setattr__(self, 'parameter_types', tuple(self.parameter_types))


class OperandKind(enum.Enum):
    INPUT = enum.auto()
    OUTPUT = enum.auto()
    LOCAL = enum.auto()


@dataclass(frozen=True, slots=True)
class Operand:
    kind: OperandKind
    index: int

    def __str__(self) -> str:
        return f'{self.kind.name.lower()}.{self.index}'


@dataclass(frozen=True, slots=True)
class ConstOperand:
    """A value operand reading a constant."""

    constant: Constant

    def __str__(self) -> str:
        return f'const({self.constant.value_type} {self.constant.value!r})'


ValueOperand = Operand | ConstOperand


@dataclass(frozen=True, slots=True)
class BranchTarget:
    index: int

    def __str__(self) -> str:
        return f'idx{self.index}'


@dataclass(frozen=True, slots=True)
class Instruction:
    opcode: Opcode
    # Value operands, branch targets, integer parameters, and the FAIL format
    # Constant or CALL Function.
    operands: tuple[ValueOperand | BranchTarget | int | Constant | Function, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, 'operands', tuple(self.operands))

    def __str__(self) -> str:
        operands = ', '.join(_operand_text(operand) for operand in self.operands)
        return self.opcode.name + (' ' + operands if operands else '')


def _operand_text(operand: object) -> str:
    if isinstance(operand, Constant):
        return str(ConstOperand(operand))
    if isinstance(operand, Function):
        return operand.name
    return str(operand)


@dataclass(frozen=True, slots=True)
class ProcedureBody:
    instructions: tuple[Instruction, ...]
    local_types: tuple[Type, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, 'instructions', tuple(self.instructions))
        object.__setattr__(self, 'local_types', tuple(self.local_types))
