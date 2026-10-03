"""Immutable values for named Wirely structs."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True, eq=False)
class StructValue(Mapping):
    """A field mapping carrying the identity of its declared struct type."""

    _type: object
    _fields: Mapping

    def __post_init__(self):
        object.__setattr__(self, '_fields', MappingProxyType(dict(self._fields)))

    def __getitem__(self, name):
        return self._fields[name]

    def __iter__(self):
        return iter(self._fields)

    def __len__(self):
        return len(self._fields)

    def __eq__(self, other):
        return (isinstance(other, StructValue) and self._type is other._type
                and self._fields == other._fields)

    __hash__ = None

    def __repr__(self):
        fields = ', '.join(f'{name}: {value!r}' for name, value in self.items())
        return f'{self._type} {{ {fields} }}'
