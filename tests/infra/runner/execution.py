"""Running one test of each kind."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import io
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from wirely.compiler import model
from wirely.compiler import program as program_compiler
from wirely.compiler.diagnostics import print_diagnostic
from wirely.compiler.function_implementations.test import TEST_RESOURCES_SERVICE
from tests.infra.support import test_engine
from .diagnostics import (
    NegativeTestCase, diagnostic_items, expected_diagnostics, match_diagnostics, negative_test_cases,
)
from .registry import example_tests, negative_tests, python_only_tests, to_compile
from .services import TestResourceProvider, with_maybe_knxd, with_maybe_mqtt, with_maybe_twon

if TYPE_CHECKING:
    from wirely.compiler.diagnostics import Diagnostic
    from wirely.program import Program
    from wirely.runtime.engine import Engine


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def compile_checked(fn: str, *, services: Mapping[str, object] | None = None) -> Program:
    """Compile fn, which must warn exactly as its W: annotations expect."""
    full_fn = str(REPOSITORY_ROOT / fn)
    with open(full_fn, encoding='utf-8') as file:
        source = file.read()
    line_count = len(source.splitlines())
    expected = expected_diagnostics(source)
    shuffled_expected = expected_diagnostics(source, shuffled=True)
    relaxed = bool(os.environ.get('WIRELY_COMPILER_SHUFFLE_SEED') and shuffled_expected)
    if relaxed:
        expected = shuffled_expected
    if any(kind != 'W' for kind, _, _ in expected):
        raise AssertionError(f'{fn}: only warning annotations are allowed outside negative tests')

    # Diagnostics are printed only on failure, so expected warnings stay silent.
    diagnostics: list[Diagnostic] = []
    from tests.infra.native_extension.generated import extension
    global_node_type = model.compiler.compile_files([full_fn], services=services, on_diagnostic=diagnostics.append,
                                                    extensions=[extension])
    prog = None if global_node_type is None else program_compiler.compile(global_node_type,
                                                                          on_diagnostic=diagnostics.append)
    if prog is None:
        for diagnostic in diagnostics:
            print_diagnostic(diagnostic)
        raise AssertionError(f'{fn}: compilation failed')
    warnings = [item for item in diagnostic_items(diagnostics) if item[0] == 'W']
    missing, unexpected = match_diagnostics(expected, warnings, full_fn, 1, line_count, relaxed=relaxed)
    if missing or unexpected:
        for diagnostic in diagnostics:
            print_diagnostic(diagnostic)
        raise AssertionError(f'{fn}: warning mismatch; missing={missing}, unexpected={unexpected}')
    return prog


def compile_negative_case(full_fn: str, case: NegativeTestCase) -> None:
    if case.label == '':
        raise AssertionError('empty test label')
    expected = expected_diagnostics(
        case.source, start_line=case.start_line, end_line=case.end_line,
    )
    if not expected:
        raise AssertionError('no expected diagnostics')
    shuffled_expected = expected_diagnostics(
        case.source, start_line=case.start_line, end_line=case.end_line, shuffled=True,
    )
    relaxed = bool(os.environ.get('WIRELY_COMPILER_SHUFFLE_SEED') and shuffled_expected)
    if relaxed:
        expected = shuffled_expected

    diagnostics: list = []
    sources = model.compiler.standard_library_sources(case.libraries)
    graph = model.compiler.compile_strs(
        sources + [(full_fn, case.source)], add_std=False, on_diagnostic=diagnostics.append,
    )
    result = (program_compiler.compile(graph, on_diagnostic=diagnostics.append)
              if graph is not None else None)

    output = io.StringIO()
    for diagnostic in diagnostics:
        print_diagnostic(diagnostic, file=output, traceback_file=output)
    messages = output.getvalue()
    if any(diagnostic.kind == 'internal_error' for diagnostic in diagnostics):
        raise AssertionError(f'compiler failed internally:\n{messages}')

    actual = diagnostic_items(diagnostics)
    missing, unexpected = match_diagnostics(expected, actual, full_fn, case.start_line, case.end_line,
                                            relaxed=relaxed)
    if result is not None or missing or unexpected or not any(item[0] == 'E' for item in actual):
        raise AssertionError(
            f'diagnostic mismatch; missing={missing}, unexpected={unexpected}, '
            f'compiled={result is not None}\n{messages}'
        )


def compile_negative_test(fn: str, *, verbose: bool = True) -> None:
    if verbose:
        print(f'Compiling negative test {fn}')
    full_fn = str(REPOSITORY_ROOT / fn)
    with open(full_fn, encoding='utf-8') as file:
        cases = list(negative_test_cases(file.read()))
    failures = []
    for case in cases:
        try:
            compile_negative_case(full_fn, case)
        except AssertionError as error:
            name = case.label or 'case'
            failures.append(f'{fn}: {name} (line {case.start_line}): {error}')
    if failures:
        raise AssertionError('\n\n'.join(failures))
    if verbose:
        print(f'Compiling negative test {fn} done')


def compile_test(fn: str) -> None:
    print(f'Compiling {fn}')

    prog = compile_checked(fn)

    with open(os.devnull, 'w') as file:
        prog.dump(file=file)

    eng = test_engine(prog)

    print(f'Compiling {fn} done')


async def run_python_test(filename: str, *, engine: Engine | None = None) -> None:
    spec = importlib.util.spec_from_file_location('test', filename)
    if spec is None or spec.loader is None:
        raise ImportError(f'Unable to load Python test {filename!r}')
    module = importlib.util.module_from_spec(spec)
    if engine is not None:
        setattr(module, 'engine', engine)
    spec.loader.exec_module(module)
    await module.run_test()


async def run_python_only_test(test_name: str) -> None:
    print(f'Running test {test_name}')
    filename = REPOSITORY_ROOT / 'tests' / (test_name + '.py')
    await run_python_test(str(filename))
    print(f'Done running test {test_name}')


async def run_runtime_test(test_name: str) -> None:
    print(f'Running test {test_name}')

    with contextlib.ExitStack() as resource_stack:
        resources = TestResourceProvider(resource_stack)
        py_fn = str(REPOSITORY_ROOT / 'tests' / f'{test_name}.py')
        prog = compile_checked(f'tests/{test_name}.wrl', services={TEST_RESOURCES_SERVICE: resources})

        with open(os.devnull, 'w') as file:
            prog.dump(file=file)

        if resources.knx_path is not None:
            print('Test requires KNX')

        async with with_maybe_knxd(resources.knx_path), with_maybe_mqtt(resources.mqtt_socket_path), with_maybe_twon(resources.twon_server) as twon_info:
            eng = test_engine(prog)

            # TODO: Pass this information differently?
            eng.test_twon_fake_server_info = twon_info

            # eng.debug = True

            await run_python_test(py_fn, engine=eng)

    print(f'Done running test {test_name}')


def run_named_test(name: str, *, bytecode_roundtrip: bool = False) -> None:
    from tests.infra.bytecode import bytecode_roundtrip_mode
    token = bytecode_roundtrip_mode.set(bytecode_roundtrip)
    try:
        _run_named_test(name)
    finally:
        bytecode_roundtrip_mode.reset(token)


def _run_named_test(name: str) -> None:
    if name in example_tests:
        compile_test(name + '.wrl')
    elif name in to_compile:
        compile_test('tests/' + name + '.wrl')
    elif name in negative_tests:
        compile_negative_test('tests/' + name + '.wrl')
    elif name in python_only_tests:
        asyncio.run(run_python_only_test(name))
    else:
        asyncio.run(run_runtime_test(name))
