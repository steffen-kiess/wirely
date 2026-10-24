"""Validation of typed procedure instructions before execution."""

from collections.abc import Sequence

from ..types import Type, TypeBoolean, TypeEnum, TypeInt, TypeList, TypeOption, TypeStruct, boolean, String, uint32
from .procedures import (
    ARITHMETIC_OPCODES, CONVERSION_OPCODES, Function,
    BranchTarget, Constant, ConstOperand, Instruction, Opcode, Operand, OperandKind, ProcedureBody,
)
from .program import InputSource, NativeProcedureEntry, Procedure, Program, RunStage, VariableBank


# Operand stands for a value operand: an Operand or a ConstOperand.
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
    Opcode.XOR: (Operand, Operand, Operand),
    Opcode.SHIFT_LEFT: (Operand, Operand, Operand),
    Opcode.SHIFT_RIGHT: (Operand, Operand, Operand),
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


def _runtime_type(value_type: object) -> None:
    if not isinstance(value_type, Type) or not value_type.is_runtime_type:
        raise ValueError(f'Expected a resolved runtime type, got {value_type!r}')


def _same_type(actual: object, expected: object) -> None:
    if actual != expected:
        raise ValueError(f'Expected type `{expected}`, got `{actual}`')


def _call_signature(operands: Sequence[object]) -> tuple[type, ...]:
    """Return the CALL signature, whose length depends on its count operand."""
    count = operands[2] if len(operands) >= 3 and type(operands[2]) is int and operands[2] >= 0 else 0
    return (Operand, Function, int) + (Operand,) * count


def _matches(value: object, cls: type) -> bool:
    return type(value) in (Operand, ConstOperand) if cls is Operand else type(value) is cls


