"""Compiler messages independent of compiler objects and output streams."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
import sys
import traceback
from typing import TYPE_CHECKING, Literal, TextIO, TypedDict

if TYPE_CHECKING:
    from . import model


class _Offsets(TypedDict, total=False):
    start: int
    end: int


@dataclass(frozen=True)
class Diagnostic:
    kind: Literal['error', 'syntax_error', 'internal_error', 'hint', 'warning', 'info']
    message: str
    filename: str | os.PathLike | None = None
    line: int | None = None
    context: str | None = None
    traceback: str | None = None
    # Offsets into the source text of filename, if known.
    start: int | None = None
    end: int | None = None


def print_diagnostic(diagnostic: Diagnostic, *, file: TextIO | None = None,
                     traceback_file: TextIO | None = None) -> None:
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


def error_diagnostics(error: model.ProgramError, *, always_print_stacktrace: bool = False) -> Iterator[Diagnostic]:
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
        **_offsets(position),
    )
    for hint in error.hints:
        yield _hint_diagnostic(hint)


# Warning names accepted by #[Allow(...)].
WARNINGS = frozenset({'NativeFunction', 'RedundantOn', 'CoincidentTriggers', 'UnguardedEvent', 'Unused'})


class WarningSink:
    """Report compiler warnings unless suppressed or already reported.

    A warning found in several instances is reported once, with the
    instantiation hint of the first. Callers may replace report between
    phases.
    """

    def __init__(self, report: Callable[[Diagnostic], None]) -> None:
        self.report = report
        self._allowed: list[tuple[str, str | os.PathLike | None, int | None, int | None]] = []
        self._reported: set[tuple[str, str | os.PathLike | None, int, str]] = set()
        # Instantiation hints of the instances being evaluated, innermost last.
        self._instances: list[model.CompilerHint] = []

    def allow(self, name: str, filename: str | os.PathLike | None, start: int | None = None,
              end: int | None = None) -> None:
        """Suppress name in filename between the offsets, or in the whole file."""
        self._allowed.append((name, filename, start, end))

    @contextlib.contextmanager
    def instance(self, hint: model.CompilerHint | None) -> Iterator[None]:
        """Add hint, if not None, to instance-dependent warnings reported inside."""
        if hint is None:
            yield
            return
        self._instances.append(hint)
        try:
            yield
        finally:
            self._instances.pop()

    def warn(self, name: str, element: model.ast.SourceElement, message: str, *,
             hints: Iterable[model.CompilerHint] = (), instance_trace: bool = True) -> None:
        if name not in WARNINGS:
            raise ValueError(f'Unknown warning `{name}`')
        position = element.source_position
        offset = position.start_pos
        for allowed, filename, start, end in self._allowed:
            if allowed == name and filename == position.filename:
                if start is None:
                    return
                assert end is not None
                if start <= offset < end:
                    return
        key = name, position.filename, offset, message
        if key in self._reported:
            return
        self._reported.add(key)
        if instance_trace and self._instances:
            hints = (*hints, self._instances[-1])
        for diagnostic in warning_diagnostics(name, element, message, hints):
            self.report(diagnostic)


def warning_diagnostics(name: str, element: model.ast.SourceElement, message: str,
                        hints: Iterable[model.CompilerHint] = ()) -> Iterator[Diagnostic]:
    position = element.source_position
    yield Diagnostic('warning', f'{message} [{name}]', filename=position.filename,
                     line=position.start_line, context=_context(element), **_offsets(position))
    for hint in hints:
        yield _hint_diagnostic(hint)


def _context(element: model.ast.SourceElement) -> str:
    import lark

    error = lark.exceptions.UnexpectedInput()
    error.pos_in_stream = element.source_position.start_pos  # type: ignore[assignment]
    return error.get_context(element.source_position.input_data)


def _hint_diagnostic(hint: model.CompilerHint) -> Diagnostic:
    position = hint.source_element.source_position
    return Diagnostic('hint', str(hint), filename=position.filename,
                      line=position.start_line, context=hint.get_context(), **_offsets(position))


def _offsets(position: model.SourcePosition | None) -> _Offsets:
    meta = None if position is None else position.meta
    if meta is None or getattr(meta, 'empty', True):
        return {}
    return {'start': meta.start_pos, 'end': meta.end_pos}
