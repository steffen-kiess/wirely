import sys
import types
from unittest import TestCase

from wirely.generated import extension as builtin_extension
import wirely
import wirely.compiler
from wirely import types as wirely_types
from wirely.compiler import native_contracts
from wirely.compiler.native_signatures import native_signatures
from wirely.program import RunStage
from wirely.runtime.native_api import (
    FunctionBinding, InterfaceBinding, PreparationServices, ProcedureServices, StateServices,
)
from wirely.runtime.resources import ResourceType


checks = TestCase()


def compile_interfaces(source):
    # Unread resource outputs cause warnings, which these interfaces do not need to avoid.
    diagnostics = []
    graph = wirely.compiler.compile_strs([('<contracts>', source)], shuffle_seed=None,
                                         on_diagnostic=diagnostics.append)
    assert graph is not None, diagnostics
    return native_signatures(graph, 'TestLibrary')


def load_contracts(interfaces, resource_classes=None):
    """Generate the TestLibrary contracts of interfaces and import them as a module."""
    sources = native_contracts.contract_sources(interfaces, resource_classes=resource_classes or {}, strict=False)
    assert list(sources) == ['test_library']
    module = types.ModuleType('test_contracts')
    # Dataclasses look up their module while being created.
    sys.modules[module.__name__] = module
    try:
        exec(compile(sources['test_library'], '<test_contracts>', 'exec'), module.__dict__)
    finally:
        del sys.modules[module.__name__]
    return module


def check_names():
    for key, name in [('ChipDeviceFile', 'chip_device_file'), ('SSHTarget', 'ssh_target'), ('TwoN', 'two_n'),
                      ('Type', 'type'), ('group_address', 'group_address'), ('URI', 'uri'),
                      ('implicit input', 'implicit_input'), ('', '_'), ('2nd', '_2nd'), ('class', 'class_'),
                      ('Services', 'services_'), ('self', 'self_'), ('last_written_at_old', 'last_written_at_old')]:
        assert native_contracts.snake_case(key) == name, (key, native_contracts.snake_case(key))
    assert [native_contracts.camel_case(name) for name in ('send_value', 'run', 'getValue', 'EventSender')] == [
        'SendValue', 'Run', 'GetValue', 'EventSender']
    assert native_contracts.module_name('TwoN') == 'two_n'
    assert native_contracts.module_name('ABB.JRA') == 'abb_jra'
    for kind, name, location in [('procedure', 'MQTT.EventSender:send_value', ('mqtt', 'EventSenderSendValue')),
                                 ('initializer', 'KNX.Connection', ('knx', 'ConnectionInitializer')),
                                 ('function', 'MonotonicTime.CheckedAdd', ('monotonic_time', '_CheckedAdd'))]:
        assert native_contracts.contract_location(kind, name) == location
    with checks.assertRaisesRegex(ValueError, "Native name 'Probe' has no namespace"):
        native_contracts.contract_location('initializer', 'Probe')

    # Generated loaders carry direct adapters, without contract-location strings.
    from wirely.generated import _bindings as native_bindings
    from wirely.generated import mqtt
    from test.fixtures.native_extension.generated import test_library, extension as fixture
    assert native_bindings.procedures['MQTT.EventSender:send_value']().adapter is mqtt.EventSenderSendValue._adapter
    assert fixture.functions['TestLibrary.Clamp'].load().contract is test_library._Clamp


def check_collisions():
    for declarations, message in [
        ('''node type Probe {
              input Value: value boolean;
              input value: value boolean;
              native procedure run depends on Value, value;
            }''', "input 'Value' and input 'value' have the same Python name 'value'"),
        ('''node type Probe {
              parameter Value: boolean;
              native procedure run uses Value, Value as "value";
            }''', "uses binding 'Value' and uses binding 'value' have the same Python name"),
        ('''node type Probe {
              native procedure run as "TestLibrary.Probe:a_b";
              native procedure other as "TestLibrary.Probe:aB";
            }''', "Contracts of TestLibrary.Probe:aB and TestLibrary.Probe:a_b both define 'ProbeAB"),
        # Contracts cannot shadow the names their module imports.
        ('node type Native { native procedure run as "TestLibrary.Native:Procedure"; }',
         "Contract name 'NativeProcedure' of TestLibrary.Native:Procedure is also an imported name"),
    ]:
        interfaces = compile_interfaces(f'namespace TestLibrary {{ {declarations} }}')
        with checks.assertRaisesRegex(ValueError, message):
            native_contracts.contract_sources(interfaces, resource_classes={}, strict=False)

    # Resource types need a known class, unless generation is not strict.
    interfaces = compile_interfaces('''namespace TestLibrary {
      resource type Handle;
      native node type Owner {
        binding state: resource Handle;
        native initializer produces state as "state";
      }
    }''')
    with checks.assertRaisesRegex(ValueError, 'Cannot find the Python class of resource type TestLibrary.Handle'):
        native_contracts.contract_sources(interfaces, resource_classes={})
    source = native_contracts.contract_sources(
        interfaces, resource_classes={'TestLibrary.Handle': ('example.module', 'Handle')})['test_library']
    assert 'if TYPE_CHECKING:\n    from example.module import Handle\n' in source
    assert '    state: Handle\n' in source


