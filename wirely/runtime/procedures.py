"""Execution of validated, forward-only procedure instructions."""

from collections.abc import Callable, Sequence
from typing import Any

from ..program import ARITHMETIC_OPCODES, CONVERSION_OPCODES, Opcode, OperandKind, Procedure, Program
from ..types import InternalValue, Some, convert

NativeFunction = Callable[[Sequence[InternalValue]], InternalValue]


def execute_procedure(program: Program, procedure: Procedure, inputs: Sequence[InternalValue],
                      functions: Sequence[NativeFunction] = ()) -> dict[str, InternalValue]:
    """Run a bytecode procedure on internal values.

    functions call the linked implementations of program.functions."""
    body = procedure.body
    assert body is not None
    input_types = tuple(source.value_type for _, source in procedure.dependencies)
    output_types = tuple(output_target.variable.var_type for output_target in procedure.outputs.values())
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
        if operand.kind == OperandKind.CONST:
            return program.constants[operand.index].value
        return values[operand.kind][operand.index]

    def value_type(operand: Any) -> Any:
        if operand.kind == OperandKind.CONST:
            return program.constants[operand.index].value_type
        return types[operand.kind][operand.index]

    index = 0
    while True:
        instruction = body.instructions[index]
        opcode = instruction.opcode
        operands: Any = instruction.operands  # Their kinds are checked by validate_program().
        if opcode == Opcode.RETURN:
            break
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
                result = functions[operands[1]]([read(argument) for argument in operands[3:]])
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
    return dict(zip(procedure.outputs, values[OperandKind.OUTPUT]))
