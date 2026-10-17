from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar('T', covariant=True)


@dataclass(frozen=True, slots=True)
class Some(Generic[T]):
    """A present option or event, with a shallowly immutable wrapper."""

    value: T
