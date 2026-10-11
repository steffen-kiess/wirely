import sys
import os
import importlib
import contextlib
import time
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
from dataclasses import dataclass
from typing import TYPE_CHECKING

import lark

import wirely
from wirely.compiler import model
from wirely.compiler import program as program_compiler
from wirely.compiler.diagnostics import print_diagnostic
from wirely.compiler.function_implementations.test import TEST_RESOURCES_SERVICE
from wirely.util import check_parameters

if TYPE_CHECKING:
    from wirely.two_n.fake_server import ServerInfo

# TODO: Where should this import be done?
import wirely.compiler
from wirely.runtime.engine import Engine

test_names = [
    'unit/runtime_program',
    'unit/compiler_api',
    'unit/native_interface',
    'unit/procedures',
    'unit/instructions',
    'unit/bitwise',
    'unit/native_values',
    'unit/test_resources',
    'unit/library_configuration',
    'unit/state_connections',
    'unit/static_sources',
    'unit/types',
    'unit/serialization',
    'unit/resources',
    'unit/equality',
    'unit/negative_cases',
    'unit/editing',
    'unit/outline',
    'unit/analysis',
    'unit/editor',
    'unit/editor_widgets',
    'unit/editor_workspace',
    'unit/editor_export',
    'unit/control',
    'unit/shuffle',
    'unit/container',
    'unit/engine_wakeup',
    'unit/monotonic_timestamp',
    'wrl/delay',
    'wrl/std',
    'wrl/and',
    'wrl/multi_forward',
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

to_compile = [
    # 'wirely/lib/std.wrl',
    'examples/test.wrl',
    'examples/knx.wrl',
    'examples/delay.wrl',
    'examples/flipflop.wrl',
    'examples/gpio.wrl',
    'test/wrl/simple.wrl',
    'test/compiler/assertions.wrl',
    'test/compiler/attributes.wrl',
    'test/compiler/constants.wrl',
    'test/compiler/connection_order.wrl',
    'test/compiler/static_sources.wrl',
    'test/compiler/type_stages.wrl',
    'test/compiler/equality.wrl',
    'test/compiler/ordering.wrl',
    'test/compiler/arithmetic.wrl',
    'test/compiler/bitwise.wrl',
    'test/compiler/conversions.wrl',
    'test/compiler/durations.wrl',
    'test/compiler/options.wrl',
    'test/compiler/structs.wrl',
    'test/compiler/time_of_day.wrl',
    'test/compiler/library_settings.wrl',
    'test/compiler/literals.wrl',
    'test/compiler/namespaces.wrl',
    'test/compiler/native_functions.wrl',
    'test/compiler/parameters.wrl',
    'test/compiler/path.wrl',
    'test/compiler/prepared_configuration.wrl',
    'test/compiler/syntax.wrl',
    'test/compiler/blocks.wrl',
    'test/compiler/multi_procedures.wrl',
    'test/compiler/types.wrl',
    'test/compiler/runtime_limits.wrl',
]

negative_tests = [
    'test/negative/library_settings.wrl',
    'test/negative/prepared_configuration.wrl',
    'test/negative/runtime_types.wrl',
    'test/negative/runtime_limits.wrl',
    'test/negative/parameters.wrl',
    'test/negative/optional_forwarding.wrl',
    'test/negative/optional_outputs.wrl',
    'test/negative/constants.wrl',
    'test/negative/arithmetic.wrl',
    'test/negative/bitwise.wrl',
    'test/negative/connections.wrl',
    'test/negative/conversions.wrl',
    'test/negative/durations.wrl',
    'test/negative/time_of_day.wrl',
    'test/negative/options.wrl',
    'test/negative/type_comparisons.wrl',
    'test/negative/assertions.wrl',
    'test/negative/native_procedures.wrl',
    'test/negative/native_values.wrl',
    'test/negative/native_functions.wrl',
    'test/negative/path.wrl',
    'test/negative/procedures.wrl',
    'test/negative/blocks.wrl',
    'test/negative/multi_procedures.wrl',
    'test/negative/native_initializers.wrl',
    'test/negative/procedure_ports.wrl',
    'test/negative/resource_declarations.wrl',
    'test/negative/resource_value_types.wrl',
    'test/negative/resource_connections.wrl',
    'test/negative/monotonic_time.wrl',
    'test/negative/node_declarations.wrl',
    'test/negative/option_literals.wrl',
    'test/negative/option_properties.wrl',
    'test/negative/unit_literals.wrl',
    'test/negative/integer_literals.wrl',
    'test/negative/string_literals.wrl',
    'test/negative/node_types.wrl',
    'test/negative/enum_literals.wrl',
    'test/negative/enum_declarations.wrl',
    'test/negative/structs.wrl',
    'test/negative/state_connections.wrl',
    'test/negative/attributes.wrl',
    'test/negative/syntax_lists.wrl',
    'test/negative/weak_keywords.wrl',
]

annotation_pattern = re.compile(r'\s*(\^*)([EHW]):\s*(.*?)\s*$')
shuffle_annotation_pattern = re.compile(r'\s*S([EHW]):\s*(.*?)\s*$')
library_metadata_pattern = re.compile(r'[ \t]*//[ \t]+LIBRARIES[ \t]*:[ \t]*(.*?)[ \t]*')
library_metadata_prefix_pattern = re.compile(r'[ \t]*//[ \t]+LIBRARIES\b')
case_marker_pattern = re.compile(r'[ \t]*//[ \t]+TEST(?:[ \t]*:[ \t]*(.*?))?[ \t]*')


def expected_diagnostics(source, *, start_line=1, end_line=None, shuffled=False):
    expected = []
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


def negative_test_libraries(lines, *, start_line):
    libraries = None
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


def negative_test_cases(source):
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


def compile_negative_case(full_fn, case):
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

    diagnostics = []
    sources = model.compiler._standard_library_sources(case.libraries)
    result = model.compiler.compile_strs(
        sources + [(full_fn, case.source)], add_std=False, on_diagnostic=diagnostics.append,
    )
    if result is not None:
        result = program_compiler.compile(result, on_diagnostic=diagnostics.append)

    output = io.StringIO()
    for diagnostic in diagnostics:
        print_diagnostic(diagnostic, file=output, traceback_file=output)
    messages = output.getvalue()
    if any(diagnostic.kind == 'internal_error' for diagnostic in diagnostics):
        raise AssertionError(f'compiler failed internally:\n{messages}')

    actual = []
    for diagnostic in diagnostics:
        if diagnostic.kind == 'info':
            continue
        kind = {'error': 'E', 'syntax_error': 'E', 'hint': 'H', 'warning': 'W'}[diagnostic.kind]
        message = diagnostic.message
        if diagnostic.kind == 'syntax_error':
            message = 'Syntax error: ' + message.splitlines()[0]
        filename = None if diagnostic.filename is None else os.path.abspath(diagnostic.filename)
        actual.append((kind, filename, diagnostic.line, message))

    matched = {}

    def match_expectation(expected_index, seen):
        kind, line_number, fragment = expected[expected_index]
        for actual_index, item in enumerate(actual):
            location_matches = (isinstance(item[2], int)
                                and case.start_line <= item[2] <= case.end_line
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
    if result is not None or missing or unexpected or not any(item[0] == 'E' for item in actual):
        raise AssertionError(
            f'diagnostic mismatch; missing={missing}, unexpected={unexpected}, '
            f'compiled={result is not None}\n{messages}'
        )


def compile_negative_test(fn, *, verbose=True):
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


def compile_test(fn):
    print(f'Compiling {fn}')

    full_fn = os.path.dirname(__file__) + '/../' + fn
    global_node_type = model.compiler.compile_files([full_fn])
    if global_node_type is None:
        sys.exit(1)

    prog = program_compiler.compile(global_node_type)
    if prog is None:
        sys.exit(1)

    with open(os.devnull, 'w') as file:
        prog.dump(file=file)

    eng = Engine(prog)

    print(f'Compiling {fn} done')


@contextlib.asynccontextmanager
async def with_maybe_knxd(socket_path):
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
async def with_maybe_mqtt(socket_path):
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
async def with_maybe_twon(server):
    if server is None:
        yield None
        return

    async with server.run() as info:
        yield info


class TestResourceProvider:
    """Allocate fixtures for one compilation and test execution."""

    def __init__(self, exit_stack):
        self.exit_stack = exit_stack
        self.knx_path = None
        self.mqtt_socket_path = None
        self.output_directory = None
        self.twon_server = None

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


async def run_test(test_name):
    print(f'Running test {test_name}')

    with contextlib.ExitStack() as resource_stack:
        resources = TestResourceProvider(resource_stack)
        fn = os.path.dirname(__file__) + f'/../test/{test_name}.wrl'
        py_fn = os.path.dirname(__file__) + f'/../test/{test_name}.py'
        global_node_type = model.compiler.compile_files(
            [fn], services={TEST_RESOURCES_SERVICE: resources},
        )
        if global_node_type is None:
            sys.exit(1)

        prog = program_compiler.compile(global_node_type)
        if prog is None:
            sys.exit(1)

        with open(os.devnull, 'w') as file:
            prog.dump(file=file)

        if resources.knx_path is not None:
            print('Test requires KNX')

        async with with_maybe_knxd(resources.knx_path), with_maybe_mqtt(resources.mqtt_socket_path), with_maybe_twon(resources.twon_server) as twon_info:
            eng = Engine(prog)

            # TODO: Pass this information differently?
            eng.test_twon_fake_server_info = twon_info

            # eng.debug = True

            # https://docs.python.org/3/library/importlib.html#importing-a-source-file-directly
            spec = importlib.util.spec_from_file_location('test', py_fn)
            if spec is None:
                raise Exception('Unable to load python file ' + repr(py_fn))
            module = importlib.util.module_from_spec(spec)

            module.engine = eng

            # sys.modules['__main__'] = module
            spec.loader.exec_module(module)
            # TODO: Always require a run_test coroutine?
            if 'run_test' in module.__dict__:
                await module.run_test()

    print(f'Done running test {test_name}')


def main():
    parser = argparse.ArgumentParser(description='Run Wirely tests')
    parser.add_argument('--timings', action='store_true',
                        help='List test durations, slowest first, at the end')
    parser.add_argument('names', nargs='*', metavar='TEST',
                        help='Test names or fixture paths (default: all tests)')
    args = parser.parse_intermixed_args()
    names = args.names
    for name in names:
        if name not in to_compile and name not in negative_tests and name not in test_names:
            raise Exception(f'Unknown test name {name!r}')
    if len(names) == 0:
        names = None
    else:
        names = set(names)

    durations = []
    groups = [(to_compile, compile_test), (negative_tests, compile_negative_test),
              (test_names, lambda name: asyncio.run(run_test(name)))]
    try:
        for group, run in groups:
            for name in group:
                if names is not None and name not in names:
                    continue
                if not args.timings:
                    run(name)
                    continue
                start = time.perf_counter()
                try:
                    run(name)
                finally:
                    durations.append((name, time.perf_counter() - start))
    finally:
        if args.timings:
            print('\nTest durations (slowest first):', flush=True)
            for name, elapsed in sorted(durations, key=lambda item: (-item[1], item[0])):
                print(f'  {elapsed:.3f}s  {name}', flush=True)


if __name__ == '__main__':
    main()
