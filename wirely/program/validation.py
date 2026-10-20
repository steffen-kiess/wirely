"""Validation of typed procedure instructions before execution."""

from collections.abc import Iterable, Mapping, Sequence

from ..types import Type, TypeBoolean, TypeEnum, TypeInt, TypeOption, TypeStruct, boolean, uint32
from .procedures import (
    ARITHMETIC_OPCODES, CONVERSION_OPCODES, Function,
    BranchTarget, Constant, Instruction, Opcode, Operand, OperandKind, ProcedureBody,
)
from .program import InputSource, Procedure, Program, ResourceBinding


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
        raise ValueError(f'Expected type {expected}, got {actual}')


def _call_signature(operands: Sequence[Operand | BranchTarget | int]) -> tuple[type, ...]:
    """Return the CALL signature, whose length depends on its count operand."""
    count = operands[2] if len(operands) >= 3 and type(operands[2]) is int and operands[2] >= 0 else 0
    return (Operand, int, int) + (Operand,) * count


def _validate_body(procedure: Procedure, constant_types: Sequence[Type], functions: Sequence[Function]) -> None:
    body = procedure.body
    if not isinstance(body, ProcedureBody):
        raise ValueError('Expected a ProcedureBody')
    if procedure.resources:
        raise ValueError('Bytecode procedures cannot access resources')
    input_types = [source.value_type for _, source in procedure.dependencies]
    output_types = tuple(output_target.variable.var_type for output_target in procedure.outputs.values())
    tables = {
        OperandKind.INPUT: tuple(input_types),
        OperandKind.OUTPUT: output_types,
        OperandKind.LOCAL: body.local_types,
        OperandKind.CONST: constant_types,
    }
    for value_type in (*input_types, *output_types, *body.local_types):
        _runtime_type(value_type)
    instructions = body.instructions
    if not instructions or not isinstance(instructions[-1], Instruction) or instructions[-1].opcode != Opcode.RETURN:
        raise ValueError('Procedure must end in RETURN')

    for index, instruction in enumerate(instructions):
        try:
            if not isinstance(instruction, Instruction) or not isinstance(instruction.opcode, Opcode):
                raise ValueError('Expected an instruction with a known opcode')
            opcode, operands = instruction.opcode, instruction.operands
            signature = _call_signature(operands) if opcode == Opcode.CALL else _SIGNATURES[opcode]
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
                assert isinstance(operands[0], Operand)
                if operands[0].kind not in (OperandKind.LOCAL, OperandKind.OUTPUT):
                    raise ValueError('Destination must be local or output')
            if opcode == Opcode.COPY:
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
                function_index, count = operands[1], operands[2]
                assert isinstance(function_index, int)
                if not 0 <= function_index < len(functions):
                    raise ValueError(f'Function index out of range: {function_index}')
                function = functions[function_index]
                if count != len(function.parameter_types):
                    raise ValueError(f'Function {function.name} expects {len(function.parameter_types)} '
                                     f'arguments, got {count}')
                _same_type(operand_types[0], function.return_type)
                for argument_type, parameter_type in zip(operand_types[1:], function.parameter_types):
                    _same_type(argument_type, parameter_type)
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


def validate_program(program: Program) -> None:
    """Reject invalid constants, bindings, resources, sources, and procedure instructions with ValueError."""
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
    constant_ids = {id(constant) for constant in program.constants}

    def check_names(kind: str, names: Iterable[str], declared: Iterable[str]) -> None:
        if set(names) != set(declared):
            raise ValueError(f'{kind} {sorted(names)!r} do not match the declared {sorted(declared)!r}')

    def check_bindings(values: Mapping[str, Constant], declared: Iterable[str]) -> None:
        check_names('Static bindings', values, declared)
        for name, constant in values.items():
            if not isinstance(constant, Constant) or not isinstance(constant.value_type, Type):
                raise ValueError(f'Static binding {name!r} is not a typed Constant')

    def check_resources(bindings: Mapping[str, ResourceBinding], declared: Mapping[str, str]) -> None:
        check_names('Resource inputs', bindings, declared)
        for name, binding in bindings.items():
            if not isinstance(binding, ResourceBinding):
                raise ValueError(f'Resource input {name!r} is not a ResourceBinding')
            provider = program.instances.get(binding.provider)
            provider_type = (program.native_types.get(provider.type_name)
                             if provider is not None and provider.type_name is not None else None)
            output_type = None if provider_type is None else provider_type.resource_outputs.get(binding.output)
            if output_type is None:
                raise ValueError(f'Resource input {name!r} is bound to unknown output {binding}')
            if output_type != declared[name]:
                raise ValueError(f'Resource input {name!r} of type {declared[name]} is bound to '
                                 f'{binding} of type {output_type}')

    for node_id, instance in program.instances.items():
        try:
            native_type = None if instance.type_name is None else program.native_types.get(instance.type_name)
            if instance.has_native_state and native_type is None:
                raise ValueError(f'Unknown native type {instance.type_name!r}')
            if instance.has_native_state:
                assert native_type is not None
                check_bindings(instance.initializer_values, native_type.initializer_uses)
                check_resources(instance.initializer_bindings, native_type.initializer_resources)
            else:
                check_bindings(instance.initializer_values, ())
                check_resources(instance.initializer_bindings, {})
        except ValueError as error:
            raise ValueError(f'Instance {node_id!r}: {error}') from error

    def check_native_procedure(procedure: Procedure) -> None:
        prefix, _, method = procedure.reference.rpartition(':')
        if not prefix or not method:
            raise ValueError("Expected a native procedure name of the form 'Type:procedure'")
        interface = program.native_procedures.get(procedure.reference)
        if interface is None:
            raise ValueError('Unknown native procedure')
        check_bindings(procedure.values, interface.uses)
        check_resources(procedure.resources, interface.resources)
        check_names('Inputs', [name for name, _ in procedure.dependencies], interface.inputs)
        check_names('Outputs', procedure.outputs, interface.outputs)

    def check_source(input_source: InputSource) -> None:
        if input_source.constant is not None and id(input_source.constant) not in constant_ids:
            raise ValueError('Constant source is not an entry of the program constants')

    for schedule in program.procs.values():
        for procedure in schedule:
            try:
                if procedure.body is None:
                    check_native_procedure(procedure)
                else:
                    check_bindings(procedure.values, ())
                for _, input_source in procedure.dependencies:
                    check_source(input_source)
                if procedure.body is not None:
                    _validate_body(procedure, constant_types, program.functions)
            except ValueError as error:
                raise ValueError(f'Procedure {procedure.reference!r}: {error}') from error
    for name, input_source in program.variables.items():
        try:
            check_source(input_source)
        except ValueError as error:
            raise ValueError(f'Variable {name!r}: {error}') from error
