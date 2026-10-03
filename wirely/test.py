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
from dataclasses import dataclass, replace
from types import MappingProxyType

import lark

import wirely
from wirely.compiler import model as blocks
from wirely.compiler import program as program_compiler
from wirely.util import check_parameters

# TODO: Where should this import be done?
import wirely.compiler

test_names = [
    'unit/runtime_program',
    'unit/native_interface',
    'unit/native_procedures',
    'unit/native_parameters',
    'unit/native_configuration',
    'unit/state_connections',
    'unit/types',
    'unit/resources',
    'unit/string_literals',
    'unit/integer_literals',
    'unit/unit_literals',
    'unit/option_literals',
    'unit/enum_literals',
    'unit/enum_declarations',
    'unit/negative_cases',
    'wrl/delay',
    'wrl/std',
    'wrl/and',
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
    'wrl/ext_prog',
]

to_compile = [
    # 'wirely/lib/std.wrl',
    'examples/test.wrl',
    'examples/knx.wrl',
    'examples/delay.wrl',
    'examples/flipflop.wrl',
    'examples/gpio.wrl',
    'test/wrl/simple.wrl',
    'test/unit/parser.wrl',
    'test/unit/compiler.wrl',
    'test/unit/nested_library.wrl',
]

negative_tests = [
    'test/negative/runtime_types.wrl',
    'test/negative/parameters.wrl',
    'test/negative/native_procedures.wrl',
    'test/negative/native_initializers.wrl',
    'test/negative/procedure_bindings.wrl',
    'test/negative/resource_declarations.wrl',
    'test/negative/resource_value_types.wrl',
    'test/negative/resource_connections.wrl',
    'test/negative/option_literals.wrl',
    'test/negative/unit_literals.wrl',
    'test/negative/integer_literals.wrl',
    'test/negative/string_literals.wrl',
    'test/negative/node_types.wrl',
    'test/negative/enum_literals.wrl',
    'test/negative/enum_declarations.wrl',
    'test/negative/state_connections.wrl',
]

diagnostic_pattern = re.compile(
    r'^(Error in |Hint: |Warning in |Warning: )(.+):(\d+): (.+)$'
)
syntax_diagnostic_pattern = re.compile(r'^Error in (.+):(\d+)$')
annotation_pattern = re.compile(r'\s*(\^*)([EHW]):\s*(.*?)\s*$')
case_marker_pattern = re.compile(r'[ \t]*//[ \t]+TEST(?:[ \t]*:[ \t]*(.*?))?[ \t]*')


def expected_diagnostics(source, *, start_line=1, end_line=None):
    expected = []
    for line_number, line in enumerate(source.splitlines(), 1):
        for comment in line.split('//')[1:]:
            match = annotation_pattern.fullmatch(comment)
            if match is None:
                if re.match(r'\s*\^*[EHW]:', comment):
                    raise AssertionError(f'Malformed annotation on line {line_number}: //{comment}')
                continue
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


def negative_test_cases(source):
    lines = source.splitlines(keepends=True)
    markers = []
    for index, line in enumerate(lines):
        match = case_marker_pattern.fullmatch(line.rstrip('\r\n'))
        if match is not None:
            markers.append((index, match.group(1)))
    boundaries = [(-1, None), *markers, (len(lines), None)]
    for (marker, label), (end, _) in zip(boundaries, boundaries[1:]):
        start = marker + 1
        if marker == -1 and markers:
            preamble = ''.join(lines[start:end])
            if not re.search(r'//\s*\^*[EHW]:', preamble):
                try:
                    declarations = blocks.parser.parse_str(preamble, filename='<preamble>')
                except (lark.UnexpectedInput, lark.exceptions.VisitError):
                    pass
                else:
                    if not declarations:
                        continue
        padded = ''.join(line if start <= index < end else '\n'
                         for index, line in enumerate(lines))
        yield NegativeTestCase(label, start + 1, end, padded)


def compile_negative_case(full_fn, case):
    if case.label == '':
        raise AssertionError('empty test label')
    expected = expected_diagnostics(
        case.source, start_line=case.start_line, end_line=case.end_line,
    )
    if not expected:
        raise AssertionError('no expected diagnostics')

    output = io.StringIO()
    errors = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        result = blocks.compiler.compile_strs([(full_fn, case.source)], add_std=True)
        if result is not None:
            result = program_compiler.compile(result)

    stdout = output.getvalue()
    stderr = errors.getvalue()
    if stderr or 'Internal compiler error' in stdout or 'Traceback (most recent call last)' in stdout:
        raise AssertionError(f'compiler failed internally:\n{stdout}{stderr}')

    actual = []
    output_lines = stdout.splitlines()
    for index, line in enumerate(output_lines):
        match = diagnostic_pattern.fullmatch(line)
        if match is not None:
            prefix, filename, line_number, message = match.groups()
            kind = {'Error in ': 'E', 'Hint: ': 'H',
                    'Warning in ': 'W', 'Warning: ': 'W'}[prefix]
            actual.append((kind, os.path.abspath(filename), int(line_number), message))
        elif (match := syntax_diagnostic_pattern.fullmatch(line)) is not None:
            if index + 1 == len(output_lines) or not output_lines[index + 1].strip():
                raise AssertionError(f'malformed syntax diagnostic: {line!r}')
            filename, line_number = match.groups()
            message = 'Syntax error: ' + output_lines[index + 1]
            actual.append(('E', os.path.abspath(filename), int(line_number), message))
        elif line.startswith(('Error', 'Hint', 'Warning')):
            raise AssertionError(f'malformed diagnostic: {line!r}')

    matched = {}

    def match_expectation(expected_index, seen):
        kind, line_number, fragment = expected[expected_index]
        for actual_index, item in enumerate(actual):
            if (actual_index in seen or item[0] != kind or item[1] != full_fn
                    or item[2] != line_number or fragment not in item[3]):
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
    if result is not None or missing or unexpected:
        raise AssertionError(
            f'diagnostic mismatch; missing={missing}, unexpected={unexpected}, '
            f'compiled={result is not None}\n{stdout}'
        )


def compile_negative_test(fn):
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
    print(f'Compiling negative test {fn} done')


def compile_test(fn):
    print(f'Compiling {fn}')

    full_fn = os.path.dirname(__file__) + '/../' + fn
    global_block_type = blocks.compiler.compile_files([full_fn])
    if global_block_type is None:
        sys.exit(1)

    prog = program_compiler.compile(global_block_type)
    if prog is None:
        sys.exit(1)

    with open(os.devnull, 'w') as file:
        prog.dump(file=file)

    eng = blocks.Engine(prog)

    print(f'Compiling {fn} done')


@contextlib.asynccontextmanager
async def with_maybe_testdir(require_testdir):
    if not require_testdir:
        yield None
        return

    keep_testdir = blocks.util.getEnvironmentBool('WIRELY_TEST_KEEP_TESTDIR', False)

    if keep_testdir:
        tmp = tempfile.mkdtemp(prefix='test_dir.')
        print(f"Created test dir in {tmp} (won't be deleted)", flush=True)
        yield tmp
    else:
        with tempfile.TemporaryDirectory(prefix='test_dir.') as tmp:
            print(f'Created test dir in {tmp}', flush=True)
            try:
                yield tmp
            finally:
                print('Cleaning up test dir', flush=True)


@contextlib.asynccontextmanager
async def with_maybe_knxd(require_knx):
    if not require_knx:
        yield None
        return

    knxd_path = os.environ.get('KNXD_PATH', 'knxd')

    with tempfile.TemporaryDirectory(prefix='test_knxd.') as tmp:
        config_path = tmp + '/knxd.ini'
        socket_path = tmp + '/knxnet'

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
            print('Done stopping knxd.', flush=True)


