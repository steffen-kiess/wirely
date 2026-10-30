"""Compiler-only configuration predicates."""

import os
from typing import Any

from wirely.runtime.value_types import ValueType

from ..native_functions import FunctionServices


def supports_type(value_type: ValueType[Any], *, services: FunctionServices) -> bool:
    return value_type.supports_csv


def check_filename(filename: str, *, services: FunctionServices) -> bool:
    # A relative filename would be resolved against the runtime's working directory, not the source file.
    if not os.path.isabs(filename):
        raise services.error('CSV filename must be absolute', argument=0)
    return True
