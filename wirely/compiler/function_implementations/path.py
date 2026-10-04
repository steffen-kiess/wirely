"""Compiler-host lexical path functions."""

import os
from pathlib import Path


def join(*, context):
    return os.path.join(context.arguments['base'], context.arguments['path'])


def parent(*, context):
    return str(Path(context.arguments['path']).parent)


def source_file(*, context):
    path = context.call.source_position.source_path
    if path is None:
        raise context.error('Path.SourceFile requires a filesystem source location')
    return path
