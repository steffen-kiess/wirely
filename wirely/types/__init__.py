from __future__ import annotations

import abc
import re

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NoReturn, TypeAlias

from .option import Some
from .unit import Unit

if TYPE_CHECKING:
    from ..runtime.extensions import NativeTypes

# How the compiler and runtime represent a value: integers and enums as int,
# structs, including opaque structs, as tuples of their fields, lists as
# tuples, and `type` values as their Type. Native code receives other
# representations; see value_types.
InternalValue: TypeAlias = (
    'bool | int | str | Unit | Type | Some[InternalValue] | tuple[InternalValue, ...] | None'
)

# Opaque structs whose values the compiler orders by their fields.
# TODO: Let other opaque types declare an order.
ORDERED_OPAQUE_TYPES = frozenset({'Std.Duration', 'Schedule.TimeOfDay'})


# A value type supporting runtime validation, equality, and text parsing.
class Type(abc.ABC):
    is_ordered = False  # Whether less() defines a total order on values.

    # Defined by types whose is_json_serializable or has_to_csv_value is true.
    json_encode: Callable[..., Any]
    json_decode: Callable[..., Any]
    to_csv_value: Callable[..., Any]

    @property
    def has_runtime_representation(self) -> bool:
        return False

    @property
    def nesting_depth(self) -> int:
        """Maximum composite nesting, with primitives at depth zero."""
        if self.contains_parameters:
            raise ValueError('Type parameters require resolution before measuring nesting depth')
        return 0

    @property
    def total_member_count(self) -> int:
        """Count this type and every expanded member occurrence."""
        if self.contains_parameters:
            raise ValueError('Type parameters require resolution before counting members')
        return 1

    def _runtime_limit_violation(self) -> tuple[str, int, int] | None:
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
    def is_runtime_type(self) -> bool:
        """Whether values have a representation within the runtime limits."""
        return self.has_runtime_representation and self._runtime_limit_violation() is None

    def _runtime_representation_error(self) -> str | None:
        if self.contains_parameters:
            return 'type parameters require resolution'
        return f'`{self}` is parameter-only'

    def runtime_type_error(self) -> str | None:
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
    def alignment(self) -> int:
        """Required byte alignment of this runtime type."""
        from .serialization import _alignment
        return _alignment(self)

    @property
    def data_size(self) -> int:
        """Encoded byte length, excluding reusable tail padding."""
        from .serialization import _data_size
        return _data_size(self)

    @property
    def size(self) -> int:
        """Aligned storage size in bytes, including zero tail padding."""
        alignment = self.alignment
        return (self.data_size + alignment - 1) & ~(alignment - 1)

    def serialize(self, value: Any) -> bytes:
        """Return exactly data_size little-endian bytes for value."""
        from .serialization import _serialize
        return _serialize(self, value)

    def deserialize(self, data: bytes | memoryview) -> Any:
        """Decode exactly data_size bytes, validating flags and absent payloads."""
        from .serialization import _deserialize
        return _deserialize(self, data)

    @property
    @abc.abstractmethod
    def contains_parameters(self) -> bool:
        ...

    @abc.abstractmethod
    def default_value(self) -> Any:
        """Return a valid canonical value of this resolved type."""
        ...

    @abc.abstractmethod
    def verify_value(self, value: Any) -> Any:
        ...

    @abc.abstractmethod
    def equals(self, value1: Any, value2: Any) -> bool:
        ...

    def parse(self, s: str) -> Any:
        return self.verify_value(self._parse_impl(s))

    @abc.abstractmethod
    def _parse_impl(self, s: str) -> Any:
        ...

    def to_text(self, value: Any) -> str:
        """Format value as text, using Wirely literal syntax where it exists."""
        raise ValueError(f'`{self}` values cannot be formatted as text')


