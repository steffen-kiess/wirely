"""Struct, enum, and resource type declarations."""

import typing

from wirely.compiler import model
from wirely.util import check_parameters
from wirely.compiler.types import parse_value

from .base import SourcePosition, SourceElement, CompilerHint, ProgramError, Identifier, Declaration, Value
from .values import IntNumber, Reference, StringConstant


class EnumMember(SourceElement):
    @check_parameters
    def __init__(self, name: Identifier, value: typing.Optional[Value], *, source_position: SourcePosition) -> None:
        super().__init__(source_position=source_position)
        self.name = name
        self.value = value

    @staticmethod
    def create_closed(name: Identifier, *, source_position: SourcePosition) -> 'EnumMember':
        return EnumMember(name, None, source_position=source_position)


class StructField(SourceElement):
    @check_parameters
    def __init__(self, name: Identifier, field_type: Value, *, source_position: SourcePosition) -> None:
        super().__init__(source_position=source_position)
        self.name = name
        self.field_type = field_type


class DeclarationStructType(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, fields: list,
                 *, is_native: bool = False, alias: typing.Optional[StringConstant] = None,
                 source_position: SourcePosition) -> None:
        super().__init__(source_position=source_position)
        self.name = name
        self.fields = fields
        self.is_native = is_native
        self.alias = alias  # Native name replacing the qualified name.
        self.inner_attributes = getattr(fields, 'inner_attributes', ())

    @staticmethod
    def create_native(name: Identifier, alias: typing.Optional[StringConstant], fields: list, *,
                      source_position: SourcePosition) -> 'DeclarationStructType':
        return DeclarationStructType(name, fields, is_native=True, alias=alias, source_position=source_position)

    def register_interface_declarations(self, interface: 'model.Scope') -> None:
        if interface.name is not None:
            raise ProgramError(self, 'Struct type declaration not allowed inside node type')

    def collect_declarations(self, graph: 'model.Scope') -> None:
        name = self.name.id_str
        if name in model.types.type_by_name or name in ('Option', 'List'):
            raise ProgramError(self.name, f'Struct type {name} conflicts with built-in type')
        for kind, declarations in (
                ('struct type', graph.struct_types), ('enum type', graph.enum_types),
                ('node type', graph.node_types), ('resource type', graph.resource_types),
                ('namespace', graph.libraries)):
            if name in declarations:
                previous = declarations[name].declaration
                raise ProgramError(self.name, f'Struct type {name} conflicts with {kind}', hints=[
                    CompilerHint(previous, 'Previous declaration was here'),
                ])
        graph.struct_types[name] = StructDefinition(self, graph)

    def prepare_interface(self, graph: 'model.Scope') -> None:
        graph.struct_types[self.name.id_str].resolve_fields()


class StructDefinition:
    """A struct type declared in a scope, in a compilation."""

    def __init__(self, declaration: DeclarationStructType, scope: 'model.Scope') -> None:
        self.declaration = declaration
        self.scope = scope
        self.value_type: model.types.TypeStruct | None = None
        self.resolving = False

    def resolve_fields(self) -> 'model.types.TypeStruct':
        if self.value_type is not None:
            return self.value_type
        if self.resolving:
            raise ProgramError(self.declaration.name, f'Recursive struct type {self.declaration.name.id_str}')
        self.resolving = True
        try:
            fields = {}
            previous: dict[str, typing.Any] = {}
            for field in self.declaration.fields:
                name = field.name.id_str
                if name in fields:
                    raise ProgramError(field.name, f'Duplicate struct field {name}', hints=[
                        CompilerHint(previous[name].name, 'Previous field was here'),
                    ])
                fields[name] = self.scope.resolve_type(field.field_type)
                previous[name] = field
            type_name = self.declaration.name.id_str
            fullname = (self.scope.fullname + '.' + type_name if isinstance(self.scope, model.ast.Namespace)
                        else type_name)
            native_name = None
            if self.declaration.is_native:
                alias = self.declaration.alias
                native_name = alias.value if alias is not None else fullname
            self.value_type = model.types.TypeStruct(fullname, fields, native_name=native_name)
            return self.value_type
        finally:
            self.resolving = False


