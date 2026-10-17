"""Helpers shared by native implementations."""

from typing import Any

from wirely.types.value_types import ValueType


def check_state_type(name: str, procedure_type: ValueType[Any], state_type: ValueType[Any]) -> None:
    """Raise ValueError unless a procedure's ValueType matches its native state's; call it in prepare()."""
    if procedure_type != state_type:
        raise ValueError(f'{name}: procedure type {procedure_type} does not match state type {state_type}')
