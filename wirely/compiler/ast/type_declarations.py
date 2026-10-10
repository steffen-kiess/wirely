"""Struct, enum, and resource type declarations."""

import enum

from wirely.compiler import model
from wirely.util import check_parameters
from wirely.compiler.types import parse_value

from .base import SourcePosition, SourceElement, CompilerHint, ProgramError, Identifier, Declaration, Value
from .values import IntNumber, Reference


class EnumMember(SourceElement):
    @check_parameters
    def __init__(self, name: Identifier, value: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.value = value


class StructField(SourceElement):
    @check_parameters
    def __init__(self, name: Identifier, field_type: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.field_type = field_type


class DeclarationStructType(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, fields: list,
                 *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.fields = fields
        self.inner_attributes = getattr(fields, 'inner_attributes', ())
        self.value_type = None
        self.resolving = False

    def compile_pass1_btd(self, interface):
        if interface.name is not None:
            raise ProgramError(self, 'Struct type declaration not allowed inside node type')

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in model.types.type_by_name or name in ('Option', 'List'):
            raise ProgramError(self.name, f'Struct type {name} conflicts with built-in type')
        for kind, declarations in (
                ('struct type', graph.struct_types), ('enum type', graph.enum_types),
                ('node type', graph.node_type_interfaces), ('resource type', graph.resource_types),
                ('namespace', graph.libraries)):
            if name in declarations:
                previous = declarations[name]
                if kind in ('enum type', 'resource type'):
                    previous = previous.declaration
                raise ProgramError(self.name, f'Struct type {name} conflicts with {kind}', hints=[
                    CompilerHint(previous, 'Previous declaration was here'),
                ])
        self.scope = graph
        graph.struct_types[name] = self

    def resolve_fields(self):
        if self.value_type is not None:
            return self.value_type
        if self.resolving:
            raise ProgramError(self.name, f'Recursive struct type {self.name.id_str}')
        self.resolving = True
        try:
            fields = {}
            previous = {}
            for field in self.fields:
                name = field.name.id_str
                if name in fields:
                    raise ProgramError(field.name, f'Duplicate struct field {name}', hints=[
                        CompilerHint(previous[name].name, 'Previous field was here'),
                    ])
                fields[name] = self.scope.resolve_type(field.field_type)
                previous[name] = field
            fullname = self.scope.fullname + '.' + self.name.id_str if isinstance(self.scope, model.ast.DeclarationLibrary) else self.name.id_str
            self.value_type = model.types.TypeStruct(fullname, fields)
            return self.value_type
        finally:
            self.resolving = False

    def create_io(self, new_graph):
        pass

    def compile_pass1_5(self, graph):
        self.resolve_fields()

    def compile_pass2(self, graph):
        pass


class DeclarationEnumType(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, underlying_type: Value,
                 members: list, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.underlying_type = underlying_type
        self.members = members
        self.inner_attributes = getattr(members, 'inner_attributes', ())

    def compile_pass1_btd(self, interface):
        if interface.name is not None:
            raise ProgramError(self, 'Enum type declaration not allowed inside node type')

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in model.types.type_by_name or name in ('Option', 'List'):
            raise ProgramError(self.name, f'Enum type {name} conflicts with built-in type')
        for kind, declarations in (
                ('enum type', graph.enum_types),
                ('struct type', graph.struct_types),
                ('node type', graph.node_type_interfaces),
                ('resource type', graph.resource_types),
                ('namespace', graph.libraries)):
            if name in declarations:
                previous = declarations[name]
                if kind in ('enum type', 'resource type'):
                    previous = previous.declaration
                raise ProgramError(self.name, f'Enum type {name} conflicts with {kind}', hints=[
                    CompilerHint(previous, 'Previous declaration was here'),
                ])
        underlying = self.underlying_type
        if not isinstance(underlying, Reference) or underlying.length != 1:
            raise ProgramError(underlying, 'Enum underlying type must be a supported integer type')
        underlying = model.types.type_by_name.get(underlying.identifiers[0].id_str)
        if not isinstance(underlying, model.types.TypeInt):
            raise ProgramError(self.underlying_type, 'Enum underlying type must be a supported integer type')
        if not self.members:
            raise ProgramError(self.name, 'Enum must declare at least one member')
        names = {}
        values = {}
        numbers = {}
        for member in self.members:
            member_name = member.name.id_str
            if member_name in names:
                raise ProgramError(member.name, f'Duplicate enum member {member_name}', hints=[
                    CompilerHint(names[member_name], 'Previous member was here'),
                ])
            if not isinstance(member.value, IntNumber):
                raise ProgramError(member.value, 'Enum member values must be integer literals')
            number = parse_value(underlying, member.value)
            if number in values:
                raise ProgramError(member.value, f'Duplicate enum value {number}', hints=[
                    CompilerHint(values[number], 'Previous member was here'),
                ])
            names[member_name] = member
            values[number] = member
            numbers[member_name] = number
        fullname = graph.fullname + '.' + name if isinstance(graph, model.ast.DeclarationLibrary) else name
        try:
            python_type = enum.Enum(name, numbers)
        except (TypeError, ValueError) as error:
            raise ProgramError(self, str(error)) from None
        for member_name, member in names.items():
            if member_name not in python_type.__members__:
                raise ProgramError(member.name, f'Unsupported enum member name {member_name}')
        self.value_type = model.types.TypeEnum(fullname, python_type, underlying_type=underlying)
        self.value_type.declaration = self
        graph.enum_types[name] = self.value_type

    def create_io(self, new_graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass


class DeclarationResourceType(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name

    def compile_pass1_btd(self, interface):
        if interface.name is not None:
            raise ProgramError(self, 'Resource type declaration not allowed inside node type')

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.struct_types:
            raise model.ProgramError(self.name, f'Resource type {name} conflicts with struct type', hints=[
                model.CompilerHint(graph.struct_types[name], 'Previous declaration was here'),
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
        if name in graph.node_type_interfaces:
            raise ProgramError(self, f'Resource type {name} conflicts with node type', hints=[
                CompilerHint(graph.node_type_interfaces[name], 'Previous node type was here'),
            ])
        if isinstance(graph, model.ast.DeclarationLibrary):
            fullname = graph.fullname + '.' + name
        else:
            fullname = name
        graph.resource_types[name] = model.ResourceType(fullname, self)

    def create_io(self, new_graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass
