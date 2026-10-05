import contextlib
import io
import os
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import MagicMock, patch

from wirely.compiler import compile_strs, compile_files, model as blocks
from wirely.compiler import program as program_compiler
from wirely.compiler.function_implementations.test import TEST_RESOURCES_SERVICE
from wirely.compiler.native_functions import native_function_impls
from wirely.test import TestResourceProvider, run_test as runner_run_test, with_maybe_knxd


def graph(source, *, services=None):
    result = compile_strs([('<resources>', source)], add_std=True, services=services)
    assert result is not None
    return result


def compile_program(source, *, services=None):
    return program_compiler.compile_nocatch(graph(source, services=services))


class Resources:
    def __init__(self, path):
        self.path = path
        self.calls = 0

    def get_knx_path(self):
        self.calls += 1
        return self.path


# Defaults, nested scopes, and values derived from the selected path.
source = '''node type Wrapper {
  parameter Default: String;
  constant Selected: String = Test.ReplaceKNXPath(Default);
  node bus: KNX.Connection { UnixPath: Path.Join(Selected, "derived") }
}
node wrapped: Wrapper { Default: "/fallback" }
node other: KNX.Connection { UnixPath: Test.ReplaceKNXPath("/other") }
'''


def paths(prog):
    return {instance.name: instance.initializer_values['UnixPath'].value
            for instance in prog.instances.values()
            if instance.type_name == ('KNX', 'Connection')}


assert paths(compile_program(source)) == {
    'wrapped.bus': '/fallback/derived', 'other': '/other',
}
first = Resources('/first')
second = Resources('/second')
registrations = {TEST_RESOURCES_SERVICE: first}
first_graph = graph(source, services=registrations)
registrations[TEST_RESOURCES_SERVICE] = second
second_graph = graph(source, services=registrations)
# Generate in reverse order to catch a shared current-compilation context.
assert paths(program_compiler.compile_nocatch(second_graph)) == {
    'wrapped.bus': '/second/derived', 'other': '/second',
}
assert paths(program_compiler.compile_nocatch(first_graph)) == {
    'wrapped.bus': '/first/derived', 'other': '/first',
}
assert first.calls > 0 and second.calls > 0
assert paths(compile_program(source))['other'] == '/other'
assert not hasattr(program_compiler.compile_nocatch(first_graph), 'services')
runtime_program = replace(program_compiler.compile_nocatch(first_graph), graph=None)
dump = io.StringIO()
runtime_program.dump(file=dump)
assert TEST_RESOURCES_SERVICE not in dump.getvalue()


# Snapshot registrations before reading source iterables.
registrations = {TEST_RESOURCES_SERVICE: first}


def changing_sources():
    registrations[TEST_RESOURCES_SERVICE] = second
    yield '<resources>', source


snapshot = compile_strs(changing_sources(), add_std=True, services=registrations)
assert paths(program_compiler.compile_nocatch(snapshot))['other'] == '/first'


# Native functions receive the same read-only registration mapping in namespaces.
observed = []


def inspect_service(*, context):
    assert context.services['example'] is first
    try:
        context.services['example'] = second
    except TypeError:
        pass
    else:
        raise AssertionError('Mutable services mapping')
    observed.append(context.services)
    return context.services['example'].get_knx_path()


with patch.dict(native_function_impls, {('Outer', 'Inner', 'Inspect'): inspect_service}):
    prog = compile_program('''namespace Outer.Inner {
  native function Inspect(): String;
  node type Probe {
    constant Selected: String = Inspect();
    node bus: KNX.Connection { UnixPath: Selected }
  }
}
node probe: Outer.Inner.Probe {}
''', services={'example': first})
    assert paths(prog) == {'probe.bus': '/first'}
assert observed and all(mapping is observed[0] for mapping in observed)


unused = Resources('/unused')
compile_program('''node type Unused {
  constant Selected: String = Test.ReplaceKNXPath("/unused");
}
node bus: KNX.Connection {
  UnixPath: if true { "/fallback" } else { Test.ReplaceKNXPath("/unused") },
}
''', services={TEST_RESOURCES_SERVICE: unused})
assert unused.calls == 0


class FailingResources:
    def get_knx_path(self):
        raise OSError('Fixture allocation failed')


failure_graph = graph('node bus: KNX.Connection { UnixPath: Test.ReplaceKNXPath("/default") }',
                      services={TEST_RESOURCES_SERVICE: FailingResources()})
try:
    program_compiler.compile_nocatch(failure_graph)
except blocks.ProgramError as error:
    assert str(error) == 'Fixture allocation failed'
    assert error.source_element.source_position.filename == '<resources>'
else:
    raise AssertionError('Expected a fixture diagnostic')