source = '''namespace TestLibrary {
  resource type Handle;
  native node type Owner {
    parameter Limit: uint8;
    optional parameter Label: String;
    binding state: resource Handle;
    native initializer produces state as "state" uses Limit, optional(Label) as label;
  }
  node type Probe {
    parameter Type: ValueType;
    input Owner: resource Handle;
    input Value: value Option[Type];
    input Trigger: event uint8;
    output Out: event Type;
    output Count: value uint8;
    native procedure run
      depends on Owner, Value as "implicit input", Trigger
      produces Out, Count
      uses Type;
  }
  native function Twice(value: uint8): Option[uint8];
}
'''


def check_adapters():
    interfaces = compile_interfaces(source)
    contracts = load_contracts(interfaces)

    # Contract methods must be implemented.
    with checks.assertRaises(TypeError):
        contracts.OwnerInitializer()
    with checks.assertRaises(TypeError):
        contracts.ProbeRun()

    class Owner(contracts.OwnerInitializer):
        def initialize(self, *, services, limit, label):
            self.limit = limit
            self.label = label
            return contracts.OwnerInitializerResult(state=self)

    class Probe(contracts.ProbeRun):
        def prepare(self, *, services, owner, type):
            self.prepared = (services.name, owner, type)

        def run(self, *, services, owner, implicit_input, trigger):
            self.inputs = (owner, implicit_input, trigger)
            return self.result

    handle_type = ResourceType('TestLibrary.Handle', Owner)
    state_types, procedures, functions = interfaces
    triggers = []
    services = StateServices(name='owner', output_keys=Owner._adapter.output_keys, trigger=triggers.append,
                             shutdown_time=lambda: None, native_types=builtin_extension.native_types)
    owner, outputs = Owner._adapter.construct(Owner, services, InterfaceBinding(
        where='Owner', signature=state_types['TestLibrary.Owner'],
        use_types={'Limit': wirely_types.uint8, 'label': wirely_types.TypeOption(wirely_types.String)},
        use_values={'Limit': 9, 'label': wirely.Some('text')}, resources={}, port_types={},
        output_resource_types={'state': handle_type}, native_types=builtin_extension.native_types))
    assert (owner.limit, owner.label) == (wirely.UInt8(9), wirely.Some('text'))
    assert owner.services is services and outputs['state'].object is owner and outputs['state'].type is handle_type
    owner.services.trigger('state')
    assert triggers == ['state']
    with checks.assertRaisesRegex(ValueError, "Unknown output 'other' on owner"):
        owner.services.trigger('other')

    probe = Probe()
    value_type = wirely_types.TypeList(wirely_types.boolean)
    binding = InterfaceBinding(
        where='Probe', signature=procedures['TestLibrary.Probe:run'],
        use_types={'Type': wirely_types.ValueType}, use_values={'Type': value_type},
        resources={'Owner': outputs['state']},
        port_types={'implicit input': wirely_types.TypeOption(value_type),
                    'Trigger': wirely_types.TypeOption(wirely_types.uint8),
                    'Out': wirely_types.TypeOption(value_type), 'Count': wirely_types.uint8},
        output_resource_types={}, native_types=builtin_extension.native_types)
    Probe._adapter.prepare(probe, PreparationServices(name='probe', native_types=builtin_extension.native_types), binding)
    assert probe.prepared == ('probe', owner, wirely.ValueType(value_type))

    descriptor = wirely.ValueType(value_type)
    procedure_services = ProcedureServices(name='probe', stage_times={}, trigger_self=lambda: None, native_types=builtin_extension.native_types)
    payload = descriptor.from_native((True, False), native_types=builtin_extension.native_types)
    probe.result = contracts.ProbeRunResult(out=wirely.Some(payload), count=wirely.UInt8(2))
    outputs = Probe._adapter.run(probe, procedure_services, binding,
                                 {'implicit input': wirely.Some((True, False)), 'Trigger': wirely.Some(3)})
    assert outputs == {'Out': wirely.Some((True, False)), 'Count': 2}
    assert probe.inputs == (owner, wirely.Some(payload), wirely.Some(wirely.UInt8(3)))
    probe.result = contracts.ProbeRunResult(out=None, count=wirely.UInt8(0))
    assert Probe._adapter.run(probe, procedure_services, binding, {'implicit input': None, 'Trigger': None}) == {
        'Out': None, 'Count': 0}

    # Results must be the contract's result class, with values of the bound types.
    for result, message in [
        (None, 'Probe must return a ProbeRunResult, got None'),
        (contracts.ProbeRunResult(out=None, count=2),
         "Probe: invalid value for output 'Count': Expected a wirely.UInt8 for uint8, got 2"),
        (contracts.ProbeRunResult(out=wirely.Some(wirely.value_types.boolean.from_native(True, native_types=builtin_extension.native_types)), count=wirely.UInt8(0)),
         "Probe: invalid value for output 'Out': in option: Expected a DynamicValue of List\\[boolean\\], "
         'got one of boolean'),
    ]:
        probe.result = result
        with checks.assertRaisesRegex(ValueError, message):
            Probe._adapter.run(probe, procedure_services, binding, {'implicit input': None, 'Trigger': None})
    with checks.assertRaises(TypeError):
        contracts.ProbeRunResult(out=None)

    # Function contracts convert arguments and results.
    def twice(value):
        doubled = value.value * 2
        return wirely.Some(wirely.UInt8(doubled)) if doubled <= 255 else None

    function_binding = FunctionBinding(name='TestLibrary.Twice', parameter_types=(wirely_types.uint8,),
                                       return_type=wirely_types.TypeOption(wirely_types.uint8), native_types=builtin_extension.native_types)
    assert contracts._Twice.name == 'TestLibrary.Twice'
    assert contracts._Twice.call(twice, function_binding, [7]) == wirely.Some(14)
    assert contracts._Twice.call(twice, function_binding, [200]) is None
    with checks.assertRaisesRegex(ValueError, "Native function 'TestLibrary.Twice' returned an invalid value"):
        contracts._Twice.call(lambda value: wirely.Some(14), function_binding, [7])


