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

import wirely as blocks
from wirely.util import check_parameters

# TODO: Where should this import be done?
import wirely.compiler

test_names = [
    'types',
    'string_literals',
    'delay',
    'std',
    'and',
    'option',
    'gpio',
    'knx',
    'abb',
    'mqtt',
    'time',
    'csv',
    'knx_time',
    'flipflop',
    'two_n',
    'ext_prog',
]

to_compile = [
    # 'wirely/lib/std.wrl',
    'examples/test.wrl',
    'examples/knx.wrl',
    'examples/delay.wrl',
    'examples/flipflop.wrl',
    'examples/gpio.wrl',
    'test/simple.wrl',
    'test/parser.wrl',
    'test/compiler.wrl',
    'test/nested_library.wrl',
]

negative_tests = [
    'test/negative/string_invalid_hex_escape.wrl',
    'test/negative/string_unicode_missing_braces.wrl',
    'test/negative/string_invalid_unicode_scalar.wrl',
    'test/negative/string_unknown_escape.wrl',
    'test/negative/unknown_parameter.wrl',
    'test/negative/unknown_node_type.wrl',
    'test/negative/singleton_input.wrl',
    'test/negative/singleton_parameter.wrl',
    'test/negative/duplicate_block_type_in_reopened_library.wrl',
    'test/negative/unknown_abb_jra.wrl',
    'test/negative/library_inside_node_type.wrl',
]

diagnostic_pattern = re.compile(
    r'^(Error in |Hint: |Warning in |Warning: )(.+):(\d+): (.+)$'
)
annotation_pattern = re.compile(r'\s*(\^*)([EHW]):\s*(.*?)\s*$')


def expected_diagnostics(source):
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
            if target_line < 1 or not message:
                raise AssertionError(f'Malformed annotation on line {line_number}: //{comment}')
            expected.append((kind, target_line, message))
    return expected


def compile_negative_test(fn):
    print(f'Compiling negative test {fn}')
    full_fn = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', fn))
    with open(full_fn, encoding='utf-8') as file:
        expected = expected_diagnostics(file.read())
    if not expected:
        raise AssertionError(f'{fn}: no expected diagnostics')

    output = io.StringIO()
    errors = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        result = blocks.compiler.compile_files([full_fn])

    stdout = output.getvalue()
    stderr = errors.getvalue()
    if stderr or 'Internal compiler error' in stdout or 'Traceback (most recent call last)' in stdout:
        raise AssertionError(f'{fn}: compiler failed internally:\n{stdout}{stderr}')

    actual = []
    for line in stdout.splitlines():
        match = diagnostic_pattern.fullmatch(line)
        if match is not None:
            prefix, filename, line_number, message = match.groups()
            kind = {'Error in ': 'E', 'Hint: ': 'H',
                    'Warning in ': 'W', 'Warning: ': 'W'}[prefix]
            actual.append((kind, os.path.abspath(filename), int(line_number), message))
        elif line.startswith(('Error', 'Hint', 'Warning')):
            raise AssertionError(f'{fn}: malformed diagnostic: {line!r}')

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
            f'{fn}: diagnostic mismatch; missing={missing}, unexpected={unexpected}, '
            f'compiled={result is not None}\n{stdout}'
        )
    print(f'Compiling negative test {fn} done')