def _validate_body(procedure: Procedure, stage: RunStage) -> None:
    body = procedure.body
    if not isinstance(body, ProcedureBody):
        raise ValueError('Expected a ProcedureBody')
    if any(source.resource is not None for source in procedure.inputs) or any(
            target.variable.is_resource for target in procedure.outputs):
        raise ValueError('Bytecode procedures cannot access resources')
    input_types = [source.value_type for source in procedure.inputs]
    output_types = tuple(output_target.variable.var_type for output_target in procedure.outputs)
    tables = {
        OperandKind.INPUT: tuple(input_types),
        OperandKind.OUTPUT: output_types,
        OperandKind.LOCAL: body.local_types,
    }
    for value_type in (*input_types, *output_types, *body.local_types):
        _runtime_type(value_type)
    instructions = body.instructions
    if (not instructions or not isinstance(instructions[-1], Instruction)
            or instructions[-1].opcode not in (Opcode.RETURN, Opcode.FAIL)):
        raise ValueError('Procedure must end in RETURN or FAIL')

    for index, instruction in enumerate(instructions):
        try:
            if not isinstance(instruction, Instruction) or not isinstance(instruction.opcode, Opcode):
                raise ValueError('Expected an instruction with a known opcode')
            opcode, operands = instruction.opcode, instruction.operands
            signature: tuple[type, ...]
            if opcode == Opcode.FAIL:
                if (len(operands) < 2 or type(operands[1]) is not int
                        or not 0 <= operands[1] <= 0xffffffff or operands[1] != len(operands) - 2):
                    raise ValueError('Invalid operands for FAIL')
                signature = (Constant, int) + (Operand,) * operands[1]
            else:
                signature = _call_signature(operands) if opcode == Opcode.CALL else _SIGNATURES[opcode]
            if len(operands) != len(signature) or not all(_matches(value, cls) for value, cls in zip(operands, signature)):
                raise ValueError(f'Invalid operands for {opcode.name}')
            operand_types = []
            for operand in operands:
                if isinstance(operand, Operand):
                    if not isinstance(operand.kind, OperandKind):
                        raise ValueError('Unknown operand kind')
                    table = tables[operand.kind]
                    if type(operand.index) is not int or not 0 <= operand.index < len(table):
                        raise ValueError(f'Operand index out of range: {operand}')
                    _runtime_type(table[operand.index])
                    operand_types.append(table[operand.index])
                elif isinstance(operand, ConstOperand):
                    _runtime_type(operand.constant.value_type)
                    operand_types.append(operand.constant.value_type)
                elif isinstance(operand, BranchTarget):
                    if type(operand.index) is not int or not index < operand.index < len(instructions):
                        raise ValueError(f'Branch target must identify a later instruction: {operand}')
            if opcode not in (*_BRANCHES, Opcode.RETURN, Opcode.FAIL):
                if not isinstance(operands[0], Operand) or operands[0].kind not in (OperandKind.LOCAL, OperandKind.OUTPUT):
                    raise ValueError('Destination must be local or output')
            if opcode == Opcode.FAIL:
                if stage != RunStage.LOAD:
                    raise ValueError('FAIL is only allowed in load procedures')
                format_constant, count = operands[:2]
                assert isinstance(format_constant, Constant) and isinstance(count, int)
                _same_type(format_constant.value_type, TypeList(String))
                parts = format_constant.value_type.verify_value(format_constant.value)
                if len(parts) != 2 * count + 1:
                    raise ValueError(f'FAIL format must contain {2 * count + 1} strings')
                if any(parts[index] != '' for index in range(1, len(parts), 2)):
                    raise ValueError('Unsupported diagnostic format specification')
            elif opcode == Opcode.COPY:
                _same_type(operand_types[0], operand_types[1])
            elif opcode in (Opcode.GET_FIELD, Opcode.SET_FIELD):
                struct_type = operand_types[1] if opcode == Opcode.GET_FIELD else operand_types[0]
                if not isinstance(struct_type, TypeStruct) or struct_type.is_opaque:
                    raise ValueError('Field access requires a non-opaque struct')
                field = operands[2]
                assert isinstance(field, int)
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
            elif opcode in (Opcode.NOT, Opcode.AND, Opcode.OR, Opcode.XOR):
                value_type = operand_types[0]
                if not isinstance(value_type, (TypeInt, TypeBoolean)):
                    raise ValueError('Bitwise operation requires an integer or boolean type')
                for source_type in operand_types[1:]:
                    _same_type(source_type, value_type)
            elif opcode in (Opcode.SHIFT_LEFT, Opcode.SHIFT_RIGHT):
                if not isinstance(operand_types[1], TypeInt):
                    raise ValueError('Shift requires an integer type')
                _same_type(operand_types[0], operand_types[1])
                _same_type(operand_types[2], uint32)
            elif opcode in (Opcode.JUMP_IF_TRUE, Opcode.JUMP_IF_FALSE):
                for value_type in operand_types:
                    _same_type(value_type, boolean)
            elif opcode in (Opcode.EQUAL, Opcode.LESS):
                _same_type(operand_types[0], boolean)
                _same_type(operand_types[1], operand_types[2])
                if opcode == Opcode.LESS and not isinstance(operand_types[1], TypeInt):
                    raise ValueError('Ordering requires an integer type')
            elif opcode == Opcode.CALL:
                function, count = operands[1], operands[2]
                assert isinstance(function, Function)
                if count != len(function.parameter_types):
                    raise ValueError(f'Function `{function.name}` expects {len(function.parameter_types)} '
                                     f'arguments, got {count}')
                _same_type(operand_types[0], function.return_type)
                for argument_type, parameter_type in zip(operand_types[1:], function.parameter_types):
                    _same_type(argument_type, parameter_type)
            elif opcode in CONVERSION_OPCODES:
                if not isinstance(operand_types[1], (TypeInt, TypeEnum, TypeBoolean)):
                    raise ValueError(f'Conversion source must be an integer, enum, or boolean, got `{operand_types[1]}`')
                target_type = operand_types[0]
                if CONVERSION_OPCODES[opcode] == 'checked':
                    if not isinstance(target_type, TypeOption):
                        raise ValueError(f'Checked conversion requires an Option destination, got `{target_type}`')
                    target_type = target_type.inner_type
                if not isinstance(target_type, (TypeInt, TypeBoolean)):
                    raise ValueError(f'Conversion destination must be an integer or boolean, got `{target_type}`')
            elif opcode in ARITHMETIC_OPCODES:
                integer_type = operand_types[1]
                if not isinstance(integer_type, TypeInt):
                    raise ValueError(f'Arithmetic requires an integer type, got `{integer_type}`')
                _same_type(operand_types[2], integer_type)
                _same_type(operand_types[0], TypeOption(integer_type)
                           if ARITHMETIC_OPCODES[opcode][1] == 'checked' else integer_type)
        except ValueError as error:
            raise ValueError(f'Instruction idx{index}: {error}') from error


