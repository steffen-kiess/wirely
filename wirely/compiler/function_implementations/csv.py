"""Compiler-only configuration predicates."""

from typing import Any

from wirely import ValueType
from wirely.compiler.contracts import csv as contracts

from ..native_functions import FunctionServices, RegisteredConstantFunction, constant_function

native_functions: dict[str, RegisteredConstantFunction] = {}


@constant_function(native_functions, contracts.SupportsType)
def supports_type(value_type: ValueType[Any], *, services: FunctionServices) -> bool:
    return value_type.supports_csv