# A parameterless primitive type, without fields or inner types
class TypePrimitive(Type):
    def __init__(self, *, name: str, python_type: Any, is_runtime_type: bool, is_json_serializable: bool,
                     has_to_csv_value: bool) -> None:
        self.name = name
        self.python_type = python_type

        self._has_runtime_representation = is_runtime_type

        self.is_json_serializable = is_json_serializable

        self.has_to_csv_value = has_to_csv_value

    @property
    def has_runtime_representation(self) -> bool:
        return self._has_runtime_representation

    @property
    def contains_parameters(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f'TypePrimitive({self.name})'

    def __str__(self) -> str:
        return self.name

    def verify_value(self, value: Any) -> Any:
        if not isinstance(value, self.python_type):
            raise Exception(f'Expected {self.python_type} for {self}, got {type(value)} value {value!r}')
        if type(value) == self.python_type:
            return value
        return self.python_type(value)

    def equals(self, value1: Any, value2: Any) -> bool:
        return self.verify_value(value1) == self.verify_value(value2)

    def less(self, value1: Any, value2: Any) -> bool:
        if not self.is_ordered:
            raise ValueError(f'`{self}` values are not ordered')
        return self.verify_value(value1) < self.verify_value(value2)


class TypeOption(Type):
    def __init__(self, inner_type: Type) -> None:
        self.inner_type = inner_type

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TypeOption):
            return False
        return self.inner_type == other.inner_type

    def __hash__(self) -> int:
        return hash(('Option', self.inner_type))

    @property
    def contains_parameters(self) -> bool:
        return self.inner_type.contains_parameters

    @property
    def has_runtime_representation(self) -> bool:
        return self.inner_type.has_runtime_representation

    @property
    def nesting_depth(self) -> int:
        return 1 + self.inner_type.nesting_depth

    @property
    def total_member_count(self) -> int:
        return 1 + self.inner_type.total_member_count

    def _runtime_representation_error(self) -> str | None:
        return f'inner type: {self.inner_type._runtime_representation_error()}'

    @property
    def has_to_csv_value(self) -> bool:
        return getattr(self.inner_type, 'has_to_csv_value', False)

    def to_csv_value(self, value: Any) -> tuple[str, bool]:
        if value is None:
            return '', False
        return self.inner_type.to_csv_value(value.value)

    def __repr__(self) -> str:
        return f'TypeOption({self.inner_type})'

    def __str__(self) -> str:
        return f'Option[{self.inner_type}]'

    def verify_value(self, value: Any) -> Any:
        if value is None:
            return None
        elif type(value) is Some:
            return Some(self.inner_type.verify_value(value.value))
        else:
            raise Exception(f'Expected None or Some, got {value!r}')

    def equals(self, value1: Any, value2: Any) -> bool:
        if value1 is None and value2 is None:
            return True
        elif value1 is None or value2 is None:
            return False
        elif type(value1) is Some and type(value2) is Some:
            return self.inner_type.equals(value1.value, value2.value)
        else:
            raise Exception(f'Got unexpected parameter for equals: {value1!r}, {value2!r}')

    def default_value(self) -> Any:
        if self.contains_parameters:
            raise ValueError('Type parameters require resolution before producing a default value')
        return None

    def _parse_impl(self, s: str) -> Any:
        if s == 'none':
            return None
        # TODO: Use different format for strings?
        return Some(self.inner_type.parse(s))

    def to_text(self, value: Any) -> str:
        value = self.verify_value(value)
        if value is None:
            return 'None'
        return f'Some({self.inner_type.to_text(value.value)})'