@contextlib.asynccontextmanager
async def with_maybe_mqtt(require_mqtt):
    if not require_mqtt:
        yield None
        return

    mosquitto_path = 'mosquitto'

    with tempfile.TemporaryDirectory(prefix='test_mosquitto.') as tmp:
        config_path = tmp + '/mosquitto.conf'
        socket_path = tmp + '/socket'

        config_data = f'''
pid_file {tmp}/mosquitto.pid
persistence false
log_dest file {tmp}/mosquitto.log
listener 0 {tmp}/socket
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
            print('mosquitto is running.', flush=True)

            run_mqtt_sub = blocks.util.getEnvironmentBool('WIRELY_TEST_RUN_MQTT_SUB', False)
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
                    print('Done stopping monitor.', flush=True)
        finally:
            print('Stopping mosquitto...', flush=True)
            process.kill()
            print('Done stopping mosquitto.', flush=True)


@contextlib.asynccontextmanager
async def with_maybe_twon(require_twon):
    if not require_twon:
        yield None
        return

    import wirely.two_n.fake_server

    server = wirely.two_n.fake_server.Server()
    async with server.run() as info:
        yield info


async def run_test(test_name):
    print(f'Running test {test_name}')

    fn = os.path.dirname(__file__) + f'/../test/{test_name}.wrl'
    py_fn = os.path.dirname(__file__) + f'/../test/{test_name}.py'
    global_block_type = blocks.compiler.compile_files([fn])
    if global_block_type is None:
        sys.exit(1)

    prog = program_compiler.compile(global_block_type)
    if prog is None:
        sys.exit(1)

    with open(os.devnull, 'w') as file:
        prog.dump(file=file)

    # TODO: Clean up
    require_testdir = False
    require_knx = False
    require_mqtt = False
    require_twon = False
    csv_writer_paths = []
    knx_connection_paths = []
    mqtt_server_paths = []
    twon_device_paths = []
    for node_id, instance in prog.instances.items():
        if instance.type_name == ('CSV', 'ValueWriter'):
            require_testdir = True
            csv_writer_paths.append(node_id)
        if instance.type_name == ('KNX', 'Connection'):
            require_knx = True
            knx_connection_paths.append(node_id)
        if instance.type_name == ('MQTT', 'Connection'):
            require_mqtt = True
            mqtt_server_paths.append(node_id)
        if instance.type_name == ('TwoN', 'Connection'):
            require_twon = True
            twon_device_paths.append(node_id)

    if require_knx:
        print('Test requires KNX')

    async with with_maybe_testdir(require_testdir) as testdir, with_maybe_knxd(require_knx) as knxd_socket_path, with_maybe_mqtt(require_mqtt) as mqtt_socket_path, with_maybe_twon(require_twon) as twon_info:
        instances = dict(prog.instances)

        def override_instance(node_id, parameters, **configuration):
            instance = instances[node_id]
            values = dict(instance.parameters)
            values.update(parameters)
            data = instance.configuration
            if configuration:
                data = replace(data, **configuration)
            instances[node_id] = replace(
                instance, parameters=MappingProxyType(values), configuration=data,
            )

        for node_id in csv_writer_paths:
            filename = testdir + '/' + os.path.basename(prog.instances[node_id].configuration.filename)
            override_instance(node_id, {'Filename': filename}, filename=filename)
        for node_id in knx_connection_paths:
            override_instance(node_id, {'UnixPath': knxd_socket_path})
        for node_id in mqtt_server_paths:
            override_instance(node_id, {'URI': 'mqtt+unix://' + mqtt_socket_path})
        for node_id in twon_device_paths:
            override_instance(
                node_id, {'Host': twon_info.host, 'Port': twon_info.port,
                          'TLSA': (twon_info.server.tlsa,)},
                hostname=twon_info.host, port=twon_info.port, tlsa=(twon_info.server.tlsa,),
            )
        schedules = {
            stage: tuple(replace(proc, node_context=instances[proc.node_id]) for proc in schedule)
            for stage, schedule in prog.procs.items()
        }
        prog = replace(prog, instances=instances, procs=schedules)

        eng = blocks.Engine(prog)

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
    names = sys.argv[1:]
    for name in names:
        if name not in to_compile and name not in negative_tests and name not in test_names:
            raise Exception(f'Unknown test name {name!r}')
    if len(names) == 0:
        names = None
    else:
        names = set(names)

    for name in to_compile:
        if names is None or name in names:
            compile_test(name)
    for name in negative_tests:
        if names is None or name in names:
            compile_negative_test(name)
    for name in test_names:
        if names is None or name in names:
            asyncio.run(run_test(name))


if __name__ == '__main__':
    main()
