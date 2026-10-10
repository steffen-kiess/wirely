"""Compiler messages independent of compiler objects and output streams."""

from dataclasses import dataclass
import sys
import traceback
from typing import Literal


@dataclass(frozen=True)
class Diagnostic:
    kind: Literal['error', 'syntax_error', 'internal_error', 'hint', 'warning', 'info']
    message: str
    filename: str | None = None
    line: int | None = None
    context: str | None = None
    traceback: str | None = None


def print_diagnostic(diagnostic, *, file=None, traceback_file=None):
    if diagnostic.kind == 'syntax_error':
        print(f'Error in {diagnostic.filename}:{diagnostic.line}', file=file)
        print(diagnostic.message, file=file)
    elif diagnostic.kind == 'internal_error':
        location = '' if diagnostic.line is None else f' while compiling line {diagnostic.line}'
        print(f'Internal compiler error{location}: {diagnostic.message}', file=file)
    elif diagnostic.kind == 'info':
        print(diagnostic.message, file=file, flush=True)
    else:
        prefix = {'error': 'Error in', 'hint': 'Hint:', 'warning': 'Warning in'}[diagnostic.kind]
        print(f'{prefix} {diagnostic.filename}:{diagnostic.line}: {diagnostic.message}', file=file)
    if diagnostic.context is not None:
        print(diagnostic.context, file=file)
    if diagnostic.traceback is not None:
        destination = sys.stderr if traceback_file is None else traceback_file
        print(diagnostic.traceback, end='', file=destination)


def error_diagnostics(error, *, always_print_stacktrace=False):
    from . import model

    internal = isinstance(error, model.InternalCompilerError)
    source = error.source_element
    position = None if source is None else source.source_position
    stacktrace = None
    if internal or always_print_stacktrace:
        exception = error.inner_exception if internal else error
        stacktrace = ''.join(traceback.format_exception(exception))
    yield Diagnostic(
        'internal_error' if internal else 'error',
        str(error.inner_exception) if internal else str(error),
        filename=None if position is None else position.filename,
        line=None if position is None else position.start_line,
        context=None if source is None else error.get_context(),
        traceback=stacktrace,
    )
    for hint in error.hints:
        position = hint.source_element.source_position
        yield Diagnostic('hint', str(hint), filename=position.filename,
                         line=position.start_line, context=hint.get_context())
