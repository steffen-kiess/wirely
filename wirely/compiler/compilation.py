"""Compile Wirely sources into a checked graph."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from types import MappingProxyType

from wirely.compiler import model
from wirely.compiler.diagnostics import Diagnostic, WarningSink, error_diagnostics, print_diagnostic
from ..runtime.extensions import RuntimeNativeExtension, resolve_extensions
from .extensions import CompilerNativeExtension, resolve_compiler_extensions
from .attributes import ParsedFile, ValidatedMetadata, validate_files
from .sources import (
    ParsedSource, ParseDurations, SourceError, SourceInput, parse_sources, standard_library_cache,
    standard_library_texts,
)


class _DiagnosticCallbackError(Exception):
    def __init__(self, error: Exception) -> None:
        self.error = error


# Default of compile_strs's shuffle_seed: read WIRELY_COMPILER_SHUFFLE_SEED.
_FROM_ENVIRONMENT: Any = object()  # Omission differs from an explicit None.


def compile_strs(sources: Iterable[SourceInput], *, add_std: bool = False,
                 verbose_passes: bool | None = None, always_print_stacktrace: bool | None = None,
                 extensions: Iterable[RuntimeNativeExtension] = (),
                 override_extensions: Iterable[RuntimeNativeExtension] = (),
                 compiler_extensions: Iterable[CompilerNativeExtension] = (),
                 compiler_override_extensions: Iterable[CompilerNativeExtension] = (),
                 include_builtin_extensions: bool = True,
                 services: Mapping[str, object] | None = None,
                 on_diagnostic: Callable[[Diagnostic], None] | None = None,
                 shuffle_seed: int | None = _FROM_ENVIRONMENT,
                 shuffle_layout: bool | None = None,
                 timings: list[tuple[str, float]] | None = None) -> model.NodeType | None:
    """Compile (filename, text) pairs and parsed sources, in order after the standard library if add_std is set.

    If timings is given, append the name and duration of each completed stage.
    """
    report = print_diagnostic if on_diagnostic is None else on_diagnostic

    def report_escaping(diagnostic: Diagnostic) -> None:
        try:
            report(diagnostic)
        except Exception as error:
            # Callback failures must escape the compiler's exception handling.
            raise _DiagnosticCallbackError(error) from error

    def report_progress(message: str) -> None:
        report_escaping(Diagnostic('info', message))

    compilation_services = MappingProxyType(dict(services if services is not None else {}))

    if verbose_passes is None:
        verbose_passes = model.util.getEnvironmentBool('WIRELY_COMPILER_VERBOSE_PASSES', False)
    if always_print_stacktrace is None:
        always_print_stacktrace = model.util.getEnvironmentBool('WIRELY_COMPILER_ALWAYS_PRINT_STACKTRACE', False)
    if shuffle_seed is _FROM_ENVIRONMENT:
        environment_seed = os.environ.get('WIRELY_COMPILER_SHUFFLE_SEED')
        shuffle_seed = int(environment_seed) if environment_seed else None
    if shuffle_layout is None:
        shuffle_layout = model.util.getEnvironmentBool('WIRELY_COMPILER_SHUFFLE_LAYOUT', False)

    native_extension = resolve_extensions(extensions, override_extensions=override_extensions,
                                          include_builtin_extensions=include_builtin_extensions)
    compiler_native_extension = resolve_compiler_extensions(
        compiler_extensions, override_extensions=compiler_override_extensions,
        include_builtin_extensions=include_builtin_extensions)

    warnings = WarningSink(report_escaping)
    exc: Exception | None = None
    try:
        if verbose_passes:
            report_progress('Starting parsing sources')
        durations = ParseDurations()
        parsed_sources = parse_sources(standard_library_texts() if add_std else (),
                                       cache=standard_library_cache, durations=durations)
        parsed_sources += parse_sources(sources, durations=durations)
        if timings is not None:
            timings.append(('parse', durations.parse))
            timings.append(('build syntax tree', durations.build))
        parsed_files = [(source.filename, source.tree) for source in parsed_sources]
        decls = [declaration for _, parsed in parsed_files for declaration in parsed]
        if shuffle_seed is not None:
            from .shuffle import shuffle_files
            parsed_files, decls = shuffle_files(parsed_files, shuffle_seed, layout=shuffle_layout)
        if verbose_passes:
            report_progress('Starting validating source metadata')
        start = time.perf_counter()
        metadata = validate_source_metadata(parsed_files, decls, warnings)
        if timings is not None:
            timings.append(('validate source metadata', time.perf_counter() - start))
        global_source_position = model.SourcePosition(filename='<global>', meta=None, input_data='')
        global_declaration = model.DeclarationNodeType((), None, tuple(decls), source_position=global_source_position)
        global_node_type = model.NodeType(global_declaration, None)
        global_node_type.native_extension = native_extension
        global_node_type.compiler_native_extension = compiler_native_extension
        global_node_type.services = compilation_services
        global_node_type.file_metadata = metadata.files
        from .coordinator import CompilerCoordinator
        coordinator = CompilerCoordinator(global_node_type, report_progress if verbose_passes else None,
                                          shuffle_seed=shuffle_seed, warnings=warnings, timings=timings)
        coordinator.metadata = metadata
        coordinator.compile()
    except SourceError as e:
        for diagnostic in e.diagnostics(always_print_stacktrace=always_print_stacktrace):
            report(diagnostic)
        return None
    except _DiagnosticCallbackError as e:
        raise e.error from None
    except model.ProgramError as e:
        exc = e
    except Exception as e:
        exc = e

    if exc is None:
        return global_node_type

    if not isinstance(exc, model.ProgramError):
        exc = model.InternalCompilerError(None, exc)

    for diagnostic in error_diagnostics(exc, always_print_stacktrace=always_print_stacktrace):
        report(diagnostic)
    return None


def validate_source_metadata(parsed_files: Sequence[tuple[str | os.PathLike, ParsedFile]],
                             declarations: Iterable[model.Declaration],
                             warnings: WarningSink) -> ValidatedMetadata:
    from .native_functions import validate_collisions, validate_native_names

    metadata = validate_files(parsed_files, warnings=warnings)
    validate_collisions(declarations)
    validate_native_names(declarations)
    return metadata


def compile_files(inputs: Iterable[str | os.PathLike | ParsedSource], *, add_std: bool = True,
                  extensions: Iterable[RuntimeNativeExtension] = (),
                  override_extensions: Iterable[RuntimeNativeExtension] = (),
                  compiler_extensions: Iterable[CompilerNativeExtension] = (),
                  compiler_override_extensions: Iterable[CompilerNativeExtension] = (),
                  include_builtin_extensions: bool = True,
                  services: Mapping[str, object] | None = None,
                  on_diagnostic: Callable[[Diagnostic], None] | None = None,
                  timings: list[tuple[str, float]] | None = None) -> model.NodeType | None:
    """Compile files and parsed sources, in order after the standard library if add_std is set."""
    sources: list[SourceInput] = []
    for item in inputs:
        if isinstance(item, ParsedSource):
            sources.append(item)
            continue
        with open(item, encoding='utf-8') as file:
            sources.append((item, file.read()))
    return compile_strs(sources, add_std=add_std, services=services,
                        on_diagnostic=on_diagnostic, extensions=extensions,
                        override_extensions=override_extensions, compiler_extensions=compiler_extensions,
                        compiler_override_extensions=compiler_override_extensions,
                        include_builtin_extensions=include_builtin_extensions, timings=timings)
