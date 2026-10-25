"""Scopes holding declarations, with name and type resolution."""

import abc
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from ..coordinator import CompilerCoordinator

from wirely.compiler import model
from wirely.compiler.types import parse_value

from .base import ProgramError, Value
from .values import Reference, FunctionCall, OptionalExpression, BracketOperator, StructLiteral, check_struct_literal

T = TypeVar('T')


class Scope(abc.ABC):
    """The declarations of a node type or namespace in a compilation, with name and type resolution."""

    # Set by the subclasses.
    services: Mapping[str, object]
    coordinator: 'CompilerCoordinator'
    parent_graph: 'Scope | None'

    @property
    @abc.abstractmethod
    def name(self) -> 'model.Identifier | None':
        ...

    @property
    def fullname_tuple(self) -> tuple[str, ...]:
        raise NotImplementedError

    def __init__(self) -> None:
        self.libraries: dict[str, model.Namespace] = {}
        self.node_types: dict[str, model.NodeType] = {}
        self.resource_types: dict[str, model.ResourceType] = {}
        self.enum_types: dict[str, model.EnumType] = {}
        self.struct_types: dict[str, model.StructDefinition] = {}
        self.connections: list[model.Connection] = []
        self.native_functions: dict[str, model.CompiledFunction] = {}

    @property
    @abc.abstractmethod
    def fullname(self) -> str:
        ...

    @property
    def static_declarations(self) -> 'dict[str, model.NodeParameter]':
        return {**getattr(self, 'parsed_parameters', {}),
                **getattr(self, 'parsed_constants', {})}

    # TODO: Handle parameters for parent types properly? Does this even make sense?
    def resolve_type(self, ty: Value, par_values: Mapping[Any, Any] = {}) -> 'model.types.Type':
        with ty.context:
            # TODO: Clean up
            if isinstance(ty, BracketOperator):
                if not isinstance(ty.base, Reference):
                    raise model.ProgramError(ty, f'Expected Reference as generic type base, got {ty}')
                gen_type: type[model.types.TypeOption] | type[model.types.TypeList]
                if ty.base.length == 1 and ty.base.identifiers[0].id_str == 'Option':
                    gen_type = model.types.TypeOption
                elif ty.base.length == 1 and ty.base.identifiers[0].id_str == 'List':
                    gen_type = model.types.TypeList
                else:
                    raise model.ProgramError(ty, f'Cannot find generic type `{ty.base}`')
                # if len(ty.parameters) != 1:
                #     raise model.ProgramError(ty, f'Expected 1 type parameter, got {len(ty.parameters)}: {ty}')
                par_type = self.resolve_type(ty.parameter, par_values=par_values)
                if isinstance(par_type, model.ResourceType):
                    raise ProgramError(ty, 'Resources cannot be members of composite types')
                return gen_type(par_type)
            elif not isinstance(ty, Reference):
                raise model.ProgramError(ty, f'Expected Reference or BracketOperator as type name, got {ty}')

            # TODO: Move somewhere else, clean up
            if ty.length == 1:
                name = ty.identifiers[0].id_str
                # TODO: Avoid hasattr
                if name in self.static_declarations:
                    par = self.static_declarations[name]
                    # A parameter whose type refers to itself is not a type.
                    if not getattr(par, 'resolving_type', False):
                        par.resolve_type()
                    declared_type = getattr(par, 'type_resolved', None)
                    is_type = (isinstance(par.ty, Reference) and par.ty.length == 1
                               and par.ty.identifiers[0].id_str == 'type')
                    if (is_type or declared_type == model.types.ValueType
                            or (declared_type is not None and declared_type.contains_parameters)):
                        if par not in par_values:
                            return model.TypeParameter(par, ast_element=ty)
                        result = par_values[par]
                        if isinstance(result, model.Value):
                            return model.TypeParameter(par, ast_element=ty)
                        if not isinstance(result, model.Type):
                            raise ProgramError(ty, f'Expected a type for `{name}`')
                        return result
                if name in model.types.type_by_name:
                    return model.types.type_by_name[name]
            constant = self.resolve_constant(ty)
            if constant is not None:
                constant.resolve_type()
                if constant.type_resolved == model.types.ValueType:
                    if isinstance(constant, model.NamespaceConstant):
                        value = constant.value()
                        assert isinstance(value, model.Type)
                        return value
                    if constant not in par_values:
                        return model.TypeParameter(constant, ast_element=ty)
                    result = par_values[constant]
                    if not isinstance(result, model.Type):
                        raise ProgramError(ty, f'Expected a type for `{ty}`')
                    return result
            scope: Scope | None = self
            while scope is not None:
                candidate: Scope | None = scope
                for identifier in ty.identifiers[:-1]:
                    assert candidate is not None
                    candidate = candidate.libraries.get(identifier.id_str)
                    if candidate is None:
                        break
                if candidate is not None:
                    resource = candidate.resource_types.get(ty.identifiers[-1].id_str)
                    if resource is not None:
                        return resource
                    struct = candidate.struct_types.get(ty.identifiers[-1].id_str)
                    if struct is not None:
                        return struct.resolve_fields()
                    enum_type = candidate.enum_types.get(ty.identifiers[-1].id_str)
                    if enum_type is not None:
                        return enum_type
                scope = scope.parent_graph
            raise model.ProgramError(ty, f'Cannot find type `{ty}`')

    def _lookup(self, names: tuple[str, ...], find: 'Callable[[Scope, str], T | None]') -> 'T | None':
        """Find a declaration by its namespace path, searching this and enclosing scopes."""
        scope: Scope | None = self
        while scope is not None:
            candidate: Scope | None = scope
            for name in names[:-1]:
                assert candidate is not None
                candidate = candidate.libraries.get(name)
                if candidate is None:
                    break
            if candidate is not None:
                result = find(candidate, names[-1])
                if result is not None:
                    return result
            scope = scope.parent_graph
        return None

    def resolve_function(self, reference: Reference, *,
                         required: bool = True) -> 'model.CompiledFunction | None':
        names = tuple(identifier.id_str for identifier in reference.identifiers)
        function = self._lookup(names, lambda scope, name: scope.native_functions.get(name))
        if function is not None:
            return function.resolve_signature()
        if required:
            raise ProgramError(reference, f'Cannot find function `{".".join(names)}`')
        return None

    def resolve_constant(self, reference: Value) -> 'model.NamespaceConstant | model.NodeParameter | None':
        """Find a namespace or root constant visible from this scope.

        The scope's own parameters and constants shadow it; callers check those first.
        """
        if not isinstance(reference, Reference):
            return None
        names = tuple(identifier.id_str for identifier in reference.identifiers)
        if len(names) == 1 and names[0] in self.static_declarations:
            return None

        def find(scope: Scope, name: str) -> 'model.NamespaceConstant | model.NodeParameter | None':
            if isinstance(scope, model.Namespace):
                return scope.constants.get(name)
            # The root has no arguments, so its constants have one value; constants of other node types
            # depend on their instance.
            if scope.parent_graph is None and isinstance(scope, model.NodeType):
                return scope.parsed_constants.get(name)
            return None

        # mypy joins the union to object when inferring T.
        return cast('model.NamespaceConstant | model.NodeParameter | None', self._lookup(names, find))

    def resolve_resource_type(self, name: Value) -> 'model.ResourceType':
        assert isinstance(name, Reference)
        names = tuple(identifier.id_str for identifier in name.identifiers)
        resource_type = self._lookup(names, lambda scope, name: scope.resource_types.get(name))
        if resource_type is not None:
            return resource_type
        raise model.ProgramError(name, f'Cannot find resource type `{name}`; fixed values of other types are not '
                                 'supported yet')

    # TODO: Should this be named get_const_value()? (Also returns e.g. ValueFunctionCall)
    def get_const_value(self, value: Value, value_type: 'model.types.Type',
                        par_values: Mapping[Any, Any] = {}) -> 'model.value.Value':
        with value.context:
            from ..expressions import (
                Expression, PropertyAccess, compile_constant, property_reference, type_member,
            )
            value = property_reference(self, value)
            if (isinstance(value, FunctionCall) and not value_type.contains_parameters
                    and type_member(self, value.fun, par_values) is not None):
                return compile_constant(self, value, value_type, par_values)
            if (isinstance(value, FunctionCall) and not value_type.contains_parameters
                    and isinstance(property_reference(self, value.fun), PropertyAccess)):
                return compile_constant(self, value, value_type, par_values)
            if isinstance(value, OptionalExpression):
                assert isinstance(self, model.NodeType)
                parameter = value.declaration(self)
                result = model.value.ParameterOptionValue(parameter, ast_element=value)
                model.value.check_value_type(result.value_type, value_type, value)
                return result
            if value_type.contains_parameters:
                if (isinstance(value, Reference) and value.length == 1
                        and value.identifiers[0].id_str not in self.static_declarations
                        and value.identifiers[0].id_str not in ('true', 'false', 'None')
                        and self.resolve_constant(value) is None):
                    try:
                        self.resolve_type(value, par_values)
                    except ProgramError:
                        raise ProgramError(value, f'Unknown static value `{value}`') from None
                return model.value.DeferredValue(self, value, value_type)
            constant = self.resolve_constant(value)
            if constant is not None:
                return self.constant_value(constant, value, value_type, par_values)
            if (isinstance(value, Expression)
                    or (isinstance(value, Reference) and value.length == 1
                        and value.identifiers[0].id_str in ('default input', 'default output'))):
                return compile_constant(self, value, value_type, par_values)
            if value_type == model.types.ValueType and not isinstance(value, model.FunctionCall):
                return model.ConstantValue(value_type, self.resolve_type(value, par_values=par_values),
                                            ast_element=value)
            if isinstance(value, StructLiteral):
                fields = check_struct_literal(value_type, value, self)
                assert isinstance(value_type, model.types.TypeStruct)
                return model.value.ValueStruct(
                    value_type,
                    {name: self.get_const_value(field.value, value_type.fields[name], par_values=par_values)
                     for name, field in fields.items()}, ast_element=value,
                )
            if isinstance(value, model.ListLiteral) and isinstance(value_type, model.types.TypeList):
                elements = [self.get_const_value(element, value_type.inner_type, par_values=par_values)
                            for element in value.values]
                return model.value.ValueList(value_type, elements, ast_element=value)
            if isinstance(value, model.FunctionCall):
                if not isinstance(value.fun, model.Reference):
                    raise model.ProgramError(value.fun, 'Function is not a reference')
                function_name = '.'.join(identifier.id_str for identifier in value.fun.identifiers)
                if function_name == 'Some':
                    if len(value.parameters) != 1:
                        raise model.ProgramError(value, 'Some expects exactly one argument')
                    if not isinstance(value_type, model.types.TypeOption):
                        raise model.ProgramError(value, 'Some requires an Option type')
                    inner = self.get_const_value(value.parameters[0], value_type.inner_type, par_values=par_values)
                    return model.value.ValueOptionSome(value_type, inner, ast_element=value)
                function = self.resolve_function(value.fun)
                assert function is not None
                model.value.check_value_type(function.return_type, value_type, value)
                if len(value.parameters) != len(function.parameter_types):
                    raise model.ProgramError(value, f'Function `{function_name}` expected {len(function.parameter_types)} parameters, but got {len(value.parameters)}')
                parameters = [self.get_const_value(parameter, function.parameter_types[i], par_values=par_values) for i, parameter in enumerate(value.parameters)]
                return model.value.ValueFunctionCall(function=function, parameters=parameters, ast_element=value)

            # TODO: Clean up
            found = True
            while found:
                found = False
                if isinstance(value, model.Reference) and value.length == 1:
                    name = value.identifiers[0].id_str
                    if name in self.static_declarations:
                        par = self.static_declarations[name]
                        if not(isinstance(par.ty, model.Reference) and par.ty.length == 1
                               and par.ty.identifiers[0].id_str == 'type'):
                            parameter_type = self.resolve_type(par.ty, par_values=par_values)
                            model.value.check_value_type(parameter_type, value_type, value)
                            # value = par_values[name]
                            # found = True
                            # print(f'{par.name} in {par_values}')
                            # TODO: Clean up / Check whether this really is a parameter
                            if par not in par_values:
                                return model.ValueParameter(value_type, par, ast_element=value)
                            return model.ConstantValue(value_type, value_type.verify_value(par_values[par].evaluate()), ast_element=value)
            return model.ConstantValue(value_type, parse_value(value_type, value, scope=self), ast_element=value)
            # raise model.ProgramError(value, f'Cannot turn {value!r} into constant value of type {value_type}')

    def constant_value(self, constant: 'model.NamespaceConstant | model.NodeParameter', reference: Value,
                       value_type: 'model.types.Type', par_values: Mapping[Any, Any]) -> 'model.value.Value':
        """Return a namespace or root constant as a value of value_type."""
        if isinstance(constant, model.NamespaceConstant):
            result = constant.value()
            if isinstance(result, model.Type):
                model.value.check_value_type(model.types.ValueType, value_type, reference)
                return model.ConstantValue(model.types.ValueType, result, ast_element=reference)
            assert isinstance(result, model.Value)
            model.value.check_value_type(result.value_type, value_type, reference)
            return model.ConstantValue(result.value_type, result.evaluate(), ast_element=reference)
        constant.resolve_type()
        model.value.check_value_type(constant.type_resolved, value_type, reference)
        if constant not in par_values:
            return model.ValueParameter(constant.type_resolved, constant, ast_element=reference)
        result = par_values[constant]
        if isinstance(result, model.Type):
            return model.ConstantValue(model.types.ValueType, result, ast_element=reference)
        return model.ConstantValue(constant.type_resolved, result.evaluate(), ast_element=reference)

    def resolve_node_type(self, name: Reference, par_values: Mapping[Any, Any] = {}) -> 'model.NodeType':
        # print(self, 'resolve_node_type', name)

        if name.length >= 2:
            library = self.libraries.get(name.identifiers[0].id_str)
            for identifier in name.identifiers[1:-1]:
                if library is None:
                    break
                library = library.libraries.get(identifier.id_str)
            if library is not None:
                node_name = name.identifiers[-1].id_str
                if node_name in library.node_types:
                    return library.node_types[node_name]

        if name.length == 1:
            node_type_name_id = name.identifiers[0]

            if node_type_name_id.id_str in self.node_types:
                return self.node_types[node_type_name_id.id_str]

        if self.parent_graph is None:
            raise model.ProgramError(name, f'Cannot find node type `{name}`')
        return self.parent_graph.resolve_node_type(name, par_values=par_values)
