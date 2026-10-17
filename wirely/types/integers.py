"""Checked values of Wirely's integer types for native Python code."""

from __future__ import annotations

import functools
from typing import Any, ClassVar


@functools.total_ordering
class Integer:
    """An immutable integer within the range of one Wirely integer type.

    Values compare and hash only with values of the same class; read the
    Python integer with `value`.
    """

    __slots__ = ('_value',)

    bits: ClassVar[int]
    signed: ClassVar[bool]
    min_value: ClassVar[int]
    max_value: ClassVar[int]

    _value: int

    def __init_subclass__(cls, *, bits: int, signed: bool, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.bits = bits
        cls.signed = signed
        cls.min_value = -2**(bits - 1) if signed else 0
        cls.max_value = 2**(bits - 1) - 1 if signed else 2**bits - 1

    def __init__(self, value: int) -> None:
        if type(self) is Integer:
            raise TypeError('Integer is abstract; use a class such as UInt8')
        if type(value) is not int:
            raise ValueError(f'Expected an int for {type(self).__name__}, got {value!r}')
        if not self.min_value <= value <= self.max_value:
            raise ValueError(f'{value} is out of range for {type(self).__name__}')
        object.__setattr__(self, '_value', value)

    @property
    def value(self) -> int:
        return self._value

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f'{type(self).__name__} is immutable')

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f'{type(self).__name__} is immutable')

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        assert isinstance(other, Integer)
        return self._value == other._value

    def __lt__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        assert isinstance(other, Integer)
        return self._value < other._value

    def __hash__(self) -> int:
        return hash((type(self), self._value))

    def __repr__(self) -> str:
        return f'{type(self).__name__}({self._value})'

    def __reduce__(self) -> tuple[type, tuple[int]]:
        return type(self), (self._value,)


class Int8(Integer, bits=8, signed=True):
    __slots__ = ()


class Int16(Integer, bits=16, signed=True):
    __slots__ = ()


class Int32(Integer, bits=32, signed=True):
    __slots__ = ()


class Int64(Integer, bits=64, signed=True):
    __slots__ = ()


class Int128(Integer, bits=128, signed=True):
    __slots__ = ()


class UInt8(Integer, bits=8, signed=False):
    __slots__ = ()


class UInt16(Integer, bits=16, signed=False):
    __slots__ = ()


class UInt32(Integer, bits=32, signed=False):
    __slots__ = ()


class UInt64(Integer, bits=64, signed=False):
    __slots__ = ()


class UInt128(Integer, bits=128, signed=False):
    __slots__ = ()


_classes: dict[tuple[int, bool], type[Integer]] = {
    (cls.bits, cls.signed): cls
    for cls in (Int8, Int16, Int32, Int64, Int128, UInt8, UInt16, UInt32, UInt64, UInt128)
}


def integer_class(bits: int, signed: bool) -> type[Integer]:
    """Return the class of an integer type."""
    try:
        return _classes[bits, signed]
    except KeyError:
        raise ValueError(f'No {"signed" if signed else "unsigned"} integer type with {bits} bits') from None
