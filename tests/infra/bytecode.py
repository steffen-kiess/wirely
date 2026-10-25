"""Round-trip execution data and symbols, retaining source-only context."""

from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any

from wirely import types
from wirely.program import (
    BranchTarget, Constant, ConstOperand, Function, InputSource, OutputTarget, Procedure, Program,
    RunStage, Variable, VariableTable,
)
from wirely.program.loader import load_program
from wirely.program.writer import write_program
from wirely.runtime.extensions import NativeTypes

bytecode_roundtrip_mode: ContextVar[bool] = ContextVar('bytecode_roundtrip_mode', default=False)


@dataclass
class ProgramMatch:
    """Correspondence from an original program to its decoded counterpart."""

    type_pairs: dict[int, tuple[types.Type, types.Type]] = field(default_factory=dict)  # By id of the original type.
    variables: dict[Variable, Variable] = field(default_factory=dict)
    constants: dict[int, Constant] = field(default_factory=dict)  # By id of the original constant.


_TYPE_KINDS: tuple[type[types.Type], ...] = (
    types.TypeUnit, types.TypeBoolean, types.TypeInt, types.TypeString, types.TypeValueType, types.TypeEnum,
    types.TypeStruct, types.TypeResource, types.TypeOption, types.TypeList,
)


class _Matcher:
    def __init__(self, symbols: bool = True) -> None:
        self.symbols = symbols
        self.match = ProgramMatch()
        self._reverse: dict[int, types.Type] = {}

    def type_pair(self, old: types.Type, new: types.Type, where: str) -> None:
        if id(old) in self.match.type_pairs:
            assert self.match.type_pairs[id(old)][1] is new, f'{where}: `{old}` decodes to distinct types'
            return
        if self.symbols and isinstance(old, types.TypeStruct):
            assert isinstance(new, types.TypeStruct)
            assert old.type_name == new.type_name, f'{where}: declared type name changed'
            assert old.field_names == new.field_names, f'{where}: field names changed'
        elif self.symbols and isinstance(old, types.TypeEnum):
            assert isinstance(new, types.TypeEnum)
            assert old.type_name == new.type_name, f'{where}: declared type name changed'
            assert dict(old.members) == dict(new.members), f'{where}: enum member names changed'
        nominal = isinstance(old, (types.TypeEnum, types.TypeStruct, types.TypeResource))
        if nominal:
            # Distinct declared types stay distinct, and shared ones stay shared.
            assert self._reverse.setdefault(id(new), old) is old, f'{where}: distinct types decode to one `{new}`'
        self.match.type_pairs[id(old)] = old, new
        assert self._kind(old) is self._kind(new), f'{where}: `{old}` decodes to `{new}`'
        if isinstance(old, types.TypeInt) and isinstance(new, types.TypeInt):
            assert (old.is_signed, old.bits) == (new.is_signed, new.bits), f'{where}: `{old}` decodes to `{new}`'
        elif isinstance(old, (types.TypeOption, types.TypeList)) and isinstance(new, (types.TypeOption, types.TypeList)):
            self.type_pair(old.inner_type, new.inner_type, f'{where} inner type')
        elif isinstance(old, types.TypeEnum) and isinstance(new, types.TypeEnum):
            assert (old.is_open, old.member_count, old.native_name) == (new.is_open, new.member_count, new.native_name), \
                f'{where}: enum `{old}` changed'
            self.type_pair(old.underlying_type, new.underlying_type, f'{where} underlying type')
            assert self._enum_json(old) == self._enum_json(new), f'{where}: JSON names of `{old}` changed'
        elif isinstance(old, types.TypeStruct) and isinstance(new, types.TypeStruct):
            assert (old.native_name, old.is_opaque, old.json_names, len(old.field_types)) == (
                new.native_name, new.is_opaque, new.json_names, len(new.field_types)), f'{where}: struct `{old}` changed'
            for index, (old_field, new_field) in enumerate(zip(old.field_types, new.field_types)):
                self.type_pair(old_field, new_field, f'{where} field {index}')
        elif isinstance(old, types.TypeResource) and isinstance(new, types.TypeResource):
            assert old.native_name == new.native_name, f'{where}: resource `{old}` changed'

    @staticmethod
    def _kind(value_type: types.Type) -> type[types.Type] | None:
        # The compiler uses subclasses of runtime types for declared types.
        return next((kind for kind in _TYPE_KINDS if isinstance(value_type, kind)), None)

    @staticmethod
    def _enum_json(enum_type: types.TypeEnum) -> object:
        if not enum_type.is_open:
            # The writer omits closed enums without a name for every member.
            complete = enum_type.is_json_serializable and len(enum_type.json_names) == enum_type.member_count
            return dict(enum_type.json_names) if complete else None
        return enum_type.is_json_serializable, dict(enum_type.json_names)

    def value(self, value_type: types.Type, old: Any, new: Any, where: str) -> None:
        if isinstance(value_type, types.TypeValueType):
            self.type_pair(old, new, f'{where} type value')
        elif isinstance(value_type, types.TypeStruct) and type(old) is tuple:
            assert type(new) is tuple and len(new) == len(old), f'{where}: {old!r} decodes to {new!r}'
            for index, (field_type, old_field, new_field) in enumerate(zip(value_type.field_types, old, new)):
                self.value(field_type, old_field, new_field, f'{where} field {index}')
        elif isinstance(value_type, types.TypeList):
            assert len(old) == len(new), f'{where}: {old!r} decodes to {new!r}'
            for index, (old_item, new_item) in enumerate(zip(old, new)):
                self.value(value_type.inner_type, old_item, new_item, f'{where} item {index}')
        elif isinstance(value_type, types.TypeOption):
            assert (old is None) == (new is None), f'{where}: {old!r} decodes to {new!r}'
            if old is not None:
                self.value(value_type.inner_type, old.value, new.value, f'{where} option value')
        else:
            assert type(old) is type(new) and old == new, f'{where}: {old!r} decodes to {new!r}'

    def constant(self, old: Constant, new: Constant, where: str) -> None:
        self.type_pair(old.value_type, new.value_type, where)
        self.value(old.value_type, old.value, new.value, where)
        self.match.constants.setdefault(id(old), new)

    def variable_table(self, old: VariableTable, new: VariableTable, where: str) -> None:
        assert len(old.offsets) == len(new.offsets), f'{where}: variable count changed'
        for index, ((old_var, old_offset), (new_var, new_offset)) in enumerate(zip(old.offsets.items(), new.offsets.items())):
            assert old_offset == new_offset, f'{where}[{index}]: offset changed'
            if self.symbols:
                assert old_var.name == new_var.name, f'{where}[{index}]: variable name changed'
            if where != 'state':
                assert old_var.name is None and new_var.name is None, f'{where}[{index}]: unexpected canonical name'
            self.type_pair(old_var.var_type, new_var.var_type, f'{where}[{index}]')
            self.match.variables[old_var] = new_var

    def binding(self, old: InputSource | OutputTarget, new: InputSource | OutputTarget, where: str) -> None:
        assert old.bank == new.bank, f'{where}: bank changed'
        if isinstance(old, InputSource) and old.constant is not None:
            assert isinstance(new, InputSource) and new.constant is not None, f'{where}: constant changed'
            self.constant(old.constant, new.constant, where)
        else:
            assert old.variable is not None
            assert self.match.variables.get(old.variable) is new.variable, f'{where}: variable changed'

    def procedure(self, old: Procedure, new: Procedure, where: str) -> None:
        if self.symbols:
            assert old.name == new.name, f'{where}: procedure name changed'
        assert old.native == new.native, f'{where}: native procedure changed'
        assert (len(old.inputs), len(old.outputs)) == (len(new.inputs), len(new.outputs)), f'{where}: bindings changed'
        for index, (old_source, new_source) in enumerate(zip(old.inputs, new.inputs)):
            self.binding(old_source, new_source, f'{where} input {index}')
        for index, (old_target, new_target) in enumerate(zip(old.outputs, new.outputs)):
            self.binding(old_target, new_target, f'{where} output {index}')
        assert (old.body is None) == (new.body is None), f'{where}: implementation changed'
        if old.body is None or new.body is None:
            return
        assert len(old.body.local_types) == len(new.body.local_types), f'{where}: locals changed'
        for index, (old_type, new_type) in enumerate(zip(old.body.local_types, new.body.local_types)):
            self.type_pair(old_type, new_type, f'{where} local {index}')
        assert len(old.body.instructions) == len(new.body.instructions), f'{where}: instruction count changed'
        for index, (old_instruction, new_instruction) in enumerate(zip(old.body.instructions, new.body.instructions)):
            location = f'{where} instruction {index}'
            assert old_instruction.opcode == new_instruction.opcode, f'{location}: opcode changed'
            assert len(old_instruction.operands) == len(new_instruction.operands), f'{location}: operand count changed'
            for position, (old_operand, new_operand) in enumerate(zip(old_instruction.operands, new_instruction.operands)):
                operand_where = f'{location} operand {position}'
                if isinstance(old_operand, ConstOperand):
                    assert isinstance(new_operand, ConstOperand), f'{operand_where}: operand changed'
                    self.constant(old_operand.constant, new_operand.constant, operand_where)
                elif isinstance(old_operand, Constant):
                    assert isinstance(new_operand, Constant), f'{operand_where}: operand changed'
                    self.constant(old_operand, new_operand, operand_where)
                # Function signatures can contain declared types not used by variables.
                elif isinstance(old_operand, Function):
                    assert isinstance(new_operand, Function) and old_operand.name == new_operand.name, \
                        f'{operand_where}: function changed'
                    assert len(old_operand.parameter_types) == len(new_operand.parameter_types), \
                        f'{operand_where}: function signature changed'
                    for parameter, (old_type, new_type) in enumerate(zip(old_operand.parameter_types,
                                                                          new_operand.parameter_types)):
                        self.type_pair(old_type, new_type, f'{operand_where} parameter {parameter}')
                    self.type_pair(old_operand.return_type, new_operand.return_type, f'{operand_where} result')
                else:
                    assert type(old_operand) is type(new_operand) and old_operand == new_operand, \
                        f'{operand_where}: {old_operand!r} decodes to {new_operand!r}'

    def program(self, old: Program, new: Program) -> None:
        for where, old_table, new_table in (('state', old.state_variables, new.state_variables),
                                            ('temporaries', old.temporaries, new.temporaries),
                                            ('fixed', old.fixed_values, new.fixed_values)):
            self.variable_table(old_table, new_table, where)
        for variable in old.state_variables.offsets:
            self.value(variable.var_type, old.initial_state[variable],
                       new.initial_state[self.match.variables[variable]], f'initial state of `{variable}`')
        for stage in RunStage:
            old_schedule, new_schedule = old.procs.get(stage, ()), new.procs.get(stage, ())
            assert len(old_schedule) == len(new_schedule), f'{stage.name}: schedule length changed'
            for index, (old_proc, new_proc) in enumerate(zip(old_schedule, new_schedule)):
                self.procedure(old_proc, new_proc, f'{stage.name}[{index}]')

        if self.symbols:
            assert old.symbols.variable_aliases.keys() == new.symbols.variable_aliases.keys(), 'Value aliases changed'
            for name, source in old.symbols.variable_aliases.items():
                self.binding(source, new.symbols.variable_aliases[name], f'Alias {name}')


def match_programs(original: Program, loaded: Program, *, symbols: bool = True) -> ProgramMatch:
    """Assert that decoded execution data and optional symbols match the original."""
    matcher = _Matcher(symbols)
    matcher.program(original, loaded)
    return matcher.match


def roundtrip_program(original: Program, native_types: NativeTypes) -> Program:
    """Use decoded runtime data and symbols, retaining only source-only context."""
    data = write_program(original)
    loaded = load_program(data, native_types=native_types)
    assert write_program(loaded) == data, 'Bytecode write/load/write changed the bytes'
    match_programs(original, loaded)
    return replace(loaded, graph=original.graph, symbols=replace(
        loaded.symbols, name=original.symbols.name, node_aliases=original.symbols.node_aliases,
        singleton_aliases=original.symbols.singleton_aliases))