def compile_test(fn):
    print(f'Compiling {fn}')

    full_fn = os.path.dirname(__file__) + '/../' + fn
    global_block_type = blocks.compiler.compile_files([full_fn])
    if global_block_type is None:
        sys.exit(1)

    prog = blocks.Program.compile(global_block_type)
    if prog is None:
        sys.exit(1)

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

    prog = blocks.Program.compile(global_block_type)
    if prog is None:
        sys.exit(1)

    # TODO: Clean up
    require_testdir = False
    require_knx = False
    require_mqtt = False
    require_twon = False
    csv_writer_paths = []
    knx_connection_paths = []
    mqtt_server_paths = []
    twon_device_paths = []
    for block_name in prog.graph.blocks:
        for block_path in prog.graph.blocks[block_name].get_all_block_paths(()):
            if isinstance(block_path[-1], blocks.BlockType):
                block_type = block_path[-1]
            else:
                block_type = block_path[-1].block_type
            if block_type.fullname == 'CSV.ValueWriter':
                require_testdir = True
                csv_writer_paths.append(block_path)
            if block_type.fullname == 'KNX.Connection':
                require_knx = True
                knx_connection_paths.append(block_path)
            if block_type.fullname == 'MQTT.Server':
                require_mqtt = True
                mqtt_server_paths.append(block_path)
            if block_type.fullname == 'TwoN.Device':
                require_twon = True
                twon_device_paths.append(block_path)

    if require_knx:
        print('Test requires KNX')

    async with with_maybe_testdir(require_testdir) as testdir, with_maybe_knxd(require_knx) as knxd_socket_path, with_maybe_mqtt(require_mqtt) as mqtt_socket_path, with_maybe_twon(require_twon) as twon_info:
        # TODO: Clean up
        if require_testdir:
            for path in csv_writer_paths:
                block = path[-1]
                found = False
                for key in block.par_values:
                    if key.name.id_str != 'Filename':
                        continue
                    found = True
                    old_value = block.par_values[key].value
                    new_value = testdir + '/' + os.path.basename(old_value)
                    print(f'Changing Filename from {old_value} to {new_value}', flush=True)
                    block.par_values[key] = blocks.ConstantValue(block.par_values[key].value_type, new_value, ast_element=None)
                if not found:
                    raise Exception('Could not find Filename parameter')

                block_instance = prog.instances[path]
                old_value = block_instance.static_context.parsed_native_instance.filename
                new_value = testdir + '/' + os.path.basename(old_value)
                print(f'Changing static_context Filename from {old_value} to {new_value}', flush=True)
                # TODO: Clean up
                # block_instance.static_context.parsed_native_instance.filename = new_value
                block_instance.static_context.parsed_native_instance.__dict__['filename'] = new_value
                found = False
                for key in block_instance.parameter_values:
                    if key.name.id_str != 'Filename':
                        continue
                    found = True
                    old_value = block_instance.parameter_values[key].value
                    new_value = testdir + '/' + os.path.basename(old_value)
                    print(f'Changing instance Filename from {old_value} to {new_value}', flush=True)
                    block_instance.parameter_values[key] = blocks.ConstantValue(block_instance.parameter_values[key].value_type, new_value, ast_element=None)
                if not found:
                    raise Exception('Could not find Filename parameter')
        if require_knx:
            for path in knx_connection_paths:
                # TODO: Check whether this does the right thing if the Connection instantiation does not happen on the global level
                # TODO: Is this needed or should this all be done on the instance level?
                block = path[-1]
                found = False
                for key in block.par_values:
                    if key.name.id_str != 'UnixPath':
                        continue
                    found = True
                    print(f'Changing UnixPath from {block.par_values[key].value} to {knxd_socket_path}', flush=True)
                    block.par_values[key] = blocks.ConstantValue(block.par_values[key].value_type, knxd_socket_path, ast_element=None)
                if not found:
                    raise Exception('Could not find UnixPath parameter')

                block_instance = prog.instances[path]
                found = False
                for key in block_instance.parameter_values:
                    if key.name.id_str != 'UnixPath':
                        continue
                    found = True
                    print(f'Changing instance UnixPath from {block_instance.parameter_values[key].value} to {knxd_socket_path}', flush=True)
                    new_value = blocks.ConstantValue(block_instance.parameter_values[key].value_type, knxd_socket_path, ast_element=None)
                    block_instance.parameter_values[key] = new_value
                    block_instance.static_context.parameter_values['UnixPath'] = new_value
                if not found:
                    raise Exception('Could not find UnixPath parameter')
        if require_mqtt:
            new_uri = 'mqtt+unix://' + mqtt_socket_path
            for path in mqtt_server_paths:
                # TODO: Check whether this does the right thing if the Connection instantiation does not happen on the global level
                # TODO: Is this needed or should this all be done on the instance level?
                block = path[-1]
                found = False
                for key in block.par_values:
                    if key.name.id_str != 'URI':
                        continue
                    found = True
                    print(f'Changing URI from {block.par_values[key].value} to {new_uri}', flush=True)
                    block.par_values[key] = blocks.ConstantValue(block.par_values[key].value_type, new_uri, ast_element=None)
                if not found:
                    raise Exception('Could not find URI parameter')

                block_instance = prog.instances[path]
                found = False
                for key in block_instance.parameter_values:
                    if key.name.id_str != 'URI':
                        continue
                    found = True
                    print(f'Changing instance URI from {block_instance.parameter_values[key].value} to {new_uri}', flush=True)
                    block_instance.parameter_values[key] = blocks.ConstantValue(block_instance.parameter_values[key].value_type, new_uri, ast_element=None)
                if not found:
                    raise Exception('Could not find URI parameter')
        if require_twon:
            new_hostname = twon_info.host
            new_port = twon_info.port
            new_tlsa = [twon_info.server.tlsa]
            for path in twon_device_paths:
                # TODO: Clean this up / where does the value have to be changed?
                # TODO: Check whether this does the right thing if the Connection instantiation does not happen on the global level
                # TODO: Is this needed or should this all be done on the instance level?
                block = path[-1]
                found = False
                for key in block.par_values:
                    if key.name.id_str != 'Hostname':
                        continue
                    found = True
                    print(f'Changing Hostname from {block.par_values[key].value} to {new_hostname}', flush=True)
                    block.par_values[key] = blocks.ConstantValue(block.par_values[key].value_type, new_hostname, ast_element=None)
                if not found:
                    raise Exception('Could not find Hostname parameter')

                block_instance = prog.instances[path]
                old_value = block_instance.static_context.parsed_native_instance.hostname
                new_value = new_hostname
                print(f'Changing static_context Hostname from {old_value} to {new_value}', flush=True)
                found = False
                for key in block_instance.parameter_values:
                    if key.name.id_str != 'Hostname':
                        continue
                    found = True
                    old_value = block_instance.parameter_values[key].value
                    new_value = new_hostname
                    print(f'Changing instance Hostname from {old_value} to {new_value}', flush=True)
                    block_instance.parameter_values[key] = blocks.ConstantValue(block_instance.parameter_values[key].value_type, new_value, ast_element=None)
                if not found:
                    raise Exception('Could not find Hostname parameter')

                # TODO: Clean up
                # block_instance.static_context.parsed_native_instance.hostname = new_value
                block_instance.static_context.parsed_native_instance.__dict__['hostname'] = new_value
                block_instance.static_context.parsed_native_instance.__dict__['port'] = new_port
                block_instance.static_context.parsed_native_instance.__dict__['tlsa'] = new_tlsa

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
