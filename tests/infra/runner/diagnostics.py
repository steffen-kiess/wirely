"""Expected diagnostics of test fixtures and the cases of negative tests."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import lark

from wirely.compiler import model

if TYPE_CHECKING:
    from wirely.compiler.diagnostics import Diagnostic


# An expected diagnostic (kind, line, message fragment) and a reported one (kind, filename, line, message).
ExpectedDiagnostic = tuple[str, int, str]
DiagnosticItem = tuple[str, 'str | None', 'int | None', str]


annotation_pattern = re.compile(r'\s*(\^*)([EHW]):\s*(.*?)\s*$')
shuffle_annotation_pattern = re.compile(r'\s*S([EHW]):\s*(.*?)\s*$')
library_metadata_pattern = re.compile(r'[ \t]*//[ \t]+LIBRARIES[ \t]*:[ \t]*(.*?)[ \t]*')
library_metadata_prefix_pattern = re.compile(r'[ \t]*//[ \t]+LIBRARIES\b')
case_marker_pattern = re.compile(r'[ \t]*//[ \t]+TEST(?:[ \t]*:[ \t]*(.*?))?[ \t]*')


def expected_diagnostics(source: str, *, start_line: int = 1, end_line: int | None = None,
                         shuffled: bool = False) -> list[ExpectedDiagnostic]:
    expected: list[ExpectedDiagnostic] = []
    pattern = shuffle_annotation_pattern if shuffled else annotation_pattern
    prefix = r'\s*\^*S[EHW]\b' if shuffled else r'\s*\^*[EHW]:'
    for line_number, line in enumerate(source.splitlines(), 1):
        for comment in line.split('//')[1:]:
            match = pattern.fullmatch(comment)
            if match is None:
                if re.match(prefix, comment):
                    raise AssertionError(f'Malformed annotation on line {line_number}: //{comment}')
                continue
            if shuffled:
                kind, message = match.groups()
                carets = ''
            else:
                carets, kind, message = match.groups()
            target_line = line_number - len(carets)
            if (target_line < start_line
                    or (end_line is not None and target_line > end_line)
                    or not message):
                raise AssertionError(f'Malformed annotation on line {line_number}: //{comment}')
            expected.append((kind, target_line, message))
    return expected


@dataclass(frozen=True)
class NegativeTestCase:
    label: str | None
    start_line: int
    end_line: int
    source: str
    libraries: tuple[str, ...] = ()


def negative_test_libraries(lines: Sequence[str], *, start_line: int) -> tuple[str, ...] | None:
    libraries: tuple[str, ...] | None = None
    for offset, line in enumerate(lines):
        line = line.rstrip('\r\n')
        if not library_metadata_prefix_pattern.match(line):
            continue
        line_number = start_line + offset
        match = library_metadata_pattern.fullmatch(line)
        if match is None:
            raise AssertionError(f'Malformed library metadata on line {line_number}')
        if libraries is not None:
            raise AssertionError(f'Multiple library directives on line {line_number}')
        text = match.group(1)
        if not text:
            libraries = ()
            continue
        names = tuple(name.strip() for name in text.split(','))
        for name in names:
            if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
                raise AssertionError(f'Malformed library name {name!r} on line {line_number}')
            if name not in model.compiler.STANDARD_LIBRARY_NAMES:
                raise AssertionError(f'Unknown library {name!r} on line {line_number}')
        if len(set(names)) != len(names):
            raise AssertionError(f'Duplicate library name on line {line_number}')
        libraries = names
    return libraries


def negative_test_cases(source: str) -> Iterator[NegativeTestCase]:
    lines = source.splitlines(keepends=True)
    markers = []
    for index, line in enumerate(lines):
        match = case_marker_pattern.fullmatch(line.rstrip('\r\n'))
        if match is not None:
            markers.append((index, match.group(1)))
    header_end = markers[0][0] if markers else len(lines)
    default_libraries = negative_test_libraries(lines[:header_end], start_line=1) or ()
    boundaries = [(-1, None), *markers, (len(lines), None)]
    for (marker, label), (end, _) in zip(boundaries, boundaries[1:]):
        start = marker + 1
        libraries = default_libraries
        if marker != -1:
            override = negative_test_libraries(lines[start:end], start_line=start + 1)
            if override is not None:
                libraries = override
        if marker == -1 and markers:
            preamble = ''.join(lines[start:end])
            if not re.search(r'//\s*\^*S?[EHW]:', preamble):
                try:
                    declarations = model.parser.parse_str(preamble, filename='<preamble>')
                except (lark.UnexpectedInput, lark.exceptions.VisitError):
                    pass
                else:
                    if not declarations and not declarations.inner_attributes:
                        continue
        padded = ''.join(line if start <= index < end else '\n'
                         for index, line in enumerate(lines))
        yield NegativeTestCase(label, start + 1, end, padded, libraries)


def diagnostic_items(diagnostics: Iterable[Diagnostic]) -> list[DiagnosticItem]:
    """Return (kind, absolute filename, line, message) for diagnostics other than progress."""
    items = []
    for diagnostic in diagnostics:
        if diagnostic.kind == 'info':
            continue
        kind = {'error': 'E', 'syntax_error': 'E', 'hint': 'H', 'warning': 'W'}[diagnostic.kind]
        message = diagnostic.message
        if diagnostic.kind == 'syntax_error':
            message = 'Syntax error: ' + message.splitlines()[0]
        filename = None if diagnostic.filename is None else os.path.abspath(diagnostic.filename)
        items.append((kind, filename, diagnostic.line, message))
    return items


def match_diagnostics(expected: Sequence[ExpectedDiagnostic], actual: Sequence[DiagnosticItem], full_fn: str,
                      start_line: int, end_line: int, *, relaxed: bool,
                      ) -> tuple[list[ExpectedDiagnostic], list[DiagnosticItem]]:
    """Return the expected and actual diagnostics left unmatched.

    relaxed matches any line between start_line and end_line, for shuffled
    expectations.
    """
    matched: dict[int, int] = {}

    def match_expectation(expected_index: int, seen: set[int]) -> bool:
        kind, line_number, fragment = expected[expected_index]
        for actual_index, item in enumerate(actual):
            location_matches = (isinstance(item[2], int)
                                and start_line <= item[2] <= end_line
                                if relaxed else item[2] == line_number)
            if (actual_index in seen or item[0] != kind or item[1] != full_fn
                    or not location_matches or fragment not in item[3]):
                continue
            seen.add(actual_index)
            if (actual_index not in matched
                    or match_expectation(matched[actual_index], seen)):
                matched[actual_index] = expected_index
                return True
        return False

    missing = [item for index, item in enumerate(expected)
               if not match_expectation(index, set())]
    unexpected = [item for index, item in enumerate(actual) if index not in matched]
    return missing, unexpected
