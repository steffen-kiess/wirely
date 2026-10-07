import os
from types import MappingProxyType

import lark

import wirely
from wirely.compiler import model as blocks
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
        verbose_passes = blocks.util.getEnvironmentBool('WIRELY_COMPILER_VERBOSE_PASSES', False)
    if always_print_stacktrace is None:
        always_print_stacktrace = blocks.util.getEnvironmentBool('WIRELY_COMPILER_ALWAYS_PRINT_STACKTRACE', False)
    if shuffle_seed is None and os.environ.get('WIRELY_COMPILER_SHUFFLE_SEED'):
        shuffle_seed = int(os.environ['WIRELY_COMPILER_SHUFFLE_SEED'])
    if shuffle_layout is None:
        shuffle_layout = blocks.util.getEnvironmentBool('WIRELY_COMPILER_SHUFFLE_LAYOUT', False)

    if add_std:
        filename_inpudatas = _standard_library_sources() + list(filename_inpudatas)

    exc = None
    try:
        from .attributes import validate_files
        decls = []
        parsed_files = []
        for filename, input_data in filename_inpudatas:
            parsed = wirely.compiler.parser.parse_str(input_data, filename=filename)
            parsed_files.append((filename, parsed))
            decls += parsed
        if shuffle_seed is not None:
            from .shuffle import shuffle_declarations
            shuffle_declarations(decls, shuffle_seed, layout=shuffle_layout)
        file_metadata = validate_files(parsed_files)
        from .native_functions import validate_collisions
        validate_collisions(decls)
        filename = None
        global_source_position = blocks.SourcePosition(filename='<global>', meta=None, input_data='')
        global_block_type = blocks.BlockType([], None, decls, source_position=global_source_position)
        global_block_type.services = compilation_services
        global_block_type.file_metadata = file_metadata
        if verbose_passes:
            report_progress('Starting pass 1')
        global_block_type.compile_pass1(None)
        if verbose_passes:
            report_progress('Starting pass 1.5')
        global_block_type.compile_pass1_5(None)
        if verbose_passes:
            report_progress('Starting pass 2')
        global_block_type.compile_pass2(None)
        if verbose_passes:
            report_progress('Finished all passes')
        # TODO: Clean up
        # for decl in decls:
        #     with decl.context:
        #         decl.compile_pass1(graph)
        # for decl in decls:
        #     with decl.context:
        #         decl.compile_pass1_5(graph)
        # for decl in decls:
        #     with decl.context:
        #         decl.compile_pass2(graph)
    except lark.UnexpectedInput as e:
        # TODO: Using input_data here is kind of a hack
        report(Diagnostic('syntax_error', str(e), filename=filename, line=e.line,
                          context=e.get_context(input_data)))
        return None
    except _DiagnosticCallbackError as e:
        raise e.error from None
    except lark.exceptions.VisitError as e:
        exc = e.orig_exc
    except blocks.ProgramError as e:
        exc = e
    except Exception as e:
        exc = e

    if exc is None:
        return global_block_type

    if not isinstance(exc, blocks.ProgramError):
        exc = blocks.InternalCompilerError(None, exc)

    for diagnostic in error_diagnostics(exc, always_print_stacktrace=always_print_stacktrace):
        report(diagnostic)
    return None


_STANDARD_LIBRARY_NAMES = (
    'std', 'path', 'matter', 'time', 'knx', 'abb_jra', 'mqtt', 'csv',
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