def validate_program(program: Program) -> None:
    """Reject invalid constants, bindings, resources, sources, and procedure instructions with ValueError."""
    if not isinstance(program, Program):
        raise ValueError('Expected a Program')
    for index, constant in enumerate(program.constants):
        try:
            if not isinstance(constant, Constant):
                raise ValueError('Expected a Constant')
            if not isinstance(constant.value_type, Type):
                raise ValueError(f'Expected a type, got {constant.value_type!r}')
            constant.value_type.verify_value(constant.value)
            if constant.value_type.is_runtime_type:
                constant.value_type.serialize(constant.value)
        except Exception as error:
            raise ValueError(f'Constant const.{index}: {error}') from error
    for index, function in enumerate(program.functions):
        try:
            if not isinstance(function, Function):
                raise ValueError('Expected a Function')
            if type(function.name) is not str or not function.name:
                raise ValueError('Expected a function name')
            for value_type in (*function.parameter_types, function.return_type):
                _runtime_type(value_type)
        except ValueError as error:
            raise ValueError(f'Function func.{index}: {error}') from error

    for table in (program.state_variables, program.temporaries):
        for variable in table.variables.values():
            if variable.is_resource:
                raise ValueError(f'Variable `{variable}`: only fixed values can hold resources')
    fixed = program.fixed_values.variable_set
    bank_tables = {
        VariableBank.FIXED_VALUES: fixed,
        VariableBank.TEMPORARIES: program.temporaries.variable_set,
        VariableBank.OLD_STATE: program.state_variables.variable_set,
        VariableBank.NEW_STATE: program.state_variables.variable_set,
    }
    assigned: set = set()

    def check_native_procedure(procedure: Procedure) -> None:
        native = procedure.native
        if not isinstance(native, NativeProcedureEntry):
            raise ValueError('Expected a native procedure entry')
        prefix, _, method = native.name.rpartition(':')
        if not prefix or not method:
            raise ValueError('Expected a native procedure name of the form `Type:procedure`')
        for kind, names, bindings in [('inputs', native.input_names, procedure.inputs),
                                      ('outputs', native.output_names, procedure.outputs)]:
            if len(set(names)) != len(names):
                raise ValueError(f'Native procedure {kind} must have unique names')
            if len(bindings) != len(names):
                raise ValueError(f'Expected {len(names)} {kind}, got {len(bindings)}')

    def check_source(source: InputSource) -> None:
        if not isinstance(source, InputSource):
            raise ValueError('Expected an InputSource')
        if source.variable is not None and source.variable not in bank_tables[source.bank]:
            raise ValueError('Source variable is not in its declared bank')

    for stage in RunStage:
        for procedure in program.procs.get(stage, ()):
            try:
                if (procedure.native is None) == (procedure.body is None):
                    raise ValueError('Expected either a native procedure or a body')
                if procedure.initializer and stage != RunStage.LOAD:
                    raise ValueError('Initializers must be load procedures')
                if procedure.native is not None:
                    check_native_procedure(procedure)
                for source in procedure.inputs:
                    check_source(source)
                    if stage == RunStage.LOAD and source.bank not in (VariableBank.CONST_VALUES,
                                                                      VariableBank.FIXED_VALUES):
                        raise ValueError('Load procedures can only read constants and fixed values')
                    if source.bank == VariableBank.FIXED_VALUES and source.variable not in assigned:
                        raise ValueError('Fixed value is read before assignment')
                for output in procedure.outputs:
                    target = output.variable
                    is_fixed = output.bank == VariableBank.FIXED_VALUES
                    if is_fixed != (stage == RunStage.LOAD):
                        raise ValueError('Load procedures write only fixed values; runtime procedures cannot write them')
                    if is_fixed:
                        if target not in fixed:
                            raise ValueError('Unknown output variable')
                        if target in assigned:
                            raise ValueError('Fixed value is assigned more than once')
                        assigned.add(target)
                    elif target not in bank_tables[output.bank]:
                        raise ValueError('Unknown output variable')
                if procedure.body is not None:
                    _validate_body(procedure, stage)
            except ValueError as error:
                raise ValueError(f'Procedure `{procedure.name}`: {error}') from error
    if assigned != fixed:
        raise ValueError('Every fixed value must be assigned once')
    for name, input_source in program.symbols.variable_aliases.items():
        try:
            check_source(input_source)
            if input_source.constant is not None:
                _runtime_type(input_source.constant.value_type)
        except ValueError as error:
            raise ValueError(f'Variable `{name}`: {error}') from error
