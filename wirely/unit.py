from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Unit:
    """The immutable value of Wirely's unit type."""
