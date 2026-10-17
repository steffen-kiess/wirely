"""Compiler-host lexical path functions."""

import os
from pathlib import Path

from ..native_functions import NativeFunctionContext


def join(*, context: NativeFunctionContext) -> str:
    base, path = context.arguments
    return os.path.join(base, path)


def parent(*, context: NativeFunctionContext) -> str:
    path, = context.arguments
    return str(Path(path).parent)


def source_file(*, context: NativeFunctionContext) -> str:
    path = context.call.source_position.source_path
    if path is None:
        raise context.error('Path.SourceFile requires a filesystem source location')
    return path
