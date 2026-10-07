import abc
import re

from collections.abc import Mapping
from types import MappingProxyType

from .struct import StructValue
from .option import Some
from .unit import Unit
from .time import (
    MonotonicTime as RuntimeMonotonicTime, Duration as RuntimeDuration, TimeOfDay as RuntimeTimeOfDay,
)


# A value type supporting runtime validation, equality, and text parsing.
class Type(abc.ABC):
    has_runtime_representation = False
    is_ordered = False  # Whether less() defines a total order on values.

    @property
    def nesting_depth(self):
        """Maximum composite nesting, with primitives at depth zero."""
        if self.contains_parameters:
            raise ValueError('Type parameters require resolution before measuring nesting depth')
        return 0

    @property
    def total_member_count(self):
        """Count this type and every expanded member occurrence."""
        if self.contains_parameters:
            raise ValueError('Type parameters require resolution before counting members')
        return 1

    def _runtime_limit_violation(self):
        from ..program.limits import MAX_NESTING_DEPTH, MAX_STORAGE_SIZE, MAX_TOTAL_MEMBER_COUNT
        from .serialization import _layout

        depth = self.nesting_depth
        if depth > MAX_NESTING_DEPTH:
            return 'nesting depth', depth, MAX_NESTING_DEPTH
        count = self.total_member_count
        if count > MAX_TOTAL_MEMBER_COUNT:
            return 'total member count', count, MAX_TOTAL_MEMBER_COUNT
        size = _layout(self)[2]
        if size > MAX_STORAGE_SIZE:
            return 'storage size', size, MAX_STORAGE_SIZE
        return None

    @property
    def is_runtime_type(self):
        """Whether values have a representation within the runtime limits."""
        return self.has_runtime_representation and self._runtime_limit_violation() is None

    def _runtime_representation_error(self):
        if self.contains_parameters:
            return 'type parameters require resolution'
        return f'{self} is parameter-only'

    def runtime_type_error(self):
        """Return the reason for runtime ineligibility, or None."""
        if not self.has_runtime_representation:
            return self._runtime_representation_error()
        violation = self._runtime_limit_violation()
        if violation is None:
            return None
        metric, actual, maximum = violation
        if metric == 'storage size':
            return f'{metric} {actual} bytes exceeds {maximum} bytes'
        return f'{metric} {actual} exceeds {maximum}'

    @property
    def alignment(self):
        """Required byte alignment of this runtime type."""
        from .serialization import _alignment
        return _alignment(self)

    @property
    def data_size(self):
        """Encoded byte length, excluding reusable tail padding."""
        from .serialization import _data_size
        return _data_size(self)

    @property
    def size(self):
        """Aligned storage size in bytes, including zero tail padding."""
        alignment = self.alignment
        return (self.data_size + alignment - 1) & ~(alignment - 1)

    def serialize(self, value):
        """Return exactly data_size little-endian bytes for value."""
        from .serialization import _serialize
        return _serialize(self, value)

    def deserialize(self, data):
        """Decode exactly data_size bytes, validating flags and absent payloads."""
        from .serialization import _deserialize
        return _deserialize(self, data)

    @property
    @abc.abstractmethod
    def contains_parameters(self):
        ...

    @abc.abstractmethod
    def default_value(self):
        """Return a valid canonical value of this resolved type."""
        ...

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


# A concrete type which is implemented using a specific python type
class TypeConcrete(Type):
    def __init__(self, *, name, python_type, is_runtime_type, is_json_serializable, has_to_csv_value):
        self.name = name
        self.python_type = python_type

        self.has_runtime_representation = is_runtime_type

        self.is_json_serializable = is_json_serializable

        self.has_to_csv_value = has_to_csv_value

    @property
    def contains_parameters(self):
        return False

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

    def less(self, value1, value2):
        if not self.is_ordered:
            raise ValueError(f'{self} values are not ordered')
        return self.verify_value(value1) < self.verify_value(value2)


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
    def has_runtime_representation(self):
        return self.inner_type.has_runtime_representation

    @property
    def nesting_depth(self):
        return 1 + self.inner_type.nesting_depth

    @property
    def total_member_count(self):
        return 1 + self.inner_type.total_member_count

    def _runtime_representation_error(self):
        return f'inner type: {self.inner_type._runtime_representation_error()}'

    @property
    def has_to_csv_value(self):
        return self.inner_type.has_to_csv_value

    def to_csv_value(self, value):
        if value is None:
            return '', False
        return self.inner_type.to_csv_value(value.value)

    def __repr__(self):
        return f'TypeOption({self.inner_type})'

    def verify_value(self, value):
        if value is None:
            return None
        elif type(value) is Some:
            return Some(self.inner_type.verify_value(value.value))
        else:
            raise Exception(f'Expected None or Some, got {value!r}')

    def equals(self, value1, value2):
        if value1 is None and value2 is None:
            return True
        elif value1 is None or value2 is None:
            return False
        elif type(value1) is Some and type(value2) is Some:
            return self.inner_type.equals(value1.value, value2.value)
        else:
            raise Exception(f'Got unexpected parameter for equals: {value1!r}, {value2!r}')

    def default_value(self):
        if self.contains_parameters:
            raise ValueError('Type parameters require resolution before producing a default value')
        return None

    def _parse_impl(self, s):
        if s == 'none':
            return None
        # TODO: Use different format for strings?
        return Some(self.inner_type.parse(s))


