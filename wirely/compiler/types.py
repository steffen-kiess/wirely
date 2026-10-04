"""Compiler types, source literals, and parameter resolution."""

from ..types import Type, TypeOption, TypeList
from .. import types
from . import model as blocks


class ResourceType:
    """Nominal connector type identifying an opaque service resource."""

    def __init__(self, name, declaration):
        self.name = name
        self.declaration = declaration

    def __str__(self):
        return self.name

    def __repr__(self):
        return f'ResourceType({self.name})'


class TypeParameter(Type):
    def __init__(self, parameter):
        if not isinstance(parameter, blocks.DeclarationParameter):
            raise Exception("not isinstance(parameter, blocks.DeclarationParameter)")

        self.parameter = parameter

    @property
    def contains_parameters(self):
        return True

    def resolve_self(self, par_values):
        from .parameters import ParameterValues, resolve_parameter

        if not isinstance(par_values, ParameterValues) and self.parameter in par_values:
            value = par_values[self.parameter]
            return self if isinstance(value, blocks.Value) else value
        if (not isinstance(par_values, ParameterValues)
                and isinstance(self.parameter, blocks.DeclarationConstant)):
            return self
        if not isinstance(par_values, ParameterValues) and self.parameter.default_value is not None:
            from .expressions import Expression
            if isinstance(self.parameter.default_value, (blocks.FunctionCall, Expression)):
                return self
            return self.parameter.interface.resolve_type(self.parameter.default_value, par_values)
        if self.parameter in par_values or self.parameter.default_value is not None:
            return resolve_parameter(self.parameter, par_values)
        else:
            # TODO
            hints = []
            for par in par_values:
                if not isinstance(par, blocks.DeclarationParameter):
                    raise Exception("not isinstance(par, blocks.DeclarationParameter)")
                hints += [
                    blocks.CompilerHint(par, f'Available parameter: {par.interface}.{par.name}'),
                ]
            raise blocks.ProgramError(self.parameter, f'Unable to resolve type parameter {self.parameter.interface}.{self.parameter.name}', hints=hints)

    def __repr__(self):
        # return f'TypeParameter({self.parameter})'
        return f'TypeParameter({self.parameter.interface.name}.{self.parameter.name})'

    def __eq__(self, other):
        if not isinstance(other, TypeParameter):
            return False
        return self.parameter == other.parameter

    def __hash__(self):
        return hash(('TypeParameter', self.parameter))

    def verify_value(self, value):
        raise Exception('Not a concrete type')

    def equals(self, value1, value2):
        raise Exception('Not a concrete type')

    def _parse_impl(self, s):
        raise Exception('Not a concrete type')


def check_runtime_type(value_type, source):
    """Check runtime eligibility once any type parameters have resolved."""
    if not value_type.contains_parameters and not value_type.is_runtime_type:
        raise blocks.ProgramError(source, f'Type {value_type} is parameter-only')


def resolve_type(value_type, par_values):
    if not value_type.contains_parameters:
        return value_type
    if isinstance(value_type, TypeParameter):
        result = value_type.resolve_self(par_values)
        if not isinstance(result, Type):
            raise blocks.ProgramError(value_type.parameter,
                                      f'Expected ValueType for {value_type.parameter.name.id_str!r}')
        return result
    if isinstance(value_type, TypeOption):
        return TypeOption(resolve_type(value_type.inner_type, par_values))
    if isinstance(value_type, TypeList):
        return TypeList(resolve_type(value_type.inner_type, par_values))
    raise TypeError(f'Unsupported parameter-dependent type {value_type!r}')


def _parse_option(self, value, *, scope=None):
    if isinstance(value, blocks.Reference) and value.length == 1 and value.identifiers[0].id_str == 'None':
        return None
    if (isinstance(value, blocks.FunctionCall)
            and isinstance(value.fun, blocks.Reference)
            and value.fun.length == 1
            and value.fun.identifiers[0].id_str == 'Some'):
        if len(value.parameters) != 1:
            raise blocks.ProgramError(value, 'Some expects exactly one argument')
        return blocks.Some(parse_value(self.inner_type, value.parameters[0], scope=scope))
    raise blocks.ProgramError(value, f'Invalid Option value {value!r}')


def _parse_list(self, value, *, scope=None):
    if not isinstance(value, blocks.ListLiteral):
        raise blocks.ProgramError(value, f'Invalid List value {value!r}')
    return tuple(parse_value(self.inner_type, v, scope=scope) for v in value.values)


