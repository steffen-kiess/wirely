"""Parsed source files, which compilations can share, and a cache of them."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass

import lark

from wirely.compiler import model
from .attributes import ParsedFile
from .diagnostics import Diagnostic, error_diagnostics


@dataclass(frozen=True, eq=False)
class ParsedSource:
    """The syntax tree of a source file. Compilations do not modify it."""

    filename: str | os.PathLike
    text: str
    tree: ParsedFile


class SourceError(Exception):
    """A source file that failed to parse."""

    def __init__(self, filename: str | os.PathLike, text: str,
                 error: lark.UnexpectedInput | model.ProgramError) -> None:
        super().__init__(str(error))
        self.filename = filename
        self.text = text
        self.error = error

    def diagnostics(self, *, always_print_stacktrace: bool = False) -> list[Diagnostic]:
        error = self.error
        if isinstance(error, lark.UnexpectedInput):
            return [Diagnostic('syntax_error', str(error), filename=self.filename, line=error.line,
                               context=error.get_context(self.text), start=getattr(error, 'pos_in_stream', None))]
        return list(error_diagnostics(error, always_print_stacktrace=always_print_stacktrace))


@dataclass
class ParseDurations:
    """The total durations of parsing and of building syntax trees."""

    parse: float = 0.0
    build: float = 0.0


def parse_source(filename: str | os.PathLike, text: str) -> ParsedSource:
    """Parse the text of a source file; raise SourceError if it does not parse."""
    return _parse(filename, text, None)


def parse_file(path: str | os.PathLike) -> ParsedSource:
    """Read and parse a UTF-8 source file; raise SourceError if it does not parse."""
    with open(path, encoding='utf-8') as file:
        return parse_source(path, file.read())


def _parse(filename: str | os.PathLike, text: str, durations: ParseDurations | None) -> ParsedSource:
    from . import parser

    start = time.perf_counter()
    try:
        tree = parser.parser.parse(text)
    except lark.UnexpectedInput as error:
        raise SourceError(filename, text, error) from None
    parsed_at = time.perf_counter()
    try:
        parsed = parser.Transformer(filename, input_data=text).transform(tree)
    except lark.exceptions.VisitError as error:
        if not isinstance(error.orig_exc, model.ProgramError):
            raise error.orig_exc from None
        raise SourceError(filename, text, error.orig_exc) from None
    except model.ProgramError as error:
        raise SourceError(filename, text, error) from None
    finally:
        if durations is not None:
            durations.parse += parsed_at - start
            durations.build += time.perf_counter() - parsed_at
    return ParsedSource(filename, text, parsed)


class ParseCache:
    """Parsed sources by filename and text, which threads can share.

    Sources that fail to parse are not cached. When threads parse the same
    source at once, all of them get the first result.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sources: dict[tuple[str | bytes, str], ParsedSource] = {}

    def parse_source(self, filename: str | os.PathLike, text: str) -> ParsedSource:
        """Return the cached parse of text, parsing it if needed; raise SourceError if it does not parse."""
        return self._parse(filename, text, None)

    def parse_file(self, path: str | os.PathLike) -> ParsedSource:
        with open(path, encoding='utf-8') as file:
            return self.parse_source(path, file.read())

    def _parse(self, filename: str | os.PathLike, text: str, durations: ParseDurations | None) -> ParsedSource:
        key = (os.fspath(filename), text)
        with self._lock:
            source = self._sources.get(key)
        if source is not None:
            return source
        source = _parse(filename, text, durations)
        with self._lock:
            return self._sources.setdefault(key, source)

    def __contains__(self, item: tuple[str | os.PathLike, str]) -> bool:
        filename, text = item
        with self._lock:
            return (os.fspath(filename), text) in self._sources

    def clear(self) -> None:
        with self._lock:
            self._sources.clear()


SourceInput = tuple[str | os.PathLike, str] | ParsedSource


def parse_sources(sources: Iterable[SourceInput], *, cache: ParseCache | None = None, reparse: bool = False,
                  durations: ParseDurations | None = None) -> list[ParsedSource]:
    """Parse (filename, text) pairs, through cache if given, and keep parsed sources, in order.

    With reparse, parsed sources are parsed again from their text, so changing
    the result does not change them.
    """
    result = []
    for source in sources:
        if isinstance(source, ParsedSource):
            if not reparse:
                result.append(source)
                continue
            filename, text = source.filename, source.text
        else:
            filename, text = source
        result.append(cache._parse(filename, text, durations) if cache is not None
                      else _parse(filename, text, durations))
    return result


STANDARD_LIBRARY_NAMES = (
    'std', 'path', 'matter', 'schedule', 'timer', 'knx', 'abb_jra', 'mqtt', 'csv',
    'two_n', 'gpio', 'memory', 'process', 'test',
)

# Holds the standard library; compilations do not cache other sources.
standard_library_cache = ParseCache()


def standard_library_texts(names: Iterable[str] | None = None) -> list[tuple[str, str]]:
    """Return the (filename, text) of standard libraries, by default all, in dependency order."""
    names = STANDARD_LIBRARY_NAMES if names is None else tuple(names)
    for name in names:
        if name not in STANDARD_LIBRARY_NAMES:
            raise ValueError(f'Unknown library {name!r}')
    directory = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'lib')
    sources = []
    for name in names:
        filename = os.path.join(directory, name + '.wrl')
        with open(filename, encoding='utf-8') as file:
            sources.append((filename, file.read()))
    return sources


def standard_library_sources(names: Iterable[str] | None = None) -> list[ParsedSource]:
    """Return the parsed standard libraries from standard_library_cache."""
    return parse_sources(standard_library_texts(names), cache=standard_library_cache)