def check_services():
    times = {RunStage.DEFAULT: object()}
    triggered = []
    services = ProcedureServices(name='probe', stage_times=times, trigger_self=lambda: triggered.append(True), native_types=builtin_extension.native_types)
    assert services.start_time_for_stage(RunStage.DEFAULT) is times[RunStage.DEFAULT]
    with checks.assertRaisesRegex(ValueError, 'Expected a RunStage'):
        services.start_time_for_stage('default')
    services.trigger_self()
    assert triggered == [True]
    assert services.wakeup_time is None
    with checks.assertRaises(ValueError):
        services.set_wakeup_time('soon')
    deadline = wirely.MonotonicTime(generation=1, ticks=5)
    services.set_wakeup_time(deadline)
    assert services.wakeup_time == deadline
    with checks.assertRaisesRegex(ValueError, 'Wakeup time on probe was already set'):
        services.set_wakeup_time(None)


def check_binding_type_checks():
    """Generated imports and factories expose missing or incompatible implementations to mypy."""
    import subprocess
    import tempfile
    from pathlib import Path
    from wirely.generated import _signatures as native_interfaces

    from test.fixtures.native_extension.generated import extension as fixture
    interfaces = (
        {'Std.DebugValueSource': native_interfaces.native_state_types['Std.DebugValueSource']},
        {'Std.DebugEventPrinter:run': native_interfaces.native_procedures['Std.DebugEventPrinter:run']},
        {'TestLibrary.Clamp': fixture.functions['TestLibrary.Clamp'].signature},
    )
    source = native_contracts.generate_bindings(interfaces).replace('wirely.generated.test_library',
             'test.fixtures.native_extension.generated.test_library').replace('wirely.functions.test_library',
             'test.fixtures.native_extension.functions.test_library')
    constant_source = native_contracts.generate_constant_bindings({
        'KNX.ValueTypeName': native_interfaces.native_functions.get('KNX.ValueTypeName')})
    cases = [
        ('valid', source, '', None),
        ('missing_module', source.replace('from test.fixtures.native_extension.functions.test_library import clamp',
                                         'from missing_native_implementation import clamp'), '', 'import-not-found'),
        ('missing_function', source.replace('import clamp', 'import nonexistent as clamp'), '', 'attr-defined'),
        ('wrong_function', source.replace('from test.fixtures.native_extension.functions.test_library import clamp',
                                          'from implementation import clamp'),
         'def clamp(value: int) -> int:\n    return value\n', 'arg-type'),
        ('async_function', source.replace('from test.fixtures.native_extension.functions.test_library import clamp',
                                          'from implementation import clamp'),
         'from wirely import Int16\nasync def clamp(a: Int16, b: Int16, c: Int16) -> Int16:\n    return a\n', 'arg-type'),
        ('generator_function', source.replace('from test.fixtures.native_extension.functions.test_library import clamp',
                                              'from implementation import clamp'),
         'from collections.abc import Iterator\nfrom wirely import Int16\n'
         'def clamp(a: Int16, b: Int16, c: Int16) -> Iterator[Int16]:\n    yield a\n', 'arg-type'),
        ('abstract_initializer', source.replace('from wirely.native.std import DebugValueSource\n',
                                                'from implementation import DebugValueSource\n'),
         'from wirely.generated.std import DebugValueSourceInitializer\n'
         'class DebugValueSource(DebugValueSourceInitializer):\n    pass\n', 'abstract'),
        ('abstract_procedure', source.replace('from wirely.native.std import DebugEventPrinter\n',
                                              'from implementation import DebugEventPrinter\n'),
         'from wirely.generated.std import DebugEventPrinterRun\n'
         'class DebugEventPrinter:\n    class Run(DebugEventPrinterRun):\n        pass\n', 'abstract'),
        ('constructor', source.replace('from wirely.native.std import DebugValueSource\n',
                                       'from implementation import DebugValueSource\n'),
         'from wirely.native.std import DebugValueSource as Base\n'
         'class DebugValueSource(Base):\n    def __init__(self, required: int) -> None:\n        pass\n', 'call-arg'),
        ('wrong_method', source.replace('from wirely.native.std import DebugValueSource\n',
                                        'from implementation import DebugValueSource\n'),
         'from wirely.generated.std import DebugValueSourceInitializer, DebugValueSourceInitializerResult\n'
         'class DebugValueSource(DebugValueSourceInitializer):\n'
         '    def initialize(self, *, other: int) -> DebugValueSourceInitializerResult:\n'
         '        return DebugValueSourceInitializerResult(state=self)\n', 'override'),
        ('unrelated_initializer', source.replace('from wirely.native.std import DebugValueSource\n',
                                                 'from implementation import DebugValueSource\n'),
         'class DebugValueSource:\n    pass\n', 'return-value'),
        ('async_procedure', source.replace('from wirely.native.std import DebugEventPrinter\n',
                                           'from implementation import DebugEventPrinter\n'),
         'from wirely.generated.std import DebugEventPrinterRun, DebugEventPrinterRunResult\n'
         'from wirely.runtime.native_api import ProcedureServices\n'
         'class DebugEventPrinter:\n    class Run(DebugEventPrinterRun):\n'
         '        async def run(self, *, services: ProcedureServices) -> DebugEventPrinterRunResult:\n'
         '            return DebugEventPrinterRunResult()\n', 'override'),
        ('constant_signature', constant_source.replace(
            'from wirely.compiler.function_implementations.knx import value_type_name',
            'from implementation import value_type_name'),
         'def value_type_name(number: int) -> str:\n    return str(number)\n', 'arg-type'),
    ]
    with tempfile.TemporaryDirectory(prefix='wirely-native-mypy-') as directory:
        root = Path(directory)
        for label, bindings, implementation, expected in cases:
            (root / 'bindings.py').write_text(bindings)
            (root / 'implementation.py').write_text(implementation)
            result = subprocess.run([sys.executable, '-m', 'mypy', '--follow-imports=silent',
                                     '--cache-dir', str(root / 'cache'),
                                     str(root / 'bindings.py'), str(root / 'implementation.py')],
                                    capture_output=True, text=True)
            output = result.stdout + result.stderr
            if expected is None:
                assert result.returncode == 0, (label, output)
            else:
                assert result.returncode != 0 and f'[{expected}]' in output, (label, output)


def check_binding_imports():
    import subprocess
    subprocess.run([sys.executable, '-c', """
import sys
import wirely.generated
assert not any(name.startswith(('wirely.compiler', 'wirely.native.', 'wirely.functions.'))
               or name in ('lark', 'xknx', 'gpiod', 'aiomqtt') for name in sys.modules)
"""], check=True)
    subprocess.run([sys.executable, '-c', """
import builtins
original = builtins.__import__
def checked(name, *args, **kwargs):
    if name.startswith(('wirely.generated', 'wirely.compiler.generated', 'wirely.native.', 'wirely.functions.')):
        raise AssertionError('Generation imported implementations: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = checked
from wirely.compiler.native_contracts import generated_files
generated_files()
"""], check=True)


check_binding_imports()
check_binding_type_checks()
check_names()
check_collisions()
check_adapters()
check_services()


async def run_test():
    pass
