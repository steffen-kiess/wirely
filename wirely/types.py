import abc
import datetime
import re

import wirely as blocks


# A value type supporting runtime validation, equality, and text parsing.
class Type(abc.ABC):
    @property
    @abc.abstractmethod
    def contains_parameters(self):
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

        # TODO: Actually enforce is_runtime_type (i.e. disallow strings at runtime)
        self.is_runtime_type = is_runtime_type

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

    def _parse_impl(self, s):
        raise Exception('TODO: Implement TypeList.parse')


# TODO: Add to_csv_value?
class TypeUnit(TypeConcrete):
    def __init__(self):
        super().__init__(name='unit', python_type=blocks.Unit, is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    def _parse_impl(self, s):
        if s != '()':
            raise Exception(f'Invalid unit value {s!r}')
        return blocks.Unit()


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

    def _parse_impl(self, s):
        raise Exception('TODO: TypeValueType.parse()')


class TypeMonotonicTime(TypeConcrete):
    def __init__(self):
        super().__init__(name='MonotonicTime', python_type=blocks.MonotonicTime, is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    def _parse_impl(self, s):
        raise Exception('TODO: Implement TypeMonotonicTime.parse')


# TODO: Add type which represents offset to monotonic time and can be used e.g. for KNX messages which contain the current time?


# TODO: Add to_csv_value and make json serializable?
class TypeDuration(TypeConcrete):
    def __init__(self):
        super().__init__(name='Duration', python_type=blocks.Duration, is_runtime_type=True, is_json_serializable=False, has_to_csv_value=False)

    def _parse_impl(self, s):
        return blocks.Duration.parse(s)


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


# TODO: Remove json_encode because strings should not be used at runtime?
# TODO: Add to_csv_value? Probably not because it should not be used at runtime
class TypeString(TypeConcrete):
    def __init__(self):
        super().__init__(name='String', python_type=str, is_runtime_type=False, is_json_serializable=True, has_to_csv_value=False)

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
    def __init__(self, name, python_type):
        super().__init__(name=name, python_type=python_type, is_runtime_type=True, is_json_serializable=True, has_to_csv_value=False)

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
