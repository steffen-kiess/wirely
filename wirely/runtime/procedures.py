"""Execution of validated, forward-only procedure instructions."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..program import ARITHMETIC_OPCODES, CONVERSION_OPCODES, ConstOperand, Opcode, OperandKind, Procedure
from ..program.types import InternalValue, convert
from ..types.option import Some
from ..types.value_types import DynamicValue
from .extensions import NativeTypes

NativeFunction = Callable[[Sequence[InternalValue]], InternalValue]


class LoadFailure(Exception):
    """An intentional failure while executing a candidate program's load stage."""

    def __init__(self, procedure: Procedure, instruction_index: int, message: str,
                 values: Sequence[DynamicValue], *, procedure_index: int | None = None) -> None:
        self.procedure_instance = procedure
        self.procedure_index = procedure_index
        self.procedure = procedure.name
        self.node = procedure.node
        self.instruction_index = instruction_index
        self.message = message
        self.values = tuple(values)
        label = procedure.name if procedure.name is not None else (
            f'procedure[{procedure_index}]' if procedure_index is not None else '<unnamed procedure>')
        super().__init__(f'{label} at idx{instruction_index}: {message}')


def execute_procedure(procedure: Procedure, inputs: Sequence[InternalValue],
                      functions: Mapping[str, NativeFunction] = {}, *, native_types: NativeTypes,
                      procedure_index: int | None = None) -> list[InternalValue]:
    """Run a bytecode procedure on internal values.

    functions maps the names of called functions to their linked implementations.
    native_types formats the values of failure messages.
    Returns the output values in operand order."""
    body = procedure.body
    assert body is not None
    input_types = tuple(source.value_type for source in procedure.inputs)
    output_types = tuple(output_target.variable.var_type for output_target in procedure.outputs)
    values = {
        OperandKind.INPUT: list(inputs),
        OperandKind.OUTPUT: [value_type.default_value() for value_type in output_types],
        OperandKind.LOCAL: [value_type.default_value() for value_type in body.local_types],
    }
    types = {
        OperandKind.INPUT: input_types,
        OperandKind.OUTPUT: output_types,
        OperandKind.LOCAL: body.local_types,
    }

    def read(operand: Any) -> Any:
        if isinstance(operand, ConstOperand):
            return operand.constant.value
        return values[operand.kind][operand.index]

    def value_type(operand: Any) -> Any:
        if isinstance(operand, ConstOperand):
            return operand.constant.value_type
        return types[operand.kind][operand.index]

    index = 0
    while True:
        instruction = body.instructions[index]
        opcode = instruction.opcode
        operands: Any = instruction.operands  # Their kinds are checked by validate_program().
        if opcode == Opcode.RETURN:
            break
        if opcode == Opcode.FAIL:
            parts = operands[0].value
            diagnostic_values = tuple(DynamicValue._make(value_type(operand), read(operand))
                                      for operand in operands[2:])
            message = parts[0] + ''.join(value.to_text(native_types=native_types) + parts[2 * index + 2]
                                         for index, value in enumerate(diagnostic_values))
            raise LoadFailure(procedure, index, message, diagnostic_values, procedure_index=procedure_index)
        if opcode == Opcode.JUMP:
            index = operands[0].index
            continue
        if opcode in (Opcode.JUMP_IF_TRUE, Opcode.JUMP_IF_FALSE):
            condition = read(operands[0])
            if condition == (opcode == Opcode.JUMP_IF_TRUE):
                index = operands[1].index
                continue
        else:
            destination = operands[0]
            if opcode == Opcode.COPY:
                result = read(operands[1])
            elif opcode == Opcode.SET_DEFAULT:
                result = value_type(destination).default_value()
            elif opcode == Opcode.GET_FIELD:
                result = read(operands[1])[operands[2]]
            elif opcode == Opcode.SET_FIELD:
                fields = list(read(destination))
                fields[operands[2]] = read(operands[1])
                result = tuple(fields)
            elif opcode == Opcode.MAKE_SOME:
                result = Some(read(operands[1]))
            elif opcode == Opcode.GET_OPTION_VALUE:
                option = read(operands[1])
                result = value_type(destination).default_value() if option is None else option.value
            elif opcode == Opcode.IS_SOME:
                result = read(operands[1]) is not None
            elif opcode == Opcode.NOT:
                result = value_type(destination).bitwise('not', read(operands[1]))
            elif opcode in (Opcode.AND, Opcode.OR, Opcode.XOR):
                left, right = read(operands[1]), read(operands[2])
                result = value_type(destination).bitwise(opcode.name.lower(), left, right)
            elif opcode in (Opcode.SHIFT_LEFT, Opcode.SHIFT_RIGHT):
                value, count = read(operands[1]), read(operands[2])
                direction = 'left' if opcode == Opcode.SHIFT_LEFT else 'right'
                result = value_type(destination).shift(direction, value, count)
            elif opcode == Opcode.EQUAL:
                left, right = read(operands[1]), read(operands[2])
                result = value_type(operands[1]).equals(left, right)
            elif opcode == Opcode.CALL:
                # Implementations must not fail; an exception is a library error.
                result = functions[operands[1].name]([read(argument) for argument in operands[3:]])
            elif opcode in CONVERSION_OPCODES:
                target_type = value_type(destination)
                if CONVERSION_OPCODES[opcode] == 'checked':
                    target_type = target_type.inner_type
                result = convert(value_type(operands[1]), target_type, CONVERSION_OPCODES[opcode],
                                 read(operands[1]))
            elif opcode in ARITHMETIC_OPCODES:
                left, right = read(operands[1]), read(operands[2])
                result = value_type(operands[1]).arithmetic(*ARITHMETIC_OPCODES[opcode], left, right)
            elif opcode == Opcode.LESS:
                left, right = read(operands[1]), read(operands[2])
                result = value_type(operands[1]).less(left, right)
            values[destination.kind][destination.index] = result
        index += 1
    return values[OperandKind.OUTPUT]