# The file API also passes services, without requiring a daemon for compilation.
with contextlib.ExitStack() as stack:
    resources = TestResourceProvider(stack)
    assert resources.knx_path is None
    socket_path = resources.get_knx_path()
    assert os.path.isabs(socket_path)
    assert socket_path == resources.get_knx_path()
    directory = os.path.dirname(socket_path)
    assert os.path.isdir(directory)
    compiled = compile_files(['test/wrl/knx_time.wrl'],
                             services={TEST_RESOURCES_SERVICE: resources})
    assert paths(program_compiler.compile_nocatch(compiled))['bus'] == socket_path
assert not os.path.exists(directory)


async def check_daemon_lifecycle():
    with contextlib.ExitStack() as stack:
        resources = TestResourceProvider(stack)
        socket_path = resources.get_knx_path()
        directory = os.path.dirname(socket_path)
        process = MagicMock()
        with patch('wirely.test.subprocess.Popen', return_value=process) as start:
            async with with_maybe_knxd(None):
                pass
            start.assert_not_called()

        # Successful startup and an assertion failure both reap the daemon.
        with patch('wirely.test.subprocess.Popen', return_value=process), \
                patch('wirely.test.socket.socket') as socket:
            try:
                async with with_maybe_knxd(socket_path):
                    assert os.path.isdir(directory)
                    socket.return_value.__enter__.return_value.connect.assert_called_with(socket_path)
                    raise AssertionError('Test failed')
            except AssertionError as error:
                assert str(error) == 'Test failed'
        process.kill.assert_called_once()
        process.wait.assert_called_once()

        # No ready socket: distinguish a daemon exit from a readiness timeout.
        for exit_code, exception, message in (
            (3, Exception, 'knxd has exited with exit code 3'),
            (None, TimeoutError, 'Timed out waiting for knxd to start'),
        ):
            process = MagicMock()
            process.poll.return_value = exit_code
            with patch('wirely.test.subprocess.Popen', return_value=process), \
                    patch('wirely.test.socket.socket') as socket, \
                    patch('wirely.test.asyncio.sleep'):
                socket.return_value.__enter__.return_value.connect.side_effect = FileNotFoundError
                try:
                    async with with_maybe_knxd(socket_path):
                        raise AssertionError('Unready daemon accepted')
                except exception as error:
                    assert str(error) == message
            process.kill.assert_called_once()
            process.wait.assert_called_once()

        with patch('wirely.test.subprocess.Popen', side_effect=FileNotFoundError('Missing knxd')):
            try:
                async with with_maybe_knxd(socket_path):
                    raise AssertionError('Missing executable accepted')
            except FileNotFoundError:
                pass
    assert not os.path.exists(directory)


async def check_runner_cleanup():
    allocated = []

    def failed_compilation(filenames, *, services):
        allocated.append(services[TEST_RESOURCES_SERVICE].get_knx_path())
        return None

    with patch('wirely.test.blocks.compiler.compile_files', side_effect=failed_compilation), \
            patch('wirely.test.subprocess.Popen') as start:
        try:
            await runner_run_test('unit/test_resources')
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError('Compilation failure accepted')
        start.assert_not_called()
    assert not os.path.exists(os.path.dirname(allocated[-1]))

    def fixture_compilation(filenames, *, services):
        resources = services[TEST_RESOURCES_SERVICE]
        allocated.append(resources.get_knx_path())
        return graph('node bus: KNX.Connection { UnixPath: Test.ReplaceKNXPath("/default") }',
                     services=services)

    with patch('wirely.test.blocks.compiler.compile_files', side_effect=fixture_compilation), \
            patch('wirely.test.program_compiler.compile', return_value=None), \
            patch('wirely.test.subprocess.Popen') as start:
        try:
            await runner_run_test('unit/test_resources')
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError('Program generation failure accepted')
        start.assert_not_called()
    assert not os.path.exists(os.path.dirname(allocated[-1]))

    async def fail_assertions():
        assert os.path.isdir(os.path.dirname(allocated[-1]))
        raise AssertionError('Assertions failed')

    def load_assertions(module):
        module.run_test = fail_assertions

    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=load_assertions))
    process = MagicMock()
    with patch('wirely.test.blocks.compiler.compile_files', side_effect=fixture_compilation), \
            patch('wirely.test.subprocess.Popen', return_value=process), \
            patch('wirely.test.socket.socket'), \
            patch('wirely.test.blocks.Engine'), \
            patch('wirely.test.importlib.util.spec_from_file_location', return_value=spec), \
            patch('wirely.test.importlib.util.module_from_spec', return_value=SimpleNamespace()):
        try:
            await runner_run_test('unit/test_resources')
        except AssertionError as error:
            assert str(error) == 'Assertions failed'
        else:
            raise AssertionError('Assertion failure swallowed')
    process.kill.assert_called_once()
    process.wait.assert_called_once()
    assert not os.path.exists(os.path.dirname(allocated[-1]))


async def run_test():
    with contextlib.redirect_stdout(io.StringIO()):
        await check_daemon_lifecycle()
        await check_runner_cleanup()
