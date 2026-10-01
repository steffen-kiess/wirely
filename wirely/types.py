import abc
import datetime
import re

import wirely as blocks
from wirely.util import check_parameters


class ResourceType:
    """Nominal connector type identifying an opaque service resource."""

    def __init__(self, name, declaration):
        self.name = name
        self.declaration = declaration

    def __str__(self):
        return self.name

    def __repr__(self):
        return f'ResourceType({self.name})'


# A type which can be either a concrete type or a type parameter / a type depending in some way on a type parameter
class Type(abc.ABC):
    @property
    @abc.abstractmethod
    def contains_parameters(self):
        ...

    # TODO: Pass block instead of par_values?
    @abc.abstractmethod
    def resolve_self(self, par_values):
        ...

    # Only available for concrete types (will throw for parameters and generally throw for types depending on type parameters)

    @abc.abstractmethod
    def verify_value(self, value):
        ...

    @abc.abstractmethod
    def equals(self, value1, value2):
        ...

    def parse(self, s):
        return self.verify_value(self._parse_impl(s))

    @abc.abstractmethod
    def _parse_impl(self, s):
        ...

    def parse_value(self, value):
        return self.verify_value(self._parse_value_impl(value))

    @abc.abstractmethod
    def _parse_value_impl(self, value):
        ...


# A concrete type which is implemented using a specific python type
class TypeConcrete(Type):
    def __init__(self, *, name, python_type, is_runtime_type, is_json_serializable, has_to_csv_value):
        self.name = name
        self.python_type = python_type

        # TODO: Actually enforce is_runtime_type (i.e. disallow strings at runtime)
        self.is_runtime_type = is_runtime_type

        self.is_json_serializable = is_json_serializable

        self.has_to_csv_value = has_to_csv_value

    @property
    def contains_parameters(self):
        return False

    def resolve_self(self, par_values):
        return self

    def __repr__(self):
        return f'TypeConcrete({self.name})'

    def __str__(self):
        return self.name

    def verify_value(self, value):
        if not isinstance(value, self.python_type):
            raise Exception(f'Expected {self.python_type} for {self}, got {type(value)} value {value!r}')
        if type(value) == self.python_type:
            return value
        return self.python_type(value)

    def equals(self, value1, value2):
        return self.verify_value(value1) == self.verify_value(value2)


class TypeParameter(Type):
    def __init__(self, parameter):
        if not isinstance(parameter, blocks.DeclarationParameter):
            raise Exception("not isinstance(parameter, blocks.DeclarationParameter)")

        self.parameter = parameter

    @property
    def contains_parameters(self):
        return True

    def resolve_self(self, par_values):
        if self.parameter in par_values:
            return par_values[self.parameter]
        elif self.parameter.default_value is not None:
            scope = self.parameter.interface
            return scope.resolve_type(self.parameter.default_value, par_values)
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

    def _parse_value_impl(self, value):
        raise Exception('Not a concrete type')


