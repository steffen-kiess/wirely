"""Execution of validated, forward-only procedure instructions."""

from ..program import ARITHMETIC_OPCODES, Opcode, OperandKind
from ..types import Some
from ..types.value_types import _from_native, _to_native


def execute_procedure(program, procedure, inputs):
    body = procedure.body
    input_types = tuple(bindings[0].variable.var_type for _, bindings, _ in procedure.dependencies)
    result_types = tuple(binding.variable.var_type for binding in procedure.results.values())
    values = {
        OperandKind.INPUT: [_from_native(value_type, inputs[name])
                            for (name, _, _), value_type in zip(procedure.dependencies, input_types)],
        OperandKind.RESULT: [value_type.default_value() for value_type in result_types],
        OperandKind.LOCAL: [value_type.default_value() for value_type in body.local_types],
    }
    types = {
        OperandKind.INPUT: input_types,
        OperandKind.RESULT: result_types,
        OperandKind.LOCAL: body.local_types,
    }

    def read(operand):
        if operand.kind == OperandKind.CONST:
            return program.constants[operand.index].value
        return values[operand.kind][operand.index]

    def value_type(operand):
        if operand.kind == OperandKind.CONST:
            return program.constants[operand.index].value_type
        return types[operand.kind][operand.index]

    index = 0
    while True:
        instruction = body.instructions[index]
        opcode, operands = instruction.opcode, instruction.operands
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
                source = operands[1]
                field = tuple(value_type(source).fields)[operands[2]]
                result = read(source)[field]
            elif opcode == Opcode.SET_FIELD:
                struct_type = value_type(destination)
                fields = dict(read(destination))
                fields[tuple(struct_type.fields)[operands[2]]] = read(operands[1])
                result = struct_type.make_value(fields)
            elif opcode == Opcode.MAKE_SOME:
                result = Some(read(operands[1]))
            elif opcode == Opcode.GET_OPTION_VALUE:
                option = read(operands[1])
                result = value_type(destination).default_value() if option is None else option.value
            elif opcode == Opcode.IS_SOME:
                result = read(operands[1]) is not None
            elif opcode == Opcode.NOT:
                result = not read(operands[1])
            elif opcode == Opcode.AND:
                left, right = read(operands[1]), read(operands[2])
                result = left and right
            elif opcode == Opcode.OR:
                left, right = read(operands[1]), read(operands[2])
                result = left or right
            elif opcode == Opcode.EQUAL:
                left, right = read(operands[1]), read(operands[2])
                result = value_type(operands[1]).equals(left, right)
            elif opcode in ARITHMETIC_OPCODES:
                left, right = read(operands[1]), read(operands[2])
                result = value_type(operands[1]).arithmetic(*ARITHMETIC_OPCODES[opcode], left, right)
            elif opcode == Opcode.LESS:
                left, right = read(operands[1]), read(operands[2])
                result = value_type(operands[1]).less(left, right)
            values[destination.kind][destination.index] = result
        index += 1
    return {name: _to_native(value_type, value)
            for name, value_type, value in zip(procedure.results, result_types, values[OperandKind.RESULT])}
