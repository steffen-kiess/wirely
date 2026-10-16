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
    name: str  # Qualified name.


@dataclass(frozen=True, slots=True)
class TypeVar(SignatureType):
    """The type passed to native code as the `ValueType` bound to this `uses` key."""

    name: str


@dataclass(frozen=True, slots=True)
class Any(SignatureType):
    """A type that native code is not told and must handle generically."""


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