class TypeOption(Type):
    def __init__(self, inner_type):
        self.inner_type = inner_type

    def __eq__(self, other):
        if not isinstance(other, TypeOption):
            return False
        return self.inner_type == other.inner_type

    def __hash__(self):
        return hash(('Option', self.inner_type))

    @property
    def contains_parameters(self):
        return self.inner_type.contains_parameters

    @property
    def has_to_csv_value(self):
        return self.inner_type.has_to_csv_value

    def to_csv_value(self, value):
        if value is None:
            return '', False
        return self.inner_type.to_csv_value(value.value)

    def resolve_self(self, par_values):
        if not self.contains_parameters:
            return self
        return TypeOption(self.inner_type.resolve_self(par_values))

    def __repr__(self):
        return f'TypeOption({self.inner_type})'

    def verify_value(self, value):
        if value is None:
            return None
        elif type(value) is blocks.Some:
            return blocks.Some(self.inner_type.verify_value(value.value))
        else:
            raise Exception(f'Expected None or Some, got {value!r}')

    def equals(self, value1, value2):
        if value1 is None and value2 is None:
            return True
        elif value1 is None or value2 is None:
            return False
        elif type(value1) is blocks.Some and type(value2) is blocks.Some:
            return self.inner_type.equals(value1.value, value2.value)
        else:
            raise Exception(f'Got unexpected parameter for equals: {value1!r}, {value2!r}')

    def _parse_impl(self, s):
        if s == 'none':
            return None
        # TODO: Use different format for strings?
        return blocks.Some(self.inner_type.parse(s))

    def _parse_value_impl(self, value):
        if isinstance(value, blocks.Reference) and value.length == 1 and value.identifiers[0].id_str == 'None':
            return None
        if (isinstance(value, blocks.FunctionCall)
                and isinstance(value.fun, blocks.Reference)
                and value.fun.length == 1
                and value.fun.identifiers[0].id_str == 'Some'):
            if len(value.parameters) != 1:
                raise blocks.ProgramError(value, 'Some expects exactly one argument')
            return blocks.Some(self.inner_type.parse_value(value.parameters[0]))
        raise blocks.ProgramError(value, f'Invalid Option value {value!r}')


class TypeList(Type):
    def __init__(self, inner_type):
        self.inner_type = inner_type

    def __eq__(self, other):
        if not isinstance(other, TypeList):
            return False
        return self.inner_type == other.inner_type

    def __hash__(self):
        return hash(('List', self.inner_type))

    @property
    def contains_parameters(self):
        return self.inner_type.contains_parameters

    @property
    def has_to_csv_value(self):
        return False

    def resolve_self(self, par_values):
        if not self.contains_parameters:
            return self
        return TypeList(self.inner_type.resolve_self(par_values))

    def __repr__(self):
        return f'TypeList({self.inner_type})'

    def verify_value(self, value):
        if type(value) == list:
            return [self.inner_type.verify_value(v) for v in value]
        else:
            raise Exception(f'Expected list, got {value!r}')

    def equals(self, value1, value2):
        if type(value1) != list or type(value2) != list:
            raise Exception(f'Got unexpected parameter for equals: {value1!r}, {value2!r}')
        if len(value1) != len(value2):
            return False
        for v1, v2 in zip(value1, value2):
            if not self.inner_type.equals(v1, v2):
                return False
        return True

    def _parse_impl(self, s):
        raise Exception('TODO: Implement TypeList.parse')

    def _parse_value_impl(self, value):
        if not isinstance(value, blocks.ListLiteral):
            raise blocks.ProgramError(value, f'Invalid List value {value!r}')
        return [self.inner_type.parse_value(v) for v in value.values]


