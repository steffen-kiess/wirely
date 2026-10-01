from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Some:
    """A present option or event, with a shallowly immutable wrapper."""

    value: object
