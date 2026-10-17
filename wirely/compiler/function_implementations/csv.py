"""Compiler-only configuration predicates."""

from typing import Any

from wirely import ValueType

from ..native_functions import FunctionServices


def supports_type(value_type: ValueType[Any], *, services: FunctionServices) -> bool:
    return value_type.supports_csv
