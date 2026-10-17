"""Compiler-host lexical path functions."""

import os
from pathlib import Path

from wirely.compiler.contracts import path as contracts

from ..native_functions import FunctionServices, RegisteredConstantFunction, constant_function

native_functions: dict[str, RegisteredConstantFunction] = {}


@constant_function(native_functions, contracts.Join)
def join(base: str, path: str, *, services: FunctionServices) -> str:
    return os.path.join(base, path)


@constant_function(native_functions, contracts.Parent)
def parent(path: str, *, services: FunctionServices) -> str:
    return str(Path(path).parent)


@constant_function(native_functions, contracts.SourceFile)
def source_file(*, services: FunctionServices) -> str:
    path = services.source_path
    if path is None:
        raise services.error('Path.SourceFile requires a filesystem source location')
    return path
