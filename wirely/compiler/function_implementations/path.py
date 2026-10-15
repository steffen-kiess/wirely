"""Compiler-host lexical path functions."""

import os
from pathlib import Path


def join(*, context):
    base, path = context.arguments
    return os.path.join(base, path)


def parent(*, context):
    path, = context.arguments
    return str(Path(path).parent)


def source_file(*, context):
    path = context.call.source_position.source_path
    if path is None:
        raise context.error('Path.SourceFile requires a filesystem source location')
    return path