class DeclarationEnumType(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, underlying_type: typing.Optional[Value],
                 members: list, *, is_open: bool, is_native: bool = False,
                 alias: typing.Optional[StringConstant] = None, source_position: SourcePosition) -> None:
        super().__init__(source_position=source_position)
        self.name = name
        self.underlying_type = underlying_type
        self.is_open = is_open
        self.is_native = is_native
        self.alias = alias  # Native name replacing the qualified name.
        self.members = members
        self.inner_attributes = getattr(members, 'inner_attributes', ())

    @staticmethod
    def create_open(name: Identifier, underlying_type: Value, members: list, *,
                    source_position: SourcePosition) -> 'DeclarationEnumType':
        return DeclarationEnumType(name, underlying_type, members, is_open=True, source_position=source_position)

    @staticmethod
    def create_closed(name: Identifier, members: list, *, source_position: SourcePosition) -> 'DeclarationEnumType':
        return DeclarationEnumType(name, None, members, is_open=False, source_position=source_position)

    @staticmethod
    def create_native_open(name: Identifier, alias: typing.Optional[StringConstant], underlying_type: Value,
                           members: list, *, source_position: SourcePosition) -> 'DeclarationEnumType':
        return DeclarationEnumType(name, underlying_type, members, is_open=True, is_native=True, alias=alias,
                                   source_position=source_position)

    @staticmethod
    def create_native_closed(name: Identifier, alias: typing.Optional[StringConstant], members: list, *,
                             source_position: SourcePosition) -> 'DeclarationEnumType':
        return DeclarationEnumType(name, None, members, is_open=False, is_native=True, alias=alias,
                                   source_position=source_position)

    def register_interface_declarations(self, interface: 'model.Scope') -> None:
        if interface.name is not None:
            raise ProgramError(self, 'Enum type declaration not allowed inside node type')

    def collect_declarations(self, graph: 'model.Scope') -> None:
        name = self.name.id_str
        if name in model.types.type_by_name or name in ('Option', 'List'):
            raise ProgramError(self.name, f'Enum type {name} conflicts with built-in type')
        for kind, declarations in (
                ('enum type', graph.enum_types),
                ('struct type', graph.struct_types),
                ('node type', graph.node_types),
                ('resource type', graph.resource_types),
                ('namespace', graph.libraries)):
            if name in declarations:
                previous = declarations[name].declaration
                raise ProgramError(self.name, f'Enum type {name} conflicts with {kind}', hints=[
                    CompilerHint(previous, 'Previous declaration was here'),
                ])
        underlying: typing.Any
        if self.is_open:
            underlying = self.underlying_type
            if not isinstance(underlying, Reference) or underlying.length != 1:
                raise ProgramError(underlying, 'Enum underlying type must be a supported integer type')
            underlying = model.types.type_by_name.get(underlying.identifiers[0].id_str)
            if not isinstance(underlying, model.types.TypeInt):
                raise ProgramError(self.underlying_type, 'Enum underlying type must be a supported integer type')
        else:
            try:
                underlying = model.types.closed_enum_underlying_type(len(self.members))
            except ValueError as error:
                raise ProgramError(self.name, str(error)) from error
        names: dict[str, typing.Any] = {}
        values: dict[int, typing.Any] = {}
        numbers = {}
        for index, member in enumerate(self.members):
            member_name = member.name.id_str
            if member_name in names:
                raise ProgramError(member.name, f'Duplicate enum member {member_name}', hints=[
                    CompilerHint(names[member_name], 'Previous member was here'),
                ])
            if self.is_open:
                if not isinstance(member.value, IntNumber):
                    raise ProgramError(member.value, 'Enum member values must be integer literals')
                number = parse_value(underlying, member.value)
            else:
                number = index
            if number in values:
                raise ProgramError(member.value, f'Duplicate enum value {number}', hints=[
                    CompilerHint(values[number], 'Previous member was here'),
                ])
            names[member_name] = member
            values[number] = member
            numbers[member_name] = number
        fullname = graph.fullname + '.' + name if isinstance(graph, model.ast.Namespace) else name
        native_name = None
        if self.is_native:
            native_name = self.alias.value if self.alias is not None else fullname
        value_type = model.types.TypeEnum(fullname, numbers, is_open=self.is_open, underlying_type=underlying,
                                          native_name=native_name)
        value_type.declaration = self
        graph.enum_types[name] = value_type


class DeclarationResourceType(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, alias: typing.Optional[StringConstant] = None, *,
                 source_position: SourcePosition) -> None:
        super().__init__(source_position=source_position)
        self.name = name
        self.alias = alias  # Native name replacing the qualified name.

    def register_interface_declarations(self, interface: 'model.Scope') -> None:
        if interface.name is not None:
            raise ProgramError(self, 'Resource type declaration not allowed inside node type')

    def collect_declarations(self, graph: 'model.Scope') -> None:
        name = self.name.id_str
        if name in graph.struct_types:
            raise model.ProgramError(self.name, f'Resource type {name} conflicts with struct type', hints=[
                model.CompilerHint(graph.struct_types[name].declaration, 'Previous declaration was here'),
            ])
        if name in graph.enum_types:
            raise ProgramError(self.name, f'Resource type {name} conflicts with enum type', hints=[
                CompilerHint(graph.enum_types[name].declaration, 'Previous declaration was here'),
            ])
        if name in graph.resource_types:
            previous = graph.resource_types[name].declaration
            raise ProgramError(self, f'Duplicate resource type {name}', hints=[
                CompilerHint(previous, 'Previous resource type was here'),
            ])
        if name in graph.node_types:
            raise ProgramError(self, f'Resource type {name} conflicts with node type', hints=[
                CompilerHint(graph.node_types[name].declaration, 'Previous node type was here'),
            ])
        if isinstance(graph, model.ast.Namespace):
            name_tuple = graph.fullname_tuple + (name,)
        else:
            name_tuple = (name,)
        native_name = self.alias.value if self.alias is not None else '.'.join(name_tuple)
        graph.resource_types[name] = model.ResourceType('.'.join(name_tuple), name_tuple, self, native_name)
