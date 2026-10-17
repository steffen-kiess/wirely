from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from types import MappingProxyType

import lark

import wirely
from wirely.compiler import model
from wirely.compiler.diagnostics import Diagnostic, WarningSink, error_diagnostics, print_diagnostic
from wirely.util import check_parameters
from ..runtime.extensions import RuntimeNativeExtension, resolve_extensions
from .extensions import CompilerNativeExtension, resolve_compiler_extensions
from .attributes import FileMetadata, ParsedFile


class _DiagnosticCallbackError(Exception):
    def __init__(self, error: Exception) -> None:
        self.error = error


# Default of compile_strs's shuffle_seed: read WIRELY_COMPILER_SHUFFLE_SEED.
_FROM_ENVIRONMENT: Any = object()  # Omission differs from an explicit None.


def compile_strs(filename_inpudatas: Iterable[tuple[str | os.PathLike, str]], *, add_std: bool = False,
                 verbose_passes: bool | None = None, always_print_stacktrace: bool | None = None,
                 extensions: Iterable[RuntimeNativeExtension] = (),
                 override_extensions: Iterable[RuntimeNativeExtension] = (),
                 compiler_extensions: Iterable[CompilerNativeExtension] = (),
                 compiler_override_extensions: Iterable[CompilerNativeExtension] = (),
                 include_builtin_extensions: bool = True,
                 services: Mapping[str, object] | None = None,
                 on_diagnostic: Callable[[Diagnostic], None] | None = None,
                 shuffle_seed: int | None = _FROM_ENVIRONMENT,
                 shuffle_layout: bool | None = None) -> model.NodeType | None:
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
    # TODO: Where should this import be done?
    import wirely.compiler.parser

    if verbose_passes is None:
        verbose_passes = model.util.getEnvironmentBool('WIRELY_COMPILER_VERBOSE_PASSES', False)
    if always_print_stacktrace is None:
        always_print_stacktrace = model.util.getEnvironmentBool('WIRELY_COMPILER_ALWAYS_PRINT_STACKTRACE', False)
    if shuffle_seed is _FROM_ENVIRONMENT:
        environment_seed = os.environ.get('WIRELY_COMPILER_SHUFFLE_SEED')
        shuffle_seed = int(environment_seed) if environment_seed else None
    if shuffle_layout is None:
        shuffle_layout = model.util.getEnvironmentBool('WIRELY_COMPILER_SHUFFLE_LAYOUT', False)

    if add_std:
        filename_inpudatas = _standard_library_sources() + list(filename_inpudatas)

    native_extension = resolve_extensions(extensions, override_extensions=override_extensions,
                                          include_builtin_extensions=include_builtin_extensions)
    compiler_native_extension = resolve_compiler_extensions(
        compiler_extensions, override_extensions=compiler_override_extensions,
        include_builtin_extensions=include_builtin_extensions)

    warnings = WarningSink(report_escaping)
    exc = None
    try:
        if verbose_passes:
            report_progress('Starting parsing sources')
        parsed_files = wirely.compiler.parser.parse_sources(filename_inpudatas)
        decls = [declaration for _, parsed in parsed_files for declaration in parsed]
        if shuffle_seed is not None:
            from .shuffle import shuffle_declarations
            shuffle_declarations(decls, shuffle_seed, layout=shuffle_layout)
        if verbose_passes:
            report_progress('Starting validating source metadata')
        file_metadata = validate_source_metadata(parsed_files, decls, warnings)
        global_source_position = model.SourcePosition(filename='<global>', meta=None, input_data='')
        global_node_type = model.NodeType([], None, decls, source_position=global_source_position)
        global_node_type.native_extension = native_extension
        global_node_type.compiler_native_extension = compiler_native_extension
        global_node_type.services = compilation_services
        global_node_type.file_metadata = file_metadata
        from .coordinator import CompilerCoordinator
        coordinator = CompilerCoordinator(global_node_type, report_progress if verbose_passes else None,
                                          shuffle_seed=shuffle_seed, warnings=warnings)
        coordinator.compile()
    except lark.UnexpectedInput as e:
        report(Diagnostic('syntax_error', str(e), filename=e.source_filename,  # type: ignore[attr-defined]
                          line=e.line, context=e.get_context(e.source_input_data),  # type: ignore[attr-defined]
                          start=getattr(e, 'pos_in_stream', None)))
        return None
    except _DiagnosticCallbackError as e:
        raise e.error from None
    except lark.exceptions.VisitError as e:
        exc = e.orig_exc
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
                             warnings: WarningSink) -> tuple[FileMetadata, ...]:
    from .attributes import validate_files
    from .native_functions import validate_collisions, validate_native_names

    metadata = validate_files(parsed_files, warnings=warnings)
    validate_collisions(declarations)
    validate_native_names(declarations)
    return metadata


_STANDARD_LIBRARY_NAMES = (
    'std', 'path', 'matter', 'schedule', 'timer', 'knx', 'abb_jra', 'mqtt', 'csv',
    'two_n', 'gpio', 'memory', 'process', 'test',
)


def _standard_library_sources(names: Iterable[str] | None = None) -> list[tuple[str, str]]:
    names = _STANDARD_LIBRARY_NAMES if names is None else tuple(names)
    for name in names:
        if name not in _STANDARD_LIBRARY_NAMES:
            raise ValueError(f'Unknown library {name!r}')
    directory = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'lib')
    sources = []
    for name in names:
        filename = os.path.join(directory, name + '.wrl')
        with open(filename, encoding='utf-8') as file:
            sources.append((filename, file.read()))
    return sources


def compile_files(input_filenames: Iterable[str | os.PathLike], *, add_std: bool = True,
                  extensions: Iterable[RuntimeNativeExtension] = (),
                  override_extensions: Iterable[RuntimeNativeExtension] = (),
                  compiler_extensions: Iterable[CompilerNativeExtension] = (),
                  compiler_override_extensions: Iterable[CompilerNativeExtension] = (),
                  include_builtin_extensions: bool = True,
                  services: Mapping[str, object] | None = None,
                  on_diagnostic: Callable[[Diagnostic], None] | None = None) -> model.NodeType | None:
    filename_inpudatas = []
    for input_filename in input_filenames:
        with open(input_filename, encoding='utf-8') as file:
            filename_inpudatas.append((input_filename, file.read()))
    return compile_strs(filename_inpudatas, add_std=add_std, services=services,
                        on_diagnostic=on_diagnostic, extensions=extensions,
                        override_extensions=override_extensions, compiler_extensions=compiler_extensions,
                        compiler_override_extensions=compiler_override_extensions,
                        include_builtin_extensions=include_builtin_extensions)
