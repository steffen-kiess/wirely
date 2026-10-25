"""The base class of native representations of open enums."""

from __future__ import annotations

import enum
from typing import Any


class OpenIntEnum(enum.IntEnum):
    """An IntEnum that also represents unnamed numbers of its underlying type."""

    _range: range

    def __init_subclass__(cls, *, minimum: int, maximum: int, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._range = range(minimum, maximum + 1)

    @classmethod
    def _missing_(cls, value: object) -> 'OpenIntEnum | None':
        # Unnamed numbers become uncached pseudo-members whose name is None.
        if type(value) is not int:
            return None
        if value not in cls._range:
            raise ValueError(f'{value} does not fit {cls.__name__}')
        member = int.__new__(cls, value)
        member._name_ = None  # type: ignore[assignment]
        member._value_ = value  # type: ignore[assignment]
        return member

    def __repr__(self) -> str:
        if self._name_ is None:
            return f'<{type(self).__name__}: {self._value_}>'
        return super().__repr__()