# TODO: Add to_csv_value?
class TypeUnit(TypeConcrete):
    def __init__(self):
        super().__init__(name='unit', python_type=type(None), is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    def _parse_impl(self, s):
        if s != '()':
            raise Exception(f'Invalid unit value {s!r}')
        return None

    def _parse_value_impl(self, value):
        if isinstance(value, blocks.UnitLiteral):
            return None
        raise blocks.ProgramError(value, f'Invalid unit value {value!r}')


class TypeBoolean(TypeConcrete):
    def __init__(self):
        super().__init__(name='boolean', python_type=bool, is_runtime_type=True, is_json_serializable=True, has_to_csv_value=True)

    def _parse_impl(self, s):
        if s == '0' or s.lower() == 'false':
            return False
        elif s == '1' or s.lower() == 'true':
            return True
        else:
            raise Exception(f'Invalid boolean value {s!r}')

    def _parse_value_impl(self, value):
        if isinstance(value, blocks.Reference) and value.length == 1:
            s = value.identifiers[0].id_str
            if s == 'false':
                return False
            elif s == 'true':
                return True
        raise blocks.ProgramError(value, f'Invalid boolean value {value!r}')

    def json_encode(self, expr, format):
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return expr

    # TODO: Add abstract method somewhere? (Same for json_decode and to_csv_value) Determine has_to_csv_value automatically?
    def json_decode(self, expr, format):
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return self.verify_value(expr)

    def to_csv_value(self, value):
        if value == False:
            return 'false', False
        elif value == True:
            return 'true', False
        else:
            raise Exception('Invalid value in TypeBoolean.to_csv_value')


# TODO: Work on value verification etc.
class TypeInt(TypeConcrete):
    def __init__(self, *, is_signed, bits):
        name = f'int{bits}'
        if not is_signed:
            name = 'u' + name
        super().__init__(name=name, python_type=int, is_runtime_type=True, is_json_serializable=True, has_to_csv_value=True)

        self.is_signed = is_signed
        self.bits = bits

        if self.is_signed:
            self.min_value = -2**(self.bits - 1)
            self.max_value = 2**(self.bits - 1) - 1
        else:
            self.min_value = 0
            self.max_value = 2**self.bits - 1

    def _parse_impl(self, s):
        i = int(s)
        if i < self.min_value or i > self.max_value:
            raise Exception(f'Int value out of range: {i!r}')
        return i

    def _parse_value_impl(self, value):
        if isinstance(value, blocks.IntNumber):
            i = value.value
            if i < self.min_value or i > self.max_value:
                raise blocks.ProgramError(value, f'Int value out of range: {i!r}')
            return i
        raise blocks.ProgramError(value, f'Invalid int value {value!r}')

    def json_encode(self, expr, format):
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return expr

    def json_decode(self, expr, format):
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return self.verify_value(expr)

    def to_csv_value(self, value):
        return str(value), False


class TypeValueType(TypeConcrete):
    def __init__(self):
        super().__init__(name='ValueType', python_type=Type, is_runtime_type=False, is_json_serializable=False, has_to_csv_value=False)

    def _parse_impl(self, s):
        raise Exception('TODO: TypeValueType.parse()')

    def _parse_value_impl(self, value):
        raise Exception('TODO: TypeValueType.parse_value()')


class TypeMonotonicTime(TypeConcrete):
    def __init__(self):
        super().__init__(name='MonotonicTime', python_type=blocks.MonotonicTime, is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    def _parse_impl(self, s):
        raise Exception('TODO: Implement TypeMonotonicTime.parse')

    def _parse_value_impl(self, value):
        if isinstance(value, blocks.Reference) and value.length == 2 and value.identifiers[0].id_str == 'MonotonicTime' and value.identifiers[1].id_str == 'Invalid':
            return blocks.MonotonicTime.create_invalid()
        raise blocks.ProgramError(value, f'Invalid MonotonicTime value {value!r}')


# TODO: Add type which represents offset to monotonic time and can be used e.g. for KNX messages which contain the current time?


# TODO: Add to_csv_value and make json serializable?
class TypeDuration(TypeConcrete):
    def __init__(self):
        super().__init__(name='Duration', python_type=blocks.Duration, is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    def _parse_impl(self, s):
        return blocks.Duration.parse(s)

    def _parse_value_impl(self, expr):
        # TODO
        if isinstance(expr, blocks.StringConstant):
            s = expr.value
            try:
                return self.parse(s)
            except Exception:
                raise blocks.ProgramError(expr, f'Invalid Duration value {s!r}')
        raise blocks.ProgramError(expr, f'Invalid Duration value {expr!r}')


# TODO: Add to_csv_value?
class TypeTimeOfDay(TypeConcrete):
    def __init__(self):
        super().__init__(name='TimeOfDay', python_type=datetime.time, is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    # TODO: Look at ISO 8601 for this?
    def _parse_impl(self, s):
        # TODO: Allow "24:00"?
        time = datetime.time.fromisoformat(s)
        if time.tzinfo is not None:
            raise Exception('Got time with timezone info')
        return time

    def _parse_value_impl(self, expr):
        # TODO
        if isinstance(expr, blocks.StringConstant):
            s = expr.value
            try:
                return self.parse(s)
            except Exception:
                raise blocks.ProgramError(expr, f'Invalid TimeOfDay value {s!r}')
        raise blocks.ProgramError(expr, f'Invalid TimeOfDay value {expr!r}')


# TODO: Remove json_encode because strings should not be used at runtime?
# TODO: Add to_csv_value? Probably not because it should not be used at runtime
class TypeString(TypeConcrete):
    def __init__(self):
        super().__init__(name='String', python_type=str, is_runtime_type=False, is_json_serializable=True, has_to_csv_value=False)

    def _parse_impl(self, s):
        raise Exception('TypeString.parse()')

    def _parse_value_impl(self, value):
        if isinstance(value, blocks.StringConstant):
            return value.value
        raise blocks.ProgramError(value, f'Invalid string value {value!r}')

    def json_encode(self, expr, format):
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return expr

    def json_decode(self, expr, format):
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return self.verify_value(expr)


# TODO: Add to_csv_value?
class TypeEnum(TypeConcrete):
    def __init__(self, name, python_type):
        super().__init__(name=name, python_type=python_type, is_runtime_type=True, is_json_serializable=True, has_to_csv_value=False)

    def _parse_impl(self, s):
        try:
            return self.python_type[s]
        except KeyError:
            raise ValueError(f'Unknown member {s!r} of {self.name}') from None

    def _parse_value_impl(self, expr):
        if not isinstance(expr, blocks.Reference) or expr.length != 2:
            raise blocks.ProgramError(expr, f'Expected enum literal {self.name}.Member')
        type_name, member_name = [identifier.id_str for identifier in expr.identifiers]
        if type_name != self.name:
            raise blocks.ProgramError(expr, f'Expected enum type {self.name}, got {type_name}')
        try:
            return self._parse_impl(member_name)
        except ValueError as error:
            raise blocks.ProgramError(expr, str(error)) from None

    def json_encode(self, expr, format):
        if format == 'SnakeCase':
            s = expr.name
            # https://stackoverflow.com/questions/1175208/elegant-python-function-to-convert-camelcase-to-snake-case/1176023#1176023
            s = re.sub(r'(?<!^)(?=[A-Z])', '_', s).lower()
            return s
        elif format is None:
            return expr.name
        else:
            raise Exception(f'Invalid JSON format: {format!r}')

    def json_decode(self, expr, format):
        if format == 'SnakeCase':
            raise Exception('TODO: Decoding SnakeCase not implemented')
        elif format is None:
            if not isinstance(expr, str):
                raise Exception(f'Expected string for enum, got {expr!r}')
            return self.python_type[expr]
        else:
            raise Exception(f'Invalid JSON format: {format!r}')


unit = TypeUnit()

boolean = TypeBoolean()

# Integer types
int8 = TypeInt(is_signed=True, bits=8)
int16 = TypeInt(is_signed=True, bits=16)
int32 = TypeInt(is_signed=True, bits=32)
int64 = TypeInt(is_signed=True, bits=64)
uint8 = TypeInt(is_signed=False, bits=8)
uint16 = TypeInt(is_signed=False, bits=16)
uint32 = TypeInt(is_signed=False, bits=32)
uint64 = TypeInt(is_signed=False, bits=64)

ValueType = TypeValueType()
TimeOfDay = TypeTimeOfDay()
Duration = TypeDuration()
MonotonicTime = TypeMonotonicTime()
String = TypeString()
ThermostatSystemMode = TypeEnum('ThermostatSystemModeEnum', blocks.enum.ThermostatSystemMode)

# def option(inner_type):
#     return TypeOption(inner_type)

# def list(inner_type):
#     return TypeList(inner_type)

type_by_name = {}
for ty in [
        boolean, unit,
        int8, int16, int32, int64,
        uint8, uint16, uint32, uint64,
        ValueType, Duration, TimeOfDay, MonotonicTime, String,
        ThermostatSystemMode
]:
    type_by_name[ty.name] = ty