class TypeStruct(Type):
    """A nominal value type with fields kept in declaration order.

    Values are tuples of field values in that order. The type name and field
    names are optional; without them, text output falls back to the native
    name and field positions. An opaque struct is implemented by native code,
    which also parses and formats its values when native_types provides its
    implementation.
    """

    is_json_serializable = False
    has_to_csv_value = False

    def __init__(self, name: str | None, fields: Mapping[str, Type] | Sequence[Type], *,
                 native_name: str | None = None, is_opaque: bool = False,
                 native_types: NativeTypes | None = None, json_names: Sequence[str] | None = None) -> None:
        """Create a struct from named fields, or from field types alone.

        json_names are the JSON names of the fields; by default, the field names.
        """
        if native_name is not None and (type(native_name) is not str or not native_name):
            raise ValueError('Struct native_name must be a nonempty string or None')
        if is_opaque and native_name is None:
            raise ValueError('Opaque structs require a native_name')
        self.name = name or native_name or 'struct'  # The declared name, else the native name.
        self.type_name = name  # The declared name, or None.
        self.native_name = native_name  # None for structs native code does not rely on.
        self.is_opaque = is_opaque
        # Native types providing the implementation of an opaque struct, or None.
        self.native_types = native_types
        self._fields: Mapping[str, Type] | None
        if isinstance(fields, Mapping):
            self._fields = MappingProxyType(dict(fields))
            self.field_names: tuple[str, ...] | None = tuple(self._fields)
            self.field_types: tuple[Type, ...] = tuple(self._fields.values())
        else:
            self._fields = None
            self.field_names = None
            self.field_types = tuple(fields)
        if any(not isinstance(field_type, Type) or field_type.contains_parameters
               for field_type in self.field_types):
            raise ValueError('Struct fields require resolved value types')
        # JSON names of the fields, the JsonTypes entry of the struct, or None without one.
        self.json_names = self.field_names if json_names is None else tuple(json_names)
        if self.json_names is not None and len(self.json_names) != len(self.field_types):
            raise ValueError(f'Struct `{self}` needs one JSON name per field')

    @property
    def fields(self) -> Mapping[str, Type]:
        """Field types by name; requires field names."""
        if self._fields is None:
            raise ValueError(f'Struct `{self}` has no field names')
        return self._fields

    def field_label(self, index: int) -> str:
        """Return a field's name, or its position without field names."""
        return str(index) if self.field_names is None else self.field_names[index]

    @property
    def is_ordered(self) -> bool:  # type: ignore[override]
        return self.is_opaque and self.native_name in ORDERED_OPAQUE_TYPES

    def less(self, value1: Any, value2: Any) -> bool:
        if not self.is_ordered:
            raise ValueError(f'`{self}` values are not ordered')
        return self.verify_value(value1) < self.verify_value(value2)

    def opaque_class(self) -> type | None:
        """Return the Python class implementing an opaque struct, or None if it is unavailable."""
        if not self.is_opaque or self.native_types is None:
            return None
        assert self.native_name is not None
        native = self.native_types.opaque_types.get(self.native_name)
        return None if native is None else native.python_type

    def check_opaque_value(self, value: Any) -> None:
        """Raise ValueError if the implementation of an opaque struct rejects a value."""
        if self.opaque_class() is not None:
            self._to_native(value)

    def _to_native(self, value: Any) -> Any:
        from ..runtime.native_values import to_native
        assert self.native_types is not None
        return to_native(None, self, value, native_types=self.native_types)

    @property
    def contains_parameters(self) -> bool:
        return False

    @property
    def has_runtime_representation(self) -> bool:
        return all(field_type.has_runtime_representation for field_type in self.field_types)

    @property
    def nesting_depth(self) -> int:
        return 1 + max((field.nesting_depth for field in self.field_types), default=0)

    @property
    def total_member_count(self) -> int:
        return 1 + sum(field.total_member_count for field in self.field_types)

    def _runtime_representation_error(self) -> str | None:
        for index, field in enumerate(self.field_types):
            if not field.has_runtime_representation:
                return f'field `{self.field_label(index)}`: {field._runtime_representation_error()}'
        return None

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f'TypeStruct({self.name})'

    def make_value(self, fields: Mapping[str, Any]) -> tuple:
        """Return the value with these field values by name."""
        if not isinstance(fields, Mapping) or fields.keys() != self.fields.keys():
            raise ValueError(f'Expected exactly the fields of `{self}`: {", ".join(f"`{field}`" for field in self.fields)}')
        return self.verify_value(tuple(fields[name] for name in self.fields))

    def index(self, name: str) -> int:
        """Return the position of a field."""
        return tuple(self.fields).index(name)

    def verify_value(self, value: Any) -> Any:
        if type(value) is not tuple or len(value) != len(self.field_types):
            raise ValueError(f'Expected a value of struct type `{self}`, got {value!r}')
        return tuple(field_type.verify_value(field) for field_type, field in zip(self.field_types, value))

    def equals(self, value1: Any, value2: Any) -> bool:
        value1 = self.verify_value(value1)
        value2 = self.verify_value(value2)
        return all(field_type.equals(field1, field2)
                   for field_type, field1, field2 in zip(self.field_types, value1, value2))

    def default_value(self) -> Any:
        return tuple(field_type.default_value() for field_type in self.field_types)

    def _parse_impl(self, text: str) -> Any:
        python_type = self.opaque_class()
        if python_type is None or not hasattr(python_type, 'parse'):
            raise ValueError(f'`{self}` does not support text parsing')
        from ..runtime.native_values import from_native
        assert self.native_types is not None
        return from_native(None, self, python_type.parse(text), native_types=self.native_types)

    def to_text(self, value: Any) -> str:
        value = self.verify_value(value)
        python_type = self.opaque_class()
        if python_type is not None and hasattr(python_type, 'to_text'):
            return self._to_native(value).to_text()
        if not value:
            return f'{self} {{}}'
        if self.field_names is None:
            fields = ', '.join(field_type.to_text(field) for field_type, field in zip(self.field_types, value))
        else:
            fields = ', '.join(f'{name}: {field_type.to_text(field)}'
                               for name, field_type, field in zip(self.field_names, self.field_types, value))
        return f'{self} {{ {fields} }}'


