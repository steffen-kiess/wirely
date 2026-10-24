"""Round-trip test programs, restoring debug names only after checking bytes."""

from contextvars import ContextVar
from dataclasses import replace
from types import MappingProxyType
from typing import Any

from wirely import types
from wirely.program import Constant, ConstOperand, Function, InputSource, Program, ProgramSymbols, RunStage, VariableTable
from wirely.program.loader import load_program
from wirely.program.writer import write_program
from wirely.runtime.extensions import NativeTypes

bytecode_roundtrip_mode: ContextVar[bool] = ContextVar('bytecode_roundtrip_mode', default=False)


def roundtrip_program(original: Program, native_types: NativeTypes) -> Program:
    """Use decoded runtime data, with original debug metadata for test lookup."""
    data = write_program(original)
    loaded = load_program(data, native_types=native_types)
    assert write_program(loaded) == data, 'Bytecode write/load/write changed the bytes'
    type_map: dict[int, types.Type] = {}
    constants: dict[int, Constant] = {}

    def type_pair(old: types.Type, new: types.Type) -> None:
        if id(old) in type_map:
            return
        type_map[id(old)] = new
        if isinstance(old, types.TypeStruct) and isinstance(new, types.TypeStruct):
            for old_field, new_field in zip(old.field_types, new.field_types):
                type_pair(old_field, new_field)
            new.name, new.type_name, new.field_names = old.name, old.type_name, old.field_names
            new._fields = None if old.field_names is None else MappingProxyType(dict(zip(old.field_names, new.field_types)))
        elif isinstance(old, types.TypeEnum) and isinstance(new, types.TypeEnum):
            type_pair(old.underlying_type, new.underlying_type)
            new.name, new.type_name, new.members, new._names = old.name, old.type_name, old.members, old._names
        elif isinstance(old, (types.TypeList, types.TypeOption)) and isinstance(new, (types.TypeList, types.TypeOption)):
            type_pair(old.inner_type, new.inner_type)
        elif isinstance(old, types.TypeResource) and isinstance(new, types.TypeResource):
            new.name = old.name

    def value_pair(value_type: types.Type, old: Any, new: Any) -> None:
        if isinstance(value_type, types.TypeValueType):
            type_pair(old, new)
        elif isinstance(value_type, types.TypeStruct):
            for field_type, old_field, new_field in zip(value_type.field_types, old, new):
                value_pair(field_type, old_field, new_field)
        elif isinstance(value_type, types.TypeList):
            for old_item, new_item in zip(old, new):
                value_pair(value_type.inner_type, old_item, new_item)
        elif isinstance(value_type, types.TypeOption) and old is not None:
            value_pair(value_type.inner_type, old.value, new.value)

    def constant_pair(old: Constant, new: Constant) -> None:
        constants[id(old)] = new
        type_pair(old.value_type, new.value_type)
        value_pair(old.value_type, old.value, new.value)

    variables = {}
    tables = []
    for old_table, new_table in ((original.state_variables, loaded.state_variables),
                                  (original.temporaries, loaded.temporaries), (original.fixed_values, loaded.fixed_values)):
        renamed = []
        for old, new in zip(old_table.offsets, new_table.offsets):
            type_pair(old.var_type, new.var_type)
            variable = replace(new, name=old.name)
            variables[old] = variable
            variables[new] = variable
            renamed.append(variable)
        tables.append(VariableTable(renamed, old_table.name))
    schedules = {}
    for stage in RunStage:
        schedule = []
        for old_proc, new_proc in zip(original.procs.get(stage, ()), loaded.procs.get(stage, ())):
            for old_source, new_source in zip(old_proc.inputs, new_proc.inputs):
                if old_source.constant is not None:
                    assert new_source.constant is not None
                    constant_pair(old_source.constant, new_source.constant)
            if old_proc.body is not None and new_proc.body is not None:
                for old_type, new_type in zip(old_proc.body.local_types, new_proc.body.local_types):
                    type_pair(old_type, new_type)
                for old_instruction, new_instruction in zip(old_proc.body.instructions, new_proc.body.instructions):
                    for old_operand, new_operand in zip(old_instruction.operands, new_instruction.operands):
                        if isinstance(old_operand, ConstOperand) and isinstance(new_operand, ConstOperand):
                            constant_pair(old_operand.constant, new_operand.constant)
                        elif isinstance(old_operand, Constant) and isinstance(new_operand, Constant):
                            constant_pair(old_operand, new_operand)
                        # Function signatures can contain declared types not used by variables.
                        elif isinstance(old_operand, Function) and isinstance(new_operand, Function):
                            for old_type, new_type in zip(old_operand.parameter_types, new_operand.parameter_types):
                                type_pair(old_type, new_type)
                            type_pair(old_operand.return_type, new_operand.return_type)
            inputs = tuple(replace(source, variable=variables[source.variable]) if source.variable is not None else source
                           for source in new_proc.inputs)
            outputs = tuple(replace(target, variable=variables[target.variable]) for target in new_proc.outputs)
            schedule.append(replace(new_proc, name=old_proc.name, inputs=inputs, outputs=outputs))
        schedules[stage] = tuple(schedule)
    aliases = {}
    for name, source in original.symbols.variable_aliases.items():
        if source.variable is not None:
            aliases[name] = replace(source, variable=variables[source.variable])
        else:
            constant = source.constant
            assert constant is not None
            # Alias-only constants have no file entry; they are debug data.
            mapped = constants.get(id(constant), Constant(type_map.get(id(constant.value_type), constant.value_type),
                                                          constant.value))
            aliases[name] = InputSource(source.bank, constant=mapped)
    result = replace(loaded, state_variables=tables[0], temporaries=tables[1], fixed_values=tables[2],
                     initial_state={variables[var]: value for var, value in loaded.initial_state.items()},
                     procs=schedules, graph=original.graph,
                     symbols=ProgramSymbols(name=original.symbols.name, node_aliases=original.symbols.node_aliases,
                                            singleton_aliases=original.symbols.singleton_aliases, variable_aliases=aliases))
    assert write_program(result) == data, 'Restoring debug metadata changed the bytes'
    return result
