"""Validation of typed procedure instructions before execution."""

from ..types import Type, TypeBoolean, TypeEnum, TypeInt, TypeOption, TypeStruct, boolean
from .procedures import (
    ARITHMETIC_OPCODES, CONVERSION_OPCODES,
    BranchTarget, Constant, Instruction, Opcode, Operand, OperandKind, ProcedureBody,
)
from .program import Program


_SIGNATURES = {
    Opcode.COPY: (Operand, Operand),
    Opcode.SET_DEFAULT: (Operand,),
    Opcode.GET_FIELD: (Operand, Operand, int),
    Opcode.SET_FIELD: (Operand, Operand, int),
    Opcode.MAKE_SOME: (Operand, Operand),
    Opcode.GET_OPTION_VALUE: (Operand, Operand),
    Opcode.IS_SOME: (Operand, Operand),
    Opcode.NOT: (Operand, Operand),
    Opcode.AND: (Operand, Operand, Operand),
    Opcode.OR: (Operand, Operand, Operand),
    Opcode.EQUAL: (Operand, Operand, Operand),
    Opcode.LESS: (Operand, Operand, Operand),
    **{opcode: (Operand, Operand, Operand) for opcode in ARITHMETIC_OPCODES},
    **{opcode: (Operand, Operand) for opcode in CONVERSION_OPCODES},
    Opcode.JUMP: (BranchTarget,),
    Opcode.JUMP_IF_TRUE: (Operand, BranchTarget),
    Opcode.JUMP_IF_FALSE: (Operand, BranchTarget),
    Opcode.RETURN: (),
}
_BRANCHES = (Opcode.JUMP, Opcode.JUMP_IF_TRUE, Opcode.JUMP_IF_FALSE)


def _runtime_type(value_type):
    if not isinstance(value_type, Type) or not value_type.is_runtime_type:
        raise ValueError(f'Expected a resolved runtime type, got {value_type!r}')


def _same_type(actual, expected):
    if actual != expected:
        raise ValueError(f'Expected type {expected}, got {actual}')