class TypeList(Type):
    def __init__(self, inner_type: Type) -> None:
        self.inner_type = inner_type

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TypeList):
            return False
        return self.inner_type == other.inner_type

    def __hash__(self) -> int:
        return hash(('List', self.inner_type))

    @property
    def contains_parameters(self) -> bool:
        return self.inner_type.contains_parameters

    @property
    def nesting_depth(self) -> int:
        return 1 + self.inner_type.nesting_depth

    @property
    def total_member_count(self) -> int:
        return 1 + self.inner_type.total_member_count

    @property
    def has_to_csv_value(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f'TypeList({self.inner_type})'

    def __str__(self) -> str:
        return f'List[{self.inner_type}]'

    def verify_value(self, value: Any) -> Any:
        if type(value) in (list, tuple):
            return tuple(self.inner_type.verify_value(v) for v in value)
        else:
            raise Exception(f'Expected list or tuple, got {value!r}')

    def equals(self, value1: Any, value2: Any) -> bool:
        if type(value1) not in (list, tuple) or type(value2) not in (list, tuple):
            raise Exception(f'Got unexpected parameter for equals: {value1!r}, {value2!r}')
        if len(value1) != len(value2):
            return False
        for v1, v2 in zip(value1, value2):
            if not self.inner_type.equals(v1, v2):
                return False
        return True

    def default_value(self) -> Any:
        if self.contains_parameters:
            raise ValueError('Type parameters require resolution before producing a default value')
        return ()

    def _parse_impl(self, s: str) -> Any:
        raise Exception('TODO: Implement TypeList.parse')

    def to_text(self, value: Any) -> str:
        return '[' + ', '.join(self.inner_type.to_text(item) for item in self.verify_value(value)) + ']'



class TypeResource(Type):
    """A handle to a native object, such as a KNX connection; a load-time type only.

    Resources have storage size zero and alignment 1; the runtime keeps the
    native object separately. Types are equal exactly when they are the same
    object.
    """

    def __init__(self, name: str, native_name: str) -> None:
        self.name = name  # Qualified name for diagnostics.
        self.native_name = native_name  # Name in native interfaces, such as 'KNX.Bus'.

    @property
    def contains_parameters(self) -> bool:
        return False

    @property
    def alignment(self) -> int:
        return 1

    @property
    def data_size(self) -> int:
        return 0

    def default_value(self) -> NoReturn:
        raise ValueError('Resources have no default value')

    def verify_value(self, value: Any) -> NoReturn:
        raise ValueError('Resources are opaque load-time values')

    def equals(self, value1: Any, value2: Any) -> bool:
        return value1 is value2

    def _parse_impl(self, s: str) -> NoReturn:
        raise ValueError('Resources cannot be parsed')

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f'TypeResource({self.name})'


# TODO: Add to_csv_value?
class TypeUnit(TypePrimitive):
    def __init__(self) -> None:
        super().__init__(name='unit', python_type=Unit, is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    def default_value(self) -> Any:
        return Unit()

    def _parse_impl(self, s: str) -> Any:
        if s != '()':
            raise Exception(f'Invalid unit value {s!r}')
        return Unit()

    def to_text(self, value: Any) -> str:
        self.verify_value(value)
        return '()'


class TypeBoolean(TypePrimitive):
    def bitwise(self, operation: str, left: Any, right: Any = None) -> Any:
        left = self.verify_value(left)
        if operation == 'not':
            return not left
        right = self.verify_value(right)
        if operation == 'and':
            return left & right
        if operation == 'or':
            return left | right
        if operation == 'xor':
            return left ^ right
        raise ValueError(f'Unknown bitwise operation {operation!r}')

    def __init__(self) -> None:
        super().__init__(name='boolean', python_type=bool, is_runtime_type=True, is_json_serializable=True, has_to_csv_value=True)

    def default_value(self) -> Any:
        return False

    def _parse_impl(self, s: str) -> Any:
        if s == '0' or s.lower() == 'false':
            return False
        elif s == '1' or s.lower() == 'true':
            return True
        else:
            raise Exception(f'Invalid boolean value {s!r}')

    def to_text(self, value: Any) -> str:
        return 'true' if self.verify_value(value) else 'false'

    def json_encode(self, expr: Any, format: str | None) -> Any:
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return expr

    # TODO: Add abstract method somewhere? (Same for json_decode and to_csv_value) Determine has_to_csv_value automatically?
    def json_decode(self, expr: Any, format: str | None) -> Any:
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return self.verify_value(expr)

    def to_csv_value(self, value: Any) -> tuple[str, bool]:
        if value == False:
            return 'false', False
        elif value == True:
            return 'true', False
        else:
            raise Exception('Invalid value in TypeBoolean.to_csv_value')


# TODO: Work on value verification etc.
class TypeInt(TypePrimitive):
    is_ordered = True

    def __init__(self, *, is_signed: bool, bits: int) -> None:
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

    def default_value(self) -> Any:
        return 0

    def verify_value(self, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError(f'Expected an integer for `{self}`, got {value!r}')
        if not self.min_value <= value <= self.max_value:
            raise ValueError(f'{value} is out of range for `{self}`')
        return value

    def bitwise(self, operation: str, left: Any, right: Any = None) -> Any:
        left = self.verify_value(left)
        if operation == 'not':
            result = ~left
        else:
            right = self.verify_value(right)
            if operation == 'and':
                result = left & right
            elif operation == 'or':
                result = left | right
            elif operation == 'xor':
                result = left ^ right
            else:
                raise ValueError(f'Unknown bitwise operation {operation!r}')
        return _fit_integer(result, self.min_value, self.max_value, 'wrapping')

    def shift(self, direction: str, value: int, count: int) -> int:
        value = self.verify_value(value)
        count = uint32.verify_value(count)
        if direction not in ('left', 'right'):
            raise ValueError(f'Unknown shift direction {direction!r}')
        if count >= self.bits:
            return -1 if direction == 'right' and value < 0 else 0
        result = value << count if direction == 'left' else value >> count
        return _fit_integer(result, self.min_value, self.max_value, 'wrapping')

    def arithmetic(self, operation: str, overflow: str, left: Any, right: Any) -> Any:
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

    def _parse_impl(self, s: str) -> Any:
        return int(s)

    def to_text(self, value: Any) -> str:
        return str(self.verify_value(value))

    def json_encode(self, expr: Any, format: str | None) -> Any:
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return expr

    def json_decode(self, expr: Any, format: str | None) -> Any:
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return self.verify_value(expr)

    def to_csv_value(self, value: Any) -> tuple[str, bool]:
        return str(value), False


class TypeValueType(TypePrimitive):
    def __init__(self) -> None:
        super().__init__(name='type', python_type=Type, is_runtime_type=False, is_json_serializable=False, has_to_csv_value=False)

    def verify_value(self, value: Any) -> Any:
        if not isinstance(value, Type):
            raise ValueError(f'Expected a value type, got {value!r}')
        return value

    def default_value(self) -> Any:
        return unit

    def _parse_impl(self, s: str) -> Any:
        raise Exception('TODO: TypeValueType.parse()')

    def to_text(self, value: Any) -> str:
        return str(self.verify_value(value))


# TODO: Remove json_encode because strings should not be used at runtime?
# TODO: Add to_csv_value? Probably not because it should not be used at runtime
class TypeString(TypePrimitive):
    def __init__(self) -> None:
        super().__init__(name='string', python_type=str, is_runtime_type=False, is_json_serializable=True, has_to_csv_value=False)

    def default_value(self) -> Any:
        return ''

    def _parse_impl(self, s: str) -> Any:
        raise Exception('TypeString.parse()')

    def to_text(self, value: Any) -> str:
        return _string_literal(self.verify_value(value))

    def json_encode(self, expr: Any, format: str | None) -> Any:
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return expr

    def json_decode(self, expr: Any, format: str | None) -> Any:
        if format is not None:
            raise Exception(f'Invalid JSON format: {format!r}')
        return self.verify_value(expr)


def closed_enum_underlying_type(member_count: int) -> TypeInt:
    """Choose storage for a nonempty closed enum with a uint32 member count."""
    if type(member_count) is not int or member_count < 1:
        raise ValueError('Closed enum must declare at least one member')
    if member_count > 0xffffffff:
        raise ValueError('Closed enum member count must fit uint32')
    if member_count <= 256:
        return uint8
    if member_count <= 65536:
        return uint16
    return uint32


# TODO: Add to_csv_value?
class TypeEnum(TypePrimitive):
    """A nominal enum with either dense closed values or an open integer range.

    The type name and member names are optional; without them, text output
    uses numbers. members maps the names of all members of a closed enum, or
    of the named numbers of an open enum.
    """

    def __init__(self, name: str | None, members: Mapping[str, int], *, is_open: bool, underlying_type: TypeInt,
                 native_name: str | None = None, member_count: int | None = None,
                 json_names: Mapping[int, str] | None = None) -> None:
        """Create an enum; an unnamed closed enum has no members and gives member_count.

        json_names maps numbers to their JSON names; by default, the member names.
        """
        if type(is_open) is not bool:
            raise ValueError('Enum is_open must be a boolean')
        if native_name is not None and (type(native_name) is not str or not native_name):
            raise ValueError('Enum native_name must be a nonempty string or None')
        if not isinstance(underlying_type, TypeInt):
            raise ValueError('Enum underlying_type must be an integer type')
        if is_open:
            if member_count is not None:
                raise ValueError('Open enums have no member count')
        else:
            if member_count is None:
                member_count = len(members)
            elif members and len(members) != member_count:
                raise ValueError('Closed enums name either all members or none')
            closed_enum_underlying_type(member_count)
            if underlying_type.is_signed or underlying_type.bits not in (8, 16, 32):
                raise ValueError('Closed enum underlying_type must be uint8, uint16, or uint32')
        names: dict[int, str] = {}
        for index, (member, number) in enumerate(members.items()):
            if type(number) is not int or not underlying_type.min_value <= number <= underlying_type.max_value:
                raise ValueError(f'Enum member `{member}` does not fit `{underlying_type}`')
            if not is_open and number != index:
                raise ValueError('Closed enum numbers must be consecutive from zero in declaration order')
            if number in names:
                raise ValueError(f'Enum members `{names[number]}` and `{member}` have the same number {number}')
            names[number] = member
        # name is the declared name, else the native name.
        super().__init__(name=name or native_name or 'enum', python_type=int, is_runtime_type=True,
                         is_json_serializable=True, has_to_csv_value=False)
        self.type_name = name  # The declared name, or None.
        self.member_count = member_count  # Number of members of a closed enum; None for open enums.
        self.is_open = is_open
        self.underlying_type = underlying_type
        self.native_name = native_name  # None for enums native code does not rely on.
        self.members = MappingProxyType(dict(members))
        self._names = names
        # JSON names by number, the JsonTypes entry of the enum.
        self.json_names = MappingProxyType(dict(names if json_names is None else json_names))
        self._json_numbers = {json_name: number for number, json_name in self.json_names.items()}
        if len(self._json_numbers) != len(self.json_names):
            raise ValueError(f'Enum `{self}` has duplicate JSON names')

    def verify_value(self, value: Any) -> Any:
        if self.is_open:
            return self.underlying_type.verify_value(value)
        assert self.member_count is not None
        if type(value) is not int or not 0 <= value < self.member_count:
            raise ValueError(f'Expected a member number of `{self}`, got {value!r}')
        return value

    def member_name(self, number: int) -> str | None:
        number = self.verify_value(number)
        try:
            return self._names[number]
        except KeyError:
            raise ValueError(f'Unnamed value {number} of `{self}`') from None

    def default_value(self) -> Any:
        return 0

    def to_text(self, value: Any) -> str:
        value = self.verify_value(value)
        name = self._names.get(value)
        if name is None:
            return str(value)
        return name if self.type_name is None else f'{self.type_name}.{name}'

    def _parse_impl(self, text: str) -> Any:
        if text in self.members:
            return self.members[text]
        if self.is_open or len(self.members) == 0:
            try:
                number = int(text, 10)
            except ValueError:
                pass
            else:
                return self.verify_value(number)
        raise ValueError(f'Unknown member `{text}` of `{self}`')

    def json_encode(self, value: Any, format: str | None) -> Any:
        if format not in (None, 'SnakeCase'):
            raise ValueError(f'Invalid JSON format: {format!r}')
        value = self.verify_value(value)
        name = self.json_names.get(value)
        if name is None:
            return value
        if format == 'SnakeCase':
            return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
        return name

    def json_decode(self, value: Any, format: str | None) -> Any:
        if format not in (None, 'SnakeCase'):
            raise ValueError(f'Invalid JSON format: {format!r}')
        if self.is_open and type(value) is int:
            return self.verify_value(value)
        if format == 'SnakeCase':
            raise ValueError('Decoding SnakeCase not implemented')
        if not isinstance(value, str):
            raise ValueError(f'Expected string for enum, got {value!r}')
        if value in self._json_numbers:
            return self._json_numbers[value]
        if self.is_open:
            try:
                number = int(value, 10)
            except ValueError:
                pass
            else:
                return self.verify_value(number)
        raise ValueError(f'Unknown member `{value}` of `{self}`')


_STRING_ESCAPES = {'\0': '\\0', '\t': '\\t', '\n': '\\n', '\r': '\\r', '"': '\\"', '\\': '\\\\'}


def _string_literal(text: str) -> str:
    """Quote text as a Wirely string literal, escaping other control characters."""
    def escape(char: str) -> str:
        if char in _STRING_ESCAPES:
            return _STRING_ESCAPES[char]
        if ord(char) < 0x20 or ord(char) == 0x7f:
            return f'\\u{{{ord(char):x}}}'
        return char
    return '"' + ''.join(escape(char) for char in text) + '"'


def _fit_integer(value: int, min_value: int, max_value: int, overflow: str) -> Any:
    """Map an exact integer into min_value..max_value using an overflow mode."""
    if overflow == 'wrapping':
        return (value - min_value) % (max_value - min_value + 1) + min_value
    if overflow == 'saturating':
        return min(max(value, min_value), max_value)
    if overflow == 'checked':
        return Some(value) if min_value <= value <= max_value else None
    raise ValueError(f'Unknown overflow mode {overflow!r}')


def integer_range(value_type: Type) -> tuple[int, int]:
    """Return the (min, max) numbers of an integer, enum, or boolean type."""
    if isinstance(value_type, TypeInt):
        return value_type.min_value, value_type.max_value
    if isinstance(value_type, TypeEnum):
        return integer_range(value_type.underlying_type)
    if isinstance(value_type, TypeBoolean):
        return 0, 1
    raise ValueError(f'`{value_type}` is not an integer, enum, or boolean type')


def convert(source_type: Type, target_type: Type, overflow: str, value: Any) -> Any:
    """Convert an integer, enum, or boolean value to an integer or boolean type.

    Booleans are 1-bit unsigned integers. Checked results are Some(value) or None."""
    if isinstance(source_type, TypeEnum):
        number = source_type.verify_value(value)
    else:
        integer_range(source_type)
        number = int(source_type.verify_value(value))
    if not isinstance(target_type, (TypeInt, TypeBoolean)):
        raise ValueError(f'Cannot convert to `{target_type}`')
    result = _fit_integer(number, *integer_range(target_type), overflow)
    if isinstance(target_type, TypeBoolean):
        if overflow == 'checked':
            return None if result is None else Some(bool(result.value))
        return bool(result)
    return result


unit: TypeUnit = TypeUnit()

boolean: TypeBoolean = TypeBoolean()

# Integer types
int8: TypeInt = TypeInt(is_signed=True, bits=8)
int16: TypeInt = TypeInt(is_signed=True, bits=16)
int32: TypeInt = TypeInt(is_signed=True, bits=32)
int64: TypeInt = TypeInt(is_signed=True, bits=64)
int128: TypeInt = TypeInt(is_signed=True, bits=128)
uint8: TypeInt = TypeInt(is_signed=False, bits=8)
uint16: TypeInt = TypeInt(is_signed=False, bits=16)
uint32: TypeInt = TypeInt(is_signed=False, bits=32)
uint64: TypeInt = TypeInt(is_signed=False, bits=64)
uint128: TypeInt = TypeInt(is_signed=False, bits=128)

ValueType: TypeValueType = TypeValueType()
String: TypeString = TypeString()

# def option(inner_type):
#     return TypeOption(inner_type)

# def list(inner_type):
#     return TypeList(inner_type)

type_by_name = {}
for ty in [
        boolean, unit,
        int8, int16, int32, int64, int128,
        uint8, uint16, uint32, uint64, uint128,
        ValueType, String,
]:
    type_by_name[ty.name] = ty
