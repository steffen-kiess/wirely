"""Descriptions of the interfaces that native code expects.

`wirely/runtime/native_interfaces.py`, generated from the standard library,
describes native state types, procedures, and functions with these types.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


class SignatureType:
    """A value type in a native interface."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Builtin(SignatureType):
    name: str  # 'unit', 'boolean', 'String', 'Duration', 'TimeOfDay', 'MonotonicTime', or 'ValueType'.


@dataclass(frozen=True, slots=True)
class Int(SignatureType):
    bits: int
    signed: bool


@dataclass(frozen=True, slots=True)
class Option(SignatureType):
    inner: SignatureType


@dataclass(frozen=True, slots=True)
class List(SignatureType):
    inner: SignatureType


@dataclass(frozen=True, slots=True)
class Enum(SignatureType):
    native_name: str


@dataclass(frozen=True, slots=True)
class Struct(SignatureType):
    native_name: str


@dataclass(frozen=True, slots=True)
class TypeVar(SignatureType):
    """The `ValueType` bound to this key of the same initializer's or procedure's `uses`."""

    name: str


@dataclass(frozen=True, slots=True)
class ResourceType:
    native_name: str


@dataclass(frozen=True, slots=True)
class Port:
    kind: str  # 'value', 'event', or 'resource'.
    type: SignatureType | ResourceType  # Value or event payload type, or resource type.
    multi: bool = False


def _frozen(mapping):
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True, slots=True)
class Initializer:
    """The native initializer of a node type with native state."""

    depends: Mapping[str, Port]
    produces: Mapping[str, Port]
    uses: Mapping[str, SignatureType]

    def __post_init__(self):
        for name in ('depends', 'produces', 'uses'):
            object.__setattr__(self, name, _frozen(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class Procedure:
    stage: str  # 'input', 'default', or 'output'.
    depends: Mapping[str, Port]
    produces: Mapping[str, Port]
    uses: Mapping[str, SignatureType]

    def __post_init__(self):
        for name in ('depends', 'produces', 'uses'):
            object.__setattr__(self, name, _frozen(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class Function:
    """A runtime native function; its parameters are positional."""

    parameters: tuple[SignatureType, ...]
    result: SignatureType

    def __post_init__(self):
        object.__setattr__(self, 'parameters', tuple(self.parameters))


def _builtin_types():
    from .. import types
    return {'unit': types.TypeUnit, 'boolean': types.TypeBoolean, 'String': types.TypeString,
            'Duration': types.TypeDuration, 'TimeOfDay': types.TypeTimeOfDay,
            'MonotonicTime': types.TypeMonotonicTime, 'ValueType': types.TypeValueType}


def matches(signature_type, value_type, bindings):
    """Whether a value type has a signature type; bindings map type variable names to types."""
    from .. import types

    if isinstance(signature_type, TypeVar):
        return signature_type.name in bindings and value_type == bindings[signature_type.name]
    if isinstance(signature_type, Builtin):
        return type(value_type) is _builtin_types().get(signature_type.name)
    if isinstance(signature_type, Int):
        return (isinstance(value_type, types.TypeInt) and value_type.bits == signature_type.bits
                and value_type.is_signed == signature_type.signed)
    if isinstance(signature_type, (Option, List)):
        cls = types.TypeOption if isinstance(signature_type, Option) else types.TypeList
        return isinstance(value_type, cls) and matches(signature_type.inner, value_type.inner_type, bindings)
    if isinstance(signature_type, Enum):
        return isinstance(value_type, types.TypeEnum) and value_type.native_name == signature_type.native_name
    if isinstance(signature_type, Struct):
        return isinstance(value_type, types.TypeStruct) and value_type.native_name == signature_type.native_name
    raise ValueError(f'Unknown signature type {signature_type!r}')


def describe(signature_type, bindings):
    """Render a signature type in Wirely syntax, with bound type variables replaced."""
    if isinstance(signature_type, TypeVar):
        bound = bindings.get(signature_type.name)
        return signature_type.name if bound is None else str(bound)
    if isinstance(signature_type, Builtin):
        return signature_type.name
    if isinstance(signature_type, Int):
        return f'{"int" if signature_type.signed else "uint"}{signature_type.bits}'
    if isinstance(signature_type, (Option, List)):
        return f'{type(signature_type).__name__}[{describe(signature_type.inner, bindings)}]'
    if isinstance(signature_type, Enum):
        return f'native enum {signature_type.native_name}'
    if isinstance(signature_type, Struct):
        return f'native struct {signature_type.native_name}'
    raise ValueError(f'Unknown signature type {signature_type!r}')
