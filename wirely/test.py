from __future__ import annotations

import sys
import os
import importlib
import contextlib
import time
import traceback
import tempfile
import subprocess
import socket
import asyncio
import io
import re
import pwd
import shlex
import signal
import argparse
import dataclasses
import itertools
import types
from dataclasses import dataclass
from pathlib import Path
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

import lark

import wirely
from wirely.compiler import model
from wirely.compiler import program as program_compiler
from wirely.compiler.diagnostics import print_diagnostic
from wirely.compiler.function_implementations.test import TEST_RESOURCES_SERVICE
from wirely.util import check_parameters

if TYPE_CHECKING:
    from wirely.compiler.diagnostics import Diagnostic
    from wirely.program import Program
    from wirely.two_n.fake_server import Server, ServerInfo

# An expected diagnostic (kind, line, message fragment) and a reported one (kind, filename, line, message).
ExpectedDiagnostic = tuple[str, int, str]
DiagnosticItem = tuple[str, 'str | None', 'int | None', str]

# TODO: Where should this import be done?
import wirely.compiler
from wirely.runtime.engine import Engine

python_only_tests = [
    'misc/grammar',
    'misc/type_check',
    'unit/check_parameters',
    'unit/runtime_program',
    'unit/compiler_api',
    'unit/compiler_warnings',
    'unit/native_interface',
    'unit/native_contracts',
    'unit/instructions',
    'unit/test_resources',
    'unit/test_runner',
    'repo/files',
    'repo/native_types',
    'unit/library_configuration',
    'unit/csv',
    'unit/state_connections',
    'unit/types',
    'unit/serialization',
    'unit/resources',
    'unit/negative_cases',
    'unit/editing',
    'unit/outline',
    'unit/analysis',
    'unit/editor',
    'unit/editor_widgets',
    'unit/editor_workspace',
    'unit/editor_export',
    'unit/shuffle',
    'unit/container',
    'unit/monotonic_timestamp',
]

runtime_tests = [
    'unit/procedures',
    'unit/native_values',
    'unit/static_sources',
    'unit/control',
    'unit/engine_wakeup',
    'wrl/bitwise',
    'wrl/length',
    'wrl/equality',
    'wrl/delay',
    'wrl/std',
    'wrl/and',
    'wrl/multi_forward',
    'wrl/events',
    'wrl/event_bindings',
    'wrl/changed',
    'wrl/option',
    'wrl/gpio',
    'wrl/knx',
    'wrl/abb',
    'wrl/mqtt',
    'wrl/time',
    'wrl/csv',
    'wrl/knx_time',
    'wrl/flipflop',
    'wrl/two_n',
    'wrl/process',
]

example_tests = [
    'examples/test',
    'examples/knx',
    'examples/delay',
    'examples/flipflop',
    'examples/gpio',
]

to_compile = [
    'compiler/simple',
    'compiler/assertions',
    'compiler/attributes',
    'compiler/constants',
    'compiler/constant_expressions',
    'compiler/connection_order',
    'compiler/static_sources',
    'compiler/type_stages',
    'compiler/equality',
    'compiler/ordering',
    'compiler/arithmetic',
    'compiler/bitwise',
    'compiler/conversions',
    'compiler/durations',
    'compiler/options',
    'compiler/structs',
    'compiler/time_of_day',
    'compiler/library_settings',
    'compiler/literals',
    'compiler/namespaces',
    'compiler/native_functions',
    'compiler/parameters',
    'compiler/path',
    'compiler/prepared_configuration',
    'compiler/syntax',
    'compiler/blocks',
    'compiler/multi_procedures',
    'compiler/events',
    'compiler/bindings',
    'compiler/changed',
    'compiler/warnings',
    'compiler/warnings_allowed',
    'compiler/length',
    'compiler/types',
    'compiler/runtime_limits',
]

negative_tests = [
    'negative/library_settings',
    'negative/prepared_configuration',
    'negative/runtime_types',
    'negative/runtime_limits',
    'negative/parameters',
    'negative/optional_forwarding',
    'negative/optional_outputs',
    'negative/constants',
    'negative/constant_expressions',
    'negative/arithmetic',
    'negative/bitwise',
    'negative/connections',
    'negative/conversions',
    'negative/durations',
    'negative/time_of_day',
    'negative/options',
    'negative/type_comparisons',
    'negative/assertions',
    'negative/native_procedures',
    'negative/native_values',
    'negative/native_functions',
    'negative/path',
    'negative/procedures',
    'negative/blocks',
    'negative/multi_procedures',
    'negative/length',
    'negative/native_initializers',
    'negative/procedure_ports',
    'negative/changed',
    'negative/resource_declarations',
    'negative/resource_value_types',
    'negative/resource_connections',
    'negative/monotonic_time',
    'negative/node_declarations',
    'negative/bindings',
    'negative/option_literals',
    'negative/option_properties',
    'negative/unit_literals',
    'negative/integer_literals',
    'negative/string_literals',
    'negative/node_types',
    'negative/enum_literals',
    'negative/enum_declarations',
    'negative/structs',
    'negative/state_connections',
    'negative/attributes',
    'negative/syntax_lists',
    'negative/weak_keywords',
]

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
            if name not in model.compiler._STANDARD_LIBRARY_NAMES:
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