class TypeStruct(Type):
    """A nominal value type with fields kept in declaration order."""

    is_json_serializable = False
    has_to_csv_value = False

    def __init__(self, name, fields):
        self.name = name
        self.fields = MappingProxyType(dict(fields))
        if any(not isinstance(field_type, Type) or field_type.contains_parameters
               for field_type in self.fields.values()):
            raise ValueError('Struct fields require resolved value types')

    @property
    def contains_parameters(self):
        return False

    @property
    def has_runtime_representation(self):
        return all(field_type.has_runtime_representation for field_type in self.fields.values())

    @property
    def nesting_depth(self):
        return 1 + max((field.nesting_depth for field in self.fields.values()), default=0)

    @property
    def total_member_count(self):
        return 1 + sum(field.total_member_count for field in self.fields.values())

    def _runtime_representation_error(self):
        for name, field in self.fields.items():
            if not field.has_runtime_representation:
                return f'field {name}: {field._runtime_representation_error()}'

    def __str__(self):
        return self.name

    def __repr__(self):
        return f'TypeStruct({self.name})'

    def make_value(self, fields):
        if not isinstance(fields, Mapping) or fields.keys() != self.fields.keys():
            raise ValueError(f'Expected exactly the fields of {self.name}: {", ".join(self.fields)}')
        return StructValue(self, {name: field_type.verify_value(fields[name])
                                  for name, field_type in self.fields.items()})

    def verify_value(self, value):
        if not isinstance(value, StructValue) or value._type is not self:
            raise ValueError(f'Expected struct value of type {self.name}, got {value!r}')
        return self.make_value(value)

    def equals(self, value1, value2):
        value1 = self.verify_value(value1)
        value2 = self.verify_value(value2)
        return all(field_type.equals(value1[name], value2[name])
                   for name, field_type in self.fields.items())

    def default_value(self):
        return self.make_value({name: field_type.default_value() for name, field_type in self.fields.items()})

    def _parse_impl(self, text):
        raise ValueError(f'{self.name} does not support text parsing')


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
    def nesting_depth(self):
        return 1 + self.inner_type.nesting_depth

    @property
    def total_member_count(self):
        return 1 + self.inner_type.total_member_count

    @property
    def has_to_csv_value(self):
        return False

    def __repr__(self):
        return f'TypeList({self.inner_type})'

    def verify_value(self, value):
        if type(value) in (list, tuple):
            return tuple(self.inner_type.verify_value(v) for v in value)
        else:
            raise Exception(f'Expected list or tuple, got {value!r}')

    def equals(self, value1, value2):
        if type(value1) not in (list, tuple) or type(value2) not in (list, tuple):
            raise Exception(f'Got unexpected parameter for equals: {value1!r}, {value2!r}')
        if len(value1) != len(value2):
            return False
        for v1, v2 in zip(value1, value2):
            if not self.inner_type.equals(v1, v2):
                return False
        return True

    def default_value(self):
        if self.contains_parameters:
            raise ValueError('Type parameters require resolution before producing a default value')
        return ()

    def _parse_impl(self, s):
        raise Exception('TODO: Implement TypeList.parse')