def _validate_body(procedure, constant_types):
    body = procedure.body
    if not isinstance(body, ProcedureBody):
        raise ValueError('Expected a ProcedureBody')
    if procedure.resources:
        raise ValueError('Bytecode procedures cannot access resources')
    input_types = []
    for name, bindings, is_list in procedure.dependencies:
        if is_list or len(bindings) != 1:
            raise ValueError(f'Bytecode input {name!r} must have exactly one source')
        input_types.append(bindings[0].variable.var_type)
    result_types = tuple(binding.variable.var_type for binding in procedure.results.values())
    tables = {
        OperandKind.INPUT: tuple(input_types),
        OperandKind.RESULT: result_types,
        OperandKind.LOCAL: body.local_types,
        OperandKind.CONST: constant_types,
    }
    for value_type in (*input_types, *result_types, *body.local_types):
        _runtime_type(value_type)
    instructions = body.instructions
    if not instructions or not isinstance(instructions[-1], Instruction) or instructions[-1].opcode != Opcode.RETURN:
        raise ValueError('Procedure must end in RETURN')

    for index, instruction in enumerate(instructions):
        try:
            if not isinstance(instruction, Instruction) or not isinstance(instruction.opcode, Opcode):
                raise ValueError('Expected an instruction with a known opcode')
            opcode, operands = instruction.opcode, instruction.operands
            signature = _SIGNATURES[opcode]
            if len(operands) != len(signature) or any(type(value) is not cls for value, cls in zip(operands, signature)):
                raise ValueError(f'Invalid operands for {opcode.name}')
            operand_types = []
            for operand in operands:
                if isinstance(operand, Operand):
                    if not isinstance(operand.kind, OperandKind):
                        raise ValueError('Unknown operand kind')
                    table = tables[operand.kind]
                    if type(operand.index) is not int or not 0 <= operand.index < len(table):
                        raise ValueError(f'Operand index out of range: {operand}')
                    operand_types.append(table[operand.index])
                elif isinstance(operand, BranchTarget):
                    if type(operand.index) is not int or not index < operand.index < len(instructions):
                        raise ValueError(f'Branch target must identify a later instruction: {operand}')
            if opcode not in (*_BRANCHES, Opcode.RETURN):
                if operands[0].kind not in (OperandKind.LOCAL, OperandKind.RESULT):
                    raise ValueError('Destination must be local or result')
            if opcode == Opcode.COPY:
                _same_type(operand_types[0], operand_types[1])
            elif opcode in (Opcode.GET_FIELD, Opcode.SET_FIELD):
                struct_type = operand_types[1] if opcode == Opcode.GET_FIELD else operand_types[0]
                if not isinstance(struct_type, TypeStruct):
                    raise ValueError('Field access requires a struct')
                field = operands[2]
                if not 0 <= field < len(struct_type.fields):
                    raise ValueError('Field index out of range')
                field_type = tuple(struct_type.fields.values())[field]
                _same_type(operand_types[0] if opcode == Opcode.GET_FIELD else operand_types[1], field_type)
            elif opcode in (Opcode.MAKE_SOME, Opcode.GET_OPTION_VALUE, Opcode.IS_SOME):
                option_type = operand_types[0] if opcode == Opcode.MAKE_SOME else operand_types[1]
                if not isinstance(option_type, TypeOption):
                    raise ValueError('Option operation requires an Option type')
                if opcode == Opcode.MAKE_SOME:
                    _same_type(operand_types[1], option_type.inner_type)
                else:
                    _same_type(operand_types[0], boolean if opcode == Opcode.IS_SOME else option_type.inner_type)
            elif opcode in (Opcode.NOT, Opcode.AND, Opcode.OR, Opcode.JUMP_IF_TRUE, Opcode.JUMP_IF_FALSE):
                for value_type in operand_types:
                    _same_type(value_type, boolean)
            elif opcode in (Opcode.EQUAL, Opcode.LESS):
                _same_type(operand_types[0], boolean)
                _same_type(operand_types[1], operand_types[2])
                if opcode == Opcode.LESS and not operand_types[1].is_ordered:
                    raise ValueError(f'Type {operand_types[1]} is not ordered')
            elif opcode in CONVERSION_OPCODES:
                if not isinstance(operand_types[1], (TypeInt, TypeEnum, TypeBoolean)):
                    raise ValueError(f'Conversion source must be an integer, enum, or boolean, got {operand_types[1]}')
                target_type = operand_types[0]
                if CONVERSION_OPCODES[opcode] == 'checked':
                    if not isinstance(target_type, TypeOption):
                        raise ValueError(f'Checked conversion requires an Option destination, got {target_type}')
                    target_type = target_type.inner_type
                if not isinstance(target_type, (TypeInt, TypeBoolean)):
                    raise ValueError(f'Conversion destination must be an integer or boolean, got {target_type}')
            elif opcode in ARITHMETIC_OPCODES:
                integer_type = operand_types[1]
                if not isinstance(integer_type, TypeInt):
                    raise ValueError(f'Arithmetic requires an integer type, got {integer_type}')
                _same_type(operand_types[2], integer_type)
                _same_type(operand_types[0], TypeOption(integer_type)
                           if ARITHMETIC_OPCODES[opcode][1] == 'checked' else integer_type)
        except ValueError as error:
            raise ValueError(f'Instruction idx{index}: {error}') from error


def validate_program(program):
    """Reject invalid constants and procedure instructions with ValueError."""
    if not isinstance(program, Program):
        raise ValueError('Expected a Program')
    for index, constant in enumerate(program.constants):
        try:
            if not isinstance(constant, Constant):
                raise ValueError('Expected a Constant')
            _runtime_type(constant.value_type)
            constant.value_type.serialize(constant.value)
        except Exception as error:
            raise ValueError(f'Constant const.{index}: {error}') from error
    constant_types = tuple(constant.value_type for constant in program.constants)
    for schedule in program.procs.values():
        for procedure in schedule:
            if procedure.body is not None:
                try:
                    _validate_body(procedure, constant_types)
                except ValueError as error:
                    raise ValueError(f'Procedure {procedure.reference[1]!r}: {error}') from error