def compile_checked(fn: str, *, services: Mapping[str, object] | None = None) -> Program:
    """Compile fn, which must warn exactly as its W: annotations expect."""
    full_fn = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', fn))
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

    diagnostics = []

    def report(diagnostic: Diagnostic) -> None:
        diagnostics.append(diagnostic)
        print_diagnostic(diagnostic)

    global_node_type = model.compiler.compile_files([full_fn], services=services, on_diagnostic=report)
    prog = None if global_node_type is None else program_compiler.compile(global_node_type,
                                                                          on_diagnostic=report)
    if prog is None:
        sys.exit(1)
    warnings = [item for item in diagnostic_items(diagnostics) if item[0] == 'W']
    missing, unexpected = match_diagnostics(expected, warnings, full_fn, 1, line_count, relaxed=relaxed)
    if missing or unexpected:
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
    sources = model.compiler._standard_library_sources(case.libraries)
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
    full_fn = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', fn))
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


_test_contract_modules = itertools.count()


@contextlib.contextmanager
def test_contracts(graph: object) -> Iterator[types.ModuleType]:
    """Register the signatures and contracts of a program's TestLibrary native interfaces.

    Interfaces declared like in wirely/native/test_library.wrl keep their generated contracts.
    Contracts of the others are generated into the returned module, which exists while the
    context is active; implementations derive from its classes.
    """
    from unittest.mock import patch
    from wirely.compiler import native_contracts
    from wirely.compiler.native_signatures import native_signatures
    from wirely.runtime import native_interfaces

    tables: tuple[dict[str, Any], ...] = (native_interfaces.native_state_types, native_interfaces.native_procedures,
                                          native_interfaces.native_functions)
    # Some tests supply graph stand-ins with the same interface.
    declared = native_signatures(cast(model.Scope, graph), 'TestLibrary')
    generated = [{name: entry for name, entry in cast(Mapping[str, Any], entries).items() if table.get(name) != entry}
                 for table, entries in zip(tables, declared)]
    module = types.ModuleType(f'_wirely_test_contracts_{next(_test_contract_modules)}')
    interfaces = cast(native_contracts.Interfaces, tuple(generated))
    sources = native_contracts.contract_sources(
        interfaces, resource_classes=native_contracts.resource_classes(), strict=False)
    located = native_contracts.with_contracts(interfaces)
    with contextlib.ExitStack() as stack:
        # Patching sys.modules as a whole would also remove modules imported meanwhile.
        sys.modules[module.__name__] = module
        stack.callback(sys.modules.pop, module.__name__, None)
        for source in sources.values():
            exec(compile(source, f'<{module.__name__}>', 'exec'), module.__dict__)
        for table, located_entries in zip(tables, located):
            stack.enter_context(patch.dict(table, {
                name: dataclasses.replace(entry, contract=module.__name__ + ':' + entry.contract.partition(':')[2])
                for name, entry in cast(Mapping[str, Any], located_entries).items()}))
        yield module


def test_engine(program: Program, graph: object = None) -> Engine:
    """Create an Engine for a program whose TestLibrary native code is declared in graph or program.graph."""
    with test_contracts(program.graph if graph is None else graph):
        return Engine(program)


def compile_test(fn: str) -> None:
    print(f'Compiling {fn}')

    prog = compile_checked(fn)

    with open(os.devnull, 'w') as file:
        prog.dump(file=file)

    with test_contracts(prog.graph):
        eng = Engine(prog)

    print(f'Compiling {fn} done')