def _parse_unit(self, value, *, scope=None):
    if isinstance(value, blocks.UnitLiteral):
        return blocks.Unit()
    raise blocks.ProgramError(value, f'Invalid unit value {value!r}')


def _parse_boolean(self, value, *, scope=None):
    if isinstance(value, blocks.Reference) and value.length == 1:
        s = value.identifiers[0].id_str
        if s == 'false':
            return False
        elif s == 'true':
            return True
    raise blocks.ProgramError(value, f'Invalid boolean value {value!r}')


def _parse_int(self, value, *, scope=None):
    if isinstance(value, blocks.IntNumber):
        i = value.value
        if i < self.min_value or i > self.max_value:
            raise blocks.ProgramError(value, f'Int value out of range: {i!r}')
        return i
    raise blocks.ProgramError(value, f'Invalid int value {value!r}')


def _parse_value_type(self, value, *, scope=None):
    raise Exception('TODO: TypeValueType.parse_value()')


def _parse_monotonic_time(self, value, *, scope=None):
    if isinstance(value, blocks.Reference) and value.length == 2 and value.identifiers[0].id_str == 'MonotonicTime' and value.identifiers[1].id_str == 'Invalid':
        return blocks.MonotonicTime.create_invalid()
    raise blocks.ProgramError(value, f'Invalid MonotonicTime value {value!r}')


def _parse_duration(self, expr, *, scope=None):
    # TODO
    if isinstance(expr, blocks.StringConstant):
        s = expr.value
        try:
            return self.parse(s)
        except Exception:
            raise blocks.ProgramError(expr, f'Invalid Duration value {s!r}')
    raise blocks.ProgramError(expr, f'Invalid Duration value {expr!r}')


def _parse_time_of_day(self, expr, *, scope=None):
    # TODO
    if isinstance(expr, blocks.StringConstant):
        s = expr.value
        try:
            return self.parse(s)
        except Exception:
            raise blocks.ProgramError(expr, f'Invalid TimeOfDay value {s!r}')
    raise blocks.ProgramError(expr, f'Invalid TimeOfDay value {expr!r}')


def _parse_string(self, value, *, scope=None):
    if isinstance(value, blocks.StringConstant):
        return value.value
    raise blocks.ProgramError(value, f'Invalid string value {value!r}')


def _parse_enum(self, expr, *, scope=None):
    if not isinstance(expr, blocks.Reference) or expr.length < 2:
        raise blocks.ProgramError(expr, f'Expected enum literal {self.name}.Member')
    type_name = '.'.join(identifier.id_str for identifier in expr.identifiers[:-1])
    member_name = expr.identifiers[-1].id_str
    if scope is not None:
        type_reference = blocks.Reference.from_list(
            *expr.identifiers[:-1], source_position=expr.source_position,
        )
        enum_type = scope.resolve_type(type_reference)
        matches = enum_type is self
    else:
        matches = type_name == self.name
    if not matches:
        raise blocks.ProgramError(expr, f'Expected enum type {self.name}, got {type_name}')
    try:
        return self._parse_impl(member_name)
    except ValueError as error:
        raise blocks.ProgramError(expr, str(error)) from None


def _parse_struct(self, value, *, scope=None):
    if not isinstance(value, blocks.StructLiteral):
        raise blocks.ProgramError(value, f'Expected struct literal {self.name} {{ ... }}')
    fields = blocks.ast.check_struct_literal(self, value, scope)
    return self.make_value({name: parse_value(self.fields[name], field.value, scope=scope)
                            for name, field in fields.items()})


_literal_parsers = {
    types.TypeOption: _parse_option,
    types.TypeList: _parse_list,
    types.TypeUnit: _parse_unit,
    types.TypeBoolean: _parse_boolean,
    types.TypeInt: _parse_int,
    types.TypeValueType: _parse_value_type,
    types.TypeMonotonicTime: _parse_monotonic_time,
    types.TypeDuration: _parse_duration,
    types.TypeTimeOfDay: _parse_time_of_day,
    types.TypeString: _parse_string,
    types.TypeEnum: _parse_enum,
    types.TypeStruct: _parse_struct,
}


def parse_value(value_type, value, *, scope=None):
    parser = _literal_parsers[type(value_type)]
    return value_type.verify_value(parser(value_type, value, scope=scope))