# TODO: Add to_csv_value?
class TypeUnit(TypeConcrete):
    def __init__(self):
        super().__init__(name='unit', python_type=Unit, is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    def default_value(self):
        return Unit()

    def _parse_impl(self, s):
        if s != '()':
            raise Exception(f'Invalid unit value {s!r}')
        return Unit()


class TypeBoolean(TypeConcrete):
    def __init__(self):
        super().__init__(name='boolean', python_type=bool, is_runtime_type=True, is_json_serializable=True, has_to_csv_value=True)

    def default_value(self):
        return False

    def _parse_impl(self, s):
        if s == '0' or s.lower() == 'false':
            return False
        elif s == '1' or s.lower() == 'true':
            return True
        else:
            raise Exception(f'Invalid boolean value {s!r}')

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
    is_ordered = True

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

    def default_value(self):
        return 0

    def arithmetic(self, operation, overflow, left, right):
        """Apply 'add', 'sub', 'mul', 'div' or 'rem' with 'wrapping', 'saturating' or 'checked' overflow.

        Division truncates toward zero. Checked results are Some(value), or None on overflow or a
        zero divisor."""
        left, right = self.verify_value(left), self.verify_value(right)
        if operation in ('div', 'rem'):
            if overflow != 'checked':
                raise ValueError(f'Division requires checked overflow, got {overflow!r}')
            if right == 0:
                return None
            quotient = abs(left) // abs(right)
            if (left < 0) != (right < 0):
                quotient = -quotient
            result = quotient if operation == 'div' else left - right * quotient
        elif operation == 'add':
            result = left + right
        elif operation == 'sub':
            result = left - right
        elif operation == 'mul':
            result = left * right
        else:
            raise ValueError(f'Unknown integer operation {operation!r}')
        return _fit_integer(result, self.min_value, self.max_value, overflow)

    def _parse_impl(self, s):
        i = int(s)
        if i < self.min_value or i > self.max_value:
            raise Exception(f'Int value out of range: {i!r}')
        return i

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

    def verify_value(self, value):
        if not isinstance(value, Type):
            raise ValueError(f'Expected a value type, got {value!r}')
        return value

    def default_value(self):
        return unit

    def _parse_impl(self, s):
        raise Exception('TODO: TypeValueType.parse()')


class TypeMonotonicTime(TypeConcrete):
    def __init__(self):
        super().__init__(name='MonotonicTime', python_type=RuntimeMonotonicTime, is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    def default_value(self):
        # Generation 0 is never current, so the default compares to no current time.
        return RuntimeMonotonicTime(0, 0)

    def _parse_impl(self, s):
        raise Exception('TODO: Implement TypeMonotonicTime.parse')


# TODO: Add type which represents offset to monotonic time and can be used e.g. for KNX messages which contain the current time?


# TODO: Add to_csv_value and make json serializable?
class TypeDuration(TypeConcrete):
    is_ordered = True

    def __init__(self):
        super().__init__(name='Duration', python_type=RuntimeDuration, is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    def default_value(self):
        return RuntimeDuration(duration_ns=0)

    def _parse_impl(self, s):
        return RuntimeDuration.parse(s)


# TODO: Add to_csv_value?
class TypeTimeOfDay(TypeConcrete):
    is_ordered = True

    def __init__(self):
        super().__init__(name='TimeOfDay', python_type=RuntimeTimeOfDay, is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    def default_value(self):
        return RuntimeTimeOfDay(0)

    def _parse_impl(self, s):
        return RuntimeTimeOfDay.parse(s)


# TODO: Remove json_encode because strings should not be used at runtime?
# TODO: Add to_csv_value? Probably not because it should not be used at runtime
class TypeString(TypeConcrete):
    def __init__(self):
        super().__init__(name='String', python_type=str, is_runtime_type=False, is_json_serializable=True, has_to_csv_value=False)

    def default_value(self):
        return ''

    def _parse_impl(self, s):
        raise Exception('TypeString.parse()')

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
    """A named Python enumeration encoded with an explicit integer type."""

    def __init__(self, name, python_type, *, underlying_type):
        """Define the enum and check that its member numbers fit the integer type."""
        if not isinstance(underlying_type, TypeInt):
            raise ValueError('Enum underlying_type must be an integer type')
        if not python_type.__members__:
            raise ValueError('Enum must declare at least one member')
        for member in python_type.__members__.values():
            if type(member.value) is not int or not underlying_type.min_value <= member.value <= underlying_type.max_value:
                raise ValueError(f'Enum member {member.name} does not fit {underlying_type}')
        super().__init__(name=name, python_type=python_type, is_runtime_type=True, is_json_serializable=True, has_to_csv_value=False)
        self.underlying_type = underlying_type  # Wire width and signedness of member numbers.

    def default_value(self):
        return next(iter(self.python_type.__members__.values()))

    def _parse_impl(self, s):
        try:
            return self.python_type[s]
        except KeyError:
            raise ValueError(f'Unknown member {s!r} of {self.name}') from None

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


def _fit_integer(value, min_value, max_value, overflow):
    """Map an exact integer into min_value..max_value using an overflow mode."""
    if overflow == 'wrapping':
        return (value - min_value) % (max_value - min_value + 1) + min_value
    if overflow == 'saturating':
        return min(max(value, min_value), max_value)
    if overflow == 'checked':
        return Some(value) if min_value <= value <= max_value else None
    raise ValueError(f'Unknown overflow mode {overflow!r}')


def integer_range(value_type):
    """Return the (min, max) numbers of an integer, enum, or boolean type."""
    if isinstance(value_type, TypeInt):
        return value_type.min_value, value_type.max_value
    if isinstance(value_type, TypeEnum):
        return integer_range(value_type.underlying_type)
    if isinstance(value_type, TypeBoolean):
        return 0, 1
    raise ValueError(f'{value_type} is not an integer, enum, or boolean type')


def convert(source_type, target_type, overflow, value):
    """Convert an integer, enum, or boolean value to an integer or boolean type.

    Booleans are 1-bit unsigned integers. Checked results are Some(value) or None."""
    if isinstance(source_type, TypeEnum):
        number = source_type.verify_value(value).value
    else:
        integer_range(source_type)
        number = int(source_type.verify_value(value))
    if not isinstance(target_type, (TypeInt, TypeBoolean)):
        raise ValueError(f'Cannot convert to {target_type}')
    result = _fit_integer(number, *integer_range(target_type), overflow)
    if isinstance(target_type, TypeBoolean):
        if overflow == 'checked':
            return None if result is None else Some(bool(result.value))
        return bool(result)
    return result


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
]:
    type_by_name[ty.name] = ty
