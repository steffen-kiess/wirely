import os
import traceback

import lark

import wirely
from wirely.compiler import model as blocks
from wirely.util import check_parameters


def compile_strs(filename_inpudatas, *, verbose_passes=None, always_print_stacktrace=None):
    # TODO: Where should this import be done?
    import wirely.parser

    if verbose_passes is None:
        verbose_passes = blocks.util.getEnvironmentBool('WIRELY_COMPILER_VERBOSE_PASSES', False)
    if always_print_stacktrace is None:
        always_print_stacktrace = blocks.util.getEnvironmentBool('WIRELY_COMPILER_ALWAYS_PRINT_STACKTRACE', False)

    exc = None
    try:
        decls = []
        for filename, input_data in filename_inpudatas:
            decls += wirely.parser.parse_str(input_data, filename=filename)
        filename = None
        global_source_position = blocks.SourcePosition(filename='<global>', meta=None, input_data='')
        global_block_type = blocks.BlockType([], None, decls, source_position=global_source_position)
        if verbose_passes:
            print('Starting pass 1', flush=True)
        global_block_type.compile_pass1(None)
        if verbose_passes:
            print('Starting pass 1.5', flush=True)
        global_block_type.compile_pass1_5(None)
        if verbose_passes:
            print('Starting pass 2', flush=True)
        global_block_type.compile_pass2(None)
        if verbose_passes:
            print('Finished all passes', flush=True)
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
        print(f'Error in {filename}:{e.line}')
        print(e)
        # TODO: Using input_data here is kind of a hack
        print(e.get_context(input_data))
        return None
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

    if isinstance(exc, blocks.InternalCompilerError):
        if exc.source_element is not None:
            print(f'Internal compiler error while compiling line {exc.source_element.source_position.start_line}: {exc.inner_exception}')
        else:
            print(f'Internal compiler error: {exc.inner_exception}')
    else:
        print(f'Error in {exc.source_element.source_position.filename}:{exc.source_element.source_position.start_line}: {exc}')
    if exc.source_element is not None:
        print(exc.get_context())
    if isinstance(exc, blocks.InternalCompilerError):
        traceback.print_exception(exc.inner_exception)
    elif always_print_stacktrace:
        traceback.print_exception(exc)
    for hint in exc.hints:
        print(f'Hint: {hint.source_element.source_position.filename}:{hint.source_element.source_position.start_line}: {hint}')
        print(hint.get_context())
    return None


def compile_files(input_filenames, *, add_std=True):
    if add_std:
        std_lib = [
            'std',
            'time',
            'knx',
            'abb_jra',
            'mqtt',
            'csv',
            'two_n',
            'gpio',
            'memory',
            'ext_prog',
        ]
        input_filenames = [os.path.dirname(os.path.dirname(__file__)) + '/lib/' + name + '.wrl' for name in std_lib] + input_filenames

    filename_inpudatas = []
    for input_filename in input_filenames:
        with open(input_filename, 'r') as file:
            filename_inpudatas.append((input_filename, file.read()))
    return compile_strs(filename_inpudatas)
