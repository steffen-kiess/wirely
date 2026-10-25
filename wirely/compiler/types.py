"""Compiler types, source literals, and parameter resolution."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:
    from ..runtime.extensions import NativeTypes
    from .parameters import StaticValue

from ..program.limits import is_runtime_type, runtime_type_error
from ..types import Type, TypeOption, TypeList
from .. import types
from . import model


class TypeParameter(Type):
    def __init__(self, parameter: model.NodeParameter, *,
                 ast_element: model.ast.SourceElement | None = None) -> None:
        if not isinstance(parameter, model.NodeParameter):
            raise Exception("not isinstance(parameter, model.NodeParameter)")

        self.parameter = parameter
        self.ast_element = ast_element or parameter.declaration

    @property
    def contains_parameters(self) -> bool:
        return True

    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> StaticValue:
        from .parameters import ParameterValues, resolve_parameter

        if not isinstance(par_values, ParameterValues) and self.parameter in par_values:
            value = par_values[self.parameter]
            return self if isinstance(value, model.Value) else value
        if (not isinstance(par_values, ParameterValues)
                and self.parameter.is_constant):
            return self
        if not isinstance(par_values, ParameterValues) and self.parameter.default_value is not None:
            from .expressions import Expression
            if isinstance(self.parameter.default_value, (model.FunctionCall, Expression)):
                return self
            return self.parameter.node_type.resolve_type(self.parameter.default_value, par_values)
        if self.parameter in par_values or self.parameter.default_value is not None:
            return resolve_parameter(self.parameter, par_values)
        else:
            # TODO
            hints = []
            for par in par_values:
                if not isinstance(par, model.NodeParameter):
                    raise Exception("not isinstance(par, model.NodeParameter)")
                hints += [
                    model.CompilerHint(par.declaration, f'Available parameter: `{par.node_type}.{par.name}`'),
                ]
            raise model.ProgramError(self.ast_element, f'Unable to resolve type parameter `{self.parameter.node_type}.{self.parameter.name}`', hints=hints)

    def __repr__(self) -> str:
        # return f'TypeParameter({self.parameter})'
        return f'TypeParameter({self.parameter})'

    def __str__(self) -> str:
        return self.parameter.name.id_str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TypeParameter):
            return False
        return self.parameter == other.parameter

    def __hash__(self) -> int:
        return hash(('TypeParameter', self.parameter))

    def default_value(self) -> NoReturn:
        raise ValueError('Type parameters require resolution before producing a default value')

    def verify_value(self, value: object) -> NoReturn:
        raise Exception('Not a concrete type')

    def equals(self, value1: object, value2: object) -> NoReturn:
        raise Exception('Not a concrete type')

    def _parse_impl(self, s: str, *, native_types: NativeTypes | None) -> NoReturn:
        raise Exception('Not a concrete type')


def check_runtime_type(value_type: Type, source: model.ast.SourceElement | None) -> None:
    """Check runtime eligibility once any type parameters have resolved."""
    if not value_type.contains_parameters and not is_runtime_type(value_type):
        raise model.ProgramError(source, f'Type `{value_type}` is not a runtime type: {runtime_type_error(value_type)}')


def resolve_type(value_type: Type, par_values: Mapping[model.NodeParameter, StaticValue]) -> Type:
    if not value_type.contains_parameters:
        return value_type
    if isinstance(value_type, TypeParameter):
        result = value_type.resolve_self(par_values)
        if not isinstance(result, Type):
            raise model.ProgramError(value_type.parameter.declaration,
                                      f'Expected a type for `{value_type.parameter.name.id_str}`')
        return result
    if isinstance(value_type, TypeOption):
        return TypeOption(resolve_type(value_type.inner_type, par_values))
    if isinstance(value_type, TypeList):
        return TypeList(resolve_type(value_type.inner_type, par_values))
    raise TypeError(f'Unsupported parameter-dependent type {value_type!r}')


def _parse_option(self: types.TypeOption, value: model.ast.Value, *,
                  scope: model.Scope | None = None) -> model.Some | None:
    if isinstance(value, model.Reference) and value.length == 1 and value.identifiers[0].id_str == 'None':
        return None
    if (isinstance(value, model.FunctionCall)
            and isinstance(value.fun, model.Reference)
            and value.fun.length == 1
            and value.fun.identifiers[0].id_str == 'Some'):
        if len(value.parameters) != 1:
            raise model.ProgramError(value, 'Some expects exactly one argument')
        return model.Some(parse_value(self.inner_type, value.parameters[0], scope=scope))
    raise model.ProgramError(value, f'Invalid Option value {value!r}')


def _parse_list(self: types.TypeList, value: model.ast.Value, *,
                scope: model.Scope | None = None) -> tuple[object, ...]:
    if not isinstance(value, model.ListLiteral):
        raise model.ProgramError(value, f'Invalid List value {value!r}')
    return tuple(parse_value(self.inner_type, v, scope=scope) for v in value.values)


def _parse_unit(self: types.TypeUnit, value: model.ast.Value, *, scope: model.Scope | None = None) -> model.Unit:
    if isinstance(value, model.UnitLiteral):
        return model.Unit()
    raise model.ProgramError(value, f'Invalid unit value {value!r}')


def _parse_boolean(self: types.TypeBoolean, value: model.ast.Value, *, scope: model.Scope | None = None) -> bool:
    if isinstance(value, model.Reference) and value.length == 1:
        s = value.identifiers[0].id_str
        if s == 'false':
            return False
        elif s == 'true':
            return True
    raise model.ProgramError(value, f'Invalid boolean value {value!r}')


def _parse_int(self: types.TypeInt, value: model.ast.Value, *, scope: model.Scope | None = None) -> int:
    if isinstance(value, model.IntNumber):
        i = value.value
        if i < self.min_value or i > self.max_value:
            raise model.ProgramError(value, f'Int value out of range: {i!r}')
        return i
    raise model.ProgramError(value, f'Invalid int value {value!r}')


def _parse_value_type(self: types.TypeValueType, value: model.ast.Value, *,
                      scope: model.Scope | None = None) -> NoReturn:
    raise Exception('TODO: TypeValueType.parse_value()')


def _parse_string(self: types.TypeString, value: model.ast.Value, *, scope: model.Scope | None = None) -> str:
    if isinstance(value, model.StringConstant):
        return value.value
    raise model.ProgramError(value, f'Invalid string value {value!r}')


def _parse_enum(self: types.TypeEnum, expr: model.ast.Value, *, scope: model.Scope | None = None) -> Any:
    if not isinstance(expr, model.Reference) or expr.length < 2:
        raise model.ProgramError(expr, f'Expected enum literal `{self.name}.Member`')
    type_name = '.'.join(identifier.id_str for identifier in expr.identifiers[:-1])
    member_name = expr.identifiers[-1].id_str
    if scope is not None:
        type_reference = model.Reference.from_list(
            *expr.identifiers[:-1], source_position=expr.source_position,
        )
        enum_type = scope.resolve_type(type_reference)
        matches = enum_type is self
    else:
        matches = type_name == self.name
    if not matches:
        raise model.ProgramError(expr, f'Expected enum type `{self.name}`, got `{type_name}`')
    try:
        return self._parse_impl(member_name, native_types=None)
    except ValueError as error:
        raise model.ProgramError(expr, str(error)) from None


def _parse_opaque(self: types.TypeStruct, expr: model.ast.Value, scope: model.Scope | None) -> Any:
    """Parse a string literal with the implementation of an opaque struct."""
    if isinstance(expr, model.StructLiteral):
        raise model.ProgramError(expr, f'Expected `{self}`, got struct literal')
    assert scope is not None and self.native_name is not None
    native_types = scope.coordinator.global_node_type.native_extension.native_types
    native = native_types.opaque_types.get(self.native_name)
    python_type = None if native is None else native.python_type
    if isinstance(expr, model.StringConstant) and python_type is not None and hasattr(python_type, 'parse'):
        try:
            return self.parse(expr.value, native_types=native_types)
        except ValueError:
            raise model.ProgramError(expr, f'Invalid `{self}` value {expr.value!r}') from None
    if isinstance(expr, model.StringConstant) and python_type is None:
        raise model.ProgramError(expr, f'`{self}` has no literals without a native implementation')
    raise model.ProgramError(expr, f'Invalid `{self}` value {expr!r}')


def _parse_struct(self: types.TypeStruct, value: model.ast.Value, *, scope: model.Scope | None = None) -> Any:
    if self.is_opaque:
        return _parse_opaque(self, value, scope)
    if not isinstance(value, model.StructLiteral):
        raise model.ProgramError(value, f'Expected struct literal `{self.name} {{ ... }}`')
    fields = model.ast.check_struct_literal(self, value, scope)
    return self.make_value({name: parse_value(self.fields[name], field.value, scope=scope)
                            for name, field in fields.items()})


_literal_parsers: dict[type, Callable[..., Any]] = {
    types.TypeOption: _parse_option,
    types.TypeList: _parse_list,
    types.TypeUnit: _parse_unit,
    types.TypeBoolean: _parse_boolean,
    types.TypeInt: _parse_int,
    types.TypeValueType: _parse_value_type,
    types.TypeString: _parse_string,
    types.TypeEnum: _parse_enum,
    types.TypeStruct: _parse_struct,
}


def parse_value(value_type: Type, value: model.ast.Value, *, scope: model.Scope | None = None) -> Any:
    if isinstance(value_type, types.TypeResource):
        raise model.ProgramError(value, 'Resources cannot be compile-time values')
    parser = _literal_parsers[type(value_type)]
    return value_type.verify_value(parser(value_type, value, scope=scope))


def type_parameters(value_type: Type) -> Iterator[model.NodeParameter]:
    """Yield the parameters a parameter-dependent type refers to."""
    if isinstance(value_type, TypeParameter):
        yield value_type.parameter
    elif isinstance(value_type, (TypeOption, TypeList)):
        yield from type_parameters(value_type.inner_type)