@contextlib.asynccontextmanager
async def with_maybe_knxd(socket_path: str | None) -> AsyncIterator[str | None]:
    if socket_path is None:
        yield None
        return

    knxd_path = os.environ.get('KNXD_PATH', 'knxd')

    config_path = os.path.join(os.path.dirname(socket_path), 'knxd.ini')

    config_data = f'''
[main]
addr = 1.1.5
client-addrs = 1.1.100:100
connections = A.dummy,server_unix
# systemd = systemd
debug = debug

[A.dummy]
driver = dummy

[server_unix]
server = unixtunsrv
debug = debug-server
tunnel = tunnel
path = {socket_path}
'''

    with open(config_path, 'w', encoding='utf-8') as file:
        file.write(config_data)

    print('Starting knxd...', flush=True)
    # TODO: Avoid non-async waiting?
    process = subprocess.Popen([knxd_path, config_path])
    try:
        print('Waiting for knxd to start up...', flush=True)
        for i in range(100):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError) as e:
                exit_code = process.poll()
                if exit_code is not None:
                    raise Exception(f'knxd has exited with exit code {exit_code}')

                print('Waiting', flush=True)
                await asyncio.sleep(0.05)
        else:
            raise TimeoutError('Timed out waiting for knxd to start')
        print('knxd is running.', flush=True)

        monitor_command = os.environ.get('WIRELY_TEST_KNX_MONITOR')
        if not monitor_command:
            yield socket_path
        else:
            print('Starting monitor...', flush=True)
            command = monitor_command.replace('%s', shlex.quote(socket_path))
            monitor_process = subprocess.Popen(
                ['sh', '-c', command], start_new_session=True
            )
            try:
                print('Waiting for monitor...', flush=True)
                # TODO: Find out whether it is running somehow?
                # await asyncio.sleep(0.1)
                # TODO: This is just a guess, with new xknx version (3.x.x) 0.1s seems to be too short
                await asyncio.sleep(0.3)
                # print('monitor is running.', flush=True)

                yield socket_path
            finally:
                print('Stopping monitor...', flush=True)
                try:
                    os.killpg(monitor_process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                monitor_process.wait()
                print('Done stopping monitor.', flush=True)
    finally:
        print('Stopping knxd...', flush=True)
        process.kill()
        process.wait()
        print('Done stopping knxd.', flush=True)


@contextlib.asynccontextmanager
async def with_maybe_mqtt(socket_path: str | None) -> AsyncIterator[str | None]:
    if socket_path is None:
        yield None
        return

    mosquitto_path = 'mosquitto'

    tmp = os.path.dirname(socket_path)
    config_path = os.path.join(tmp, 'mosquitto.conf')

    config_data = f'''
pid_file {tmp}/mosquitto.pid
persistence false
log_dest file {tmp}/mosquitto.log
listener 0 {socket_path}
allow_anonymous true
'''

    with open(config_path, 'w', encoding='utf-8') as file:
        file.write(config_data)

    if os.geteuid() == 0:
        # Mosquitto drops root privileges before writing in this directory.
        try:
            broker_user = pwd.getpwnam('mosquitto')
        except KeyError:
            broker_user = pwd.getpwnam('nobody')
        os.chown(tmp, broker_user.pw_uid, broker_user.pw_gid)
        os.chown(config_path, broker_user.pw_uid, broker_user.pw_gid)

    print('Starting mosquitto...', flush=True)
    # TODO: Avoid non-async waiting?
    process = subprocess.Popen([mosquitto_path, '-c', config_path])
    try:
        print('Waiting for mosquitto to start up...', flush=True)
        for i in range(100):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(socket_path)
                    break
            except (ConnectionRefusedError, FileNotFoundError) as e:
                exit_code = process.poll()
                if exit_code is not None:
                    raise Exception(f'mosquitto has exited with exit code {exit_code}')

                print('Waiting', flush=True)
                await asyncio.sleep(0.05)
        else:
            raise TimeoutError('Timed out waiting for mosquitto to start')
        print('mosquitto is running.', flush=True)

        run_mqtt_sub = model.util.getEnvironmentBool('WIRELY_TEST_RUN_MQTT_SUB', False)
        if not run_mqtt_sub:
            yield socket_path
        else:
            print('Starting monitor...', flush=True)
            monitor_process = subprocess.Popen(['mosquitto_sub', '--unix', socket_path, '-v', '-t', '#'])
            try:
                print('Waiting for monitor...', flush=True)
                # TODO: Find out whether it is running somehow?
                await asyncio.sleep(0.3)
                # print('monitor is running.', flush=True)

                yield socket_path
            finally:
                print('Stopping monitor...', flush=True)
                monitor_process.kill()
                monitor_process.wait()
                print('Done stopping monitor.', flush=True)
    finally:
        print('Stopping mosquitto...', flush=True)
        process.kill()
        process.wait()
        print('Done stopping mosquitto.', flush=True)


@contextlib.asynccontextmanager
async def with_maybe_twon(server: Server | None) -> AsyncIterator[ServerInfo | None]:
    if server is None:
        yield None
        return

    async with server.run() as info:
        yield info


class TestResourceProvider:
    """Allocate fixtures for one compilation and test execution."""

    def __init__(self, exit_stack: contextlib.ExitStack) -> None:
        self.exit_stack = exit_stack
        self.knx_path: str | None = None
        self.mqtt_socket_path: str | None = None
        self.output_directory: str | None = None
        self.twon_server: Server | None = None

    def get_knx_path(self) -> str:
        if self.knx_path is None:
            directory = self.exit_stack.enter_context(
                tempfile.TemporaryDirectory(prefix='test_knxd.'),
            )
            self.knx_path = os.path.join(directory, 'knxnet')
        return self.knx_path

    def get_mqtt_uri(self) -> str:
        if self.mqtt_socket_path is None:
            directory = self.exit_stack.enter_context(
                tempfile.TemporaryDirectory(prefix='test_mosquitto.'),
            )
            self.mqtt_socket_path = os.path.join(directory, 'socket')
        return 'mqtt+unix://' + self.mqtt_socket_path

    def get_output_path(self, default: str) -> str:
        if self.output_directory is None:
            keep_directory = model.util.getEnvironmentBool('WIRELY_TEST_KEEP_TESTDIR', False)
            if keep_directory:
                self.output_directory = tempfile.mkdtemp(prefix='test_dir.')
                print(f"Created test dir in {self.output_directory} (won't be deleted)", flush=True)
            else:
                self.output_directory = self.exit_stack.enter_context(
                    tempfile.TemporaryDirectory(prefix='test_dir.'),
                )
                self.exit_stack.callback(print, 'Cleaning up test dir', flush=True)
                print(f'Created test dir in {self.output_directory}', flush=True)
        return os.path.join(self.output_directory, os.path.basename(default))

    def get_twon_info(self) -> 'ServerInfo':
        if self.twon_server is None:
            from wirely.two_n.fake_server import Server

            server = Server()
            self.exit_stack.callback(server.close)
            self.twon_server = server
        return self.twon_server.info


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
    filename = Path(__file__).resolve().parent.parent / 'test' / (test_name + '.py')
    await run_python_test(str(filename))
    print(f'Done running test {test_name}')


async def run_runtime_test(test_name: str) -> None:
    print(f'Running test {test_name}')

    with contextlib.ExitStack() as resource_stack:
        resources = TestResourceProvider(resource_stack)
        py_fn = os.path.dirname(__file__) + f'/../test/{test_name}.py'
        prog = compile_checked(f'test/{test_name}.wrl', services={TEST_RESOURCES_SERVICE: resources})

        with open(os.devnull, 'w') as file:
            prog.dump(file=file)

        if resources.knx_path is not None:
            print('Test requires KNX')

        async with with_maybe_knxd(resources.knx_path), with_maybe_mqtt(resources.mqtt_socket_path), with_maybe_twon(resources.twon_server) as twon_info:
            with test_contracts(prog.graph):
                eng = Engine(prog)

            # TODO: Pass this information differently?
            eng.test_twon_fake_server_info = twon_info

            # eng.debug = True

            await run_python_test(py_fn, engine=eng)

    print(f'Done running test {test_name}')


def main() -> None:
    parser = argparse.ArgumentParser(description='Run Wirely tests')
    parser.add_argument('--timings', action='store_true',
                        help='List test durations, slowest first, at the end')
    parser.add_argument('--keep-going', action='store_true',
                        help='Run the remaining tests after a failure and list the failed tests at the end')
    parser.add_argument('names', nargs='*', metavar='TEST',
                        help='Extensionless test names (default: all tests)')
    args = parser.parse_intermixed_args()
    names = args.names
    for name in names:
        if (name not in example_tests and name not in to_compile and name not in negative_tests
                and name not in python_only_tests and name not in runtime_tests):
            raise Exception(f'Unknown test name {name!r}')
    if len(names) == 0:
        names = None
    else:
        names = set(names)

    durations = []
    failed = []

    def run_one(run: Callable[[str], object], name: str) -> None:
        if not args.keep_going:
            run(name)
            return
        try:
            run(name)
        except Exception:
            traceback.print_exc()
            failed.append(name)

    groups = [(example_tests, lambda name: compile_test(name + '.wrl')),
              (to_compile, lambda name: compile_test('test/' + name + '.wrl')),
              (negative_tests, lambda name: compile_negative_test('test/' + name + '.wrl')),
              (python_only_tests, lambda name: asyncio.run(run_python_only_test(name))),
              (runtime_tests, lambda name: asyncio.run(run_runtime_test(name)))]
    try:
        for group, run in groups:
            for name in group:
                if names is not None and name not in names:
                    continue
                if not args.timings:
                    run_one(run, name)
                    continue
                start = time.perf_counter()
                try:
                    run_one(run, name)
                finally:
                    durations.append((name, time.perf_counter() - start))
    finally:
        if args.timings:
            print('\nTest durations (slowest first):', flush=True)
            for name, elapsed in sorted(durations, key=lambda item: (-item[1], item[0])):
                print(f'  {elapsed:.3f}s  {name}', flush=True)
    if failed:
        print('\nFailed tests:', flush=True)
        for name in failed:
            print(f'  {name}', flush=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
