import os
from types import MappingProxyType

import lark

import wirely
from wirely.compiler import model
from wirely.compiler.diagnostics import Diagnostic, error_diagnostics, print_diagnostic
from wirely.util import check_parameters


class _DiagnosticCallbackError(Exception):
    def __init__(self, error):
        self.error = error


def compile_strs(filename_inpudatas, *, add_std=False, verbose_passes=None, always_print_stacktrace=None,
                 services=None, on_diagnostic=None, shuffle_seed=None, shuffle_layout=None):
    report = print_diagnostic if on_diagnostic is None else on_diagnostic

    def report_progress(message):
        try:
            report(Diagnostic('info', message))
        except Exception as error:
            # Callback failures must escape the compiler's exception handling.
            raise _DiagnosticCallbackError(error) from error

    compilation_services = MappingProxyType(dict(services if services is not None else {}))
    # TODO: Where should this import be done?
    import wirely.compiler.parser

    if verbose_passes is None:
        verbose_passes = model.util.getEnvironmentBool('WIRELY_COMPILER_VERBOSE_PASSES', False)
    if always_print_stacktrace is None:
        always_print_stacktrace = model.util.getEnvironmentBool('WIRELY_COMPILER_ALWAYS_PRINT_STACKTRACE', False)
    if shuffle_seed is None and os.environ.get('WIRELY_COMPILER_SHUFFLE_SEED'):
        shuffle_seed = int(os.environ['WIRELY_COMPILER_SHUFFLE_SEED'])
    if shuffle_layout is None:
        shuffle_layout = model.util.getEnvironmentBool('WIRELY_COMPILER_SHUFFLE_LAYOUT', False)

    if add_std:
        filename_inpudatas = _standard_library_sources() + list(filename_inpudatas)

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
        file_metadata = validate_source_metadata(parsed_files, decls)
        global_source_position = model.SourcePosition(filename='<global>', meta=None, input_data='')
        global_node_type = model.NodeType([], None, decls, source_position=global_source_position)
        global_node_type.services = compilation_services
        global_node_type.file_metadata = file_metadata
        from .coordinator import CompilerCoordinator
        coordinator = CompilerCoordinator(global_node_type, report_progress if verbose_passes else None,
                                          shuffle_seed=shuffle_seed)
        coordinator.compile()
    except lark.UnexpectedInput as e:
        report(Diagnostic('syntax_error', str(e), filename=e.source_filename, line=e.line,
                          context=e.get_context(e.source_input_data),
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


def validate_source_metadata(parsed_files, declarations):
    from .attributes import validate_files
    from .native_functions import validate_collisions, validate_native_names

    metadata = validate_files(parsed_files)
    validate_collisions(declarations)
    validate_native_names(declarations)
    return metadata


_STANDARD_LIBRARY_NAMES = (
    'std', 'path', 'matter', 'schedule', 'timer', 'knx', 'abb_jra', 'mqtt', 'csv',
    'two_n', 'gpio', 'memory', 'process', 'test',
)


def _standard_library_sources(names=None):
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


def compile_files(input_filenames, *, add_std=True, services=None, on_diagnostic=None):
    filename_inpudatas = []
    for input_filename in input_filenames:
        with open(input_filename, encoding='utf-8') as file:
            filename_inpudatas.append((input_filename, file.read()))
    return compile_strs(filename_inpudatas, add_std=add_std, services=services,
                        on_diagnostic=on_diagnostic)
