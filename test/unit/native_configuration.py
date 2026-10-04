import tempfile
from pathlib import Path
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

import wirely
from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_block_type
from wirely.native.csv import CsvWriterParameters
from wirely.native.gpio import GPIOData
from wirely.native.knx import KnxData
from wirely.native.memory import ToggleFlipFlopTriggerData
from wirely.native.mqtt import MqttData
from wirely.native.std import EdgeData
from wirely.native.time import IsInDailyIntervalData, TriggerAtData
from wirely.native.two_n import TwoNCodeData, TwoNDeviceData
from wirely.compiler.native_preparation.csv import prepare_csv_writer_parameters
from wirely.compiler.native_preparation.gpio import prepare_gpio_data
from wirely.compiler.native_preparation.knx import prepare_knx_data
from wirely.compiler.native_preparation.memory import prepare_toggle_flip_flop_trigger_data
from wirely.compiler.native_preparation.mqtt import prepare_mqtt_data
from wirely.compiler.native_preparation.std import prepare_edge_data
from wirely.compiler.native_preparation.time import prepare_is_in_daily_interval_data, prepare_trigger_at_data
from wirely.compiler.native_preparation.two_n import prepare_two_n_code_data, prepare_two_n_device_data
from wirely.compiler.native_configuration import register_configuration
from wirely.compiler import model as compiler_model


checks = TestCase()
registry = {}
contexts = []
prepare = None


class Configuration:
    pass


def prepare_configuration(*, context):
    contexts.append(context)
    return prepare(context) if prepare else context.parameters


register_configuration(Configuration, prepare_configuration)


@native_block_type(registry, has_native_state=False, data_type=Configuration)
class ConfigurationProbe:
    @staticmethod
    def run(*, context):
        prepared = next(item for item in contexts if item.name == context.name)
        assert context.configuration == prepared.parameters
        assert not context.values
        assert not hasattr(context, 'parameters')


def compile_sources(*sources):
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = wirely.compiler.compile_strs(sources)
        assert graph is not None
        return program_compiler.compile_nocatch(graph)


def probe(declarations, *, arguments='', filename='/tmp/config/main.wrl',
          singleton=False):
    qualifier = 'singleton ' if singleton else ''
    return compile_sources((filename, f'''
namespace TestLibrary {{
  {qualifier}node type ConfigurationProbe {{
    {declarations}
    native procedure run;
  }}
}}
node probe: TestLibrary.ConfigurationProbe {{ {arguments} }}
node again: TestLibrary.ConfigurationProbe {{ {arguments} }}
'''))


program = probe('parameter Path: String = "../data/config.json";')
assert program.instances['again'].configuration is program.instances['again'].parameters
context = contexts[-1]
assert context.name == 'again'
assert isinstance(context.parameters, MappingProxyType)
with checks.assertRaises(TypeError):
    context.parameters['Path'] = 'other'
assert context.resolve_path(context.parameters['Path'], parameter='Path') == Path('/tmp/data/config.json')
assert context.resolve_path('./a/../b') == Path('/tmp/config/b')
assert context.resolve_path('/a/../b') == Path('/b')
with checks.assertRaises(ValueError):
    context.error('bad', parameter='Unknown')
with patch.dict(wirely.native.test_library.native_block_types, registry):
    wirely.Engine(program).step()
assert len(contexts) == 2

contexts.clear()
program = probe('', singleton=True)
assert len(contexts) == 1
assert contexts[0].name == 'TestLibrary.ConfigurationProbe'
assert contexts[0].resolve_path('settings.json') == Path('/tmp/config/settings.json')

probe('parameter Path: String = "settings.json";', filename='<test>')
context = contexts[-1]
assert context.resolve_path('/settings.json', parameter='Path') == Path('/settings.json')
with checks.assertRaises(compiler_model.ProgramError) as caught:
    context.resolve_path('settings.json', parameter='Path')
assert caught.exception.source_element.source_position.start_line == 4


def fail_parameter(context):
    raise context.error('Invalid path', parameter='Path')


prepare = fail_parameter
with checks.assertRaises(compiler_model.ProgramError) as caught:
    probe('parameter Path: String = "default.json";')
assert caught.exception.source_element.source_position.start_line == 4
with checks.assertRaises(compiler_model.ProgramError) as caught:
    probe('optional parameter Path: String;')
assert caught.exception.source_element.source_position.start_line == 4

with checks.assertRaises(compiler_model.ProgramError) as caught:
    compile_sources(
        ('/tmp/config/types.wrl', '''
namespace TestLibrary {
  node type ConfigurationProbe {
    parameter Path: String;
    native procedure run;
  }
}
node type Inner {
  parameter Path: String;
  node leaf: TestLibrary.ConfigurationProbe { Path: Path }
}
node type Outer {
  parameter Path: String;
  node child: Inner { Path: Path }
}
'''),
        ('/tmp/settings/main.wrl', 'node outer: Outer { Path: "relative.json" }'),
    )
error = caught.exception
assert error.source_element.source_position.filename == '/tmp/settings/main.wrl'
assert error.source_element.source_position.start_line == 1
assert [hint.source_element.source_position.start_line for hint in error.hints] == [14, 10]

prepare = lambda context: context.resolve_path(context.parameters['Path'], parameter='Path')
program = compile_sources(
    ('/tmp/config/types.wrl', '''
namespace TestLibrary {
  node type ConfigurationProbe {
    parameter Path: String;
    native procedure run;
  }
}
node type Outer {
  parameter Path: String;
  node child: TestLibrary.ConfigurationProbe { Path: Path }
}
'''),
    ('/tmp/settings/main.wrl', 'node outer: Outer { Path: "relative.json" }'),
)
assert contexts[-1].resolve_path('relative.json', parameter='Path') == Path('/tmp/settings/relative.json')


def plain_error(context):
    raise ValueError('Invalid configuration')


prepare = plain_error
with checks.assertRaises(compiler_model.ProgramError) as caught:
    probe('')
assert type(caught.exception) is compiler_model.ProgramError
assert caught.exception.source_element.source_position.start_line == 8


def unexpected_error(context):
    raise RuntimeError('Unexpected failure')


prepare = unexpected_error
with checks.assertRaises(compiler_model.InternalCompilerError):
    probe('')

# Credentials are prepared for execution on a host without the source file.
with tempfile.TemporaryDirectory() as directory:
    filename = Path(directory) / 'credentials.txt'
    filename.write_text('username\npassword\n')
    prepare = lambda context: prepare_two_n_device_data(context=context)
    program = probe('''parameter Host: String = "localhost";
    parameter Port: uint16 = 443;
    parameter TLSA: List[String] = [];
    parameter UsernamePasswordFile: String = "credentials.txt";''',
                    filename=str(Path(directory) / 'main.wrl'))
    filename.unlink()
    data = next(instance.configuration
                for instance in program.instances.values()
                if isinstance(instance.configuration, TwoNDeviceData))
    assert data.username == 'username'
    assert data.password == 'password'
    assert not hasattr(data, 'filename')
    with checks.assertRaises(compiler_model.ProgramError) as caught:
        probe('''parameter Host: String = "localhost";
        parameter Port: uint16 = 443;
        parameter TLSA: List[String] = [];
        parameter UsernamePasswordFile: String = "credentials.txt";''',
              filename=str(Path(directory) / 'main.wrl'))
    assert caught.exception.source_element.source_position.start_line == 7


factories = {
    EdgeData: prepare_edge_data,
    TwoNCodeData: prepare_two_n_code_data,
    CsvWriterParameters: prepare_csv_writer_parameters,
    KnxData: prepare_knx_data,
    MqttData: prepare_mqtt_data,
    GPIOData: prepare_gpio_data,
    IsInDailyIntervalData: prepare_is_in_daily_interval_data,
    TriggerAtData: prepare_trigger_at_data,
    ToggleFlipFlopTriggerData: prepare_toggle_flip_flop_trigger_data,
}
invalid_configurations = [
    (EdgeData, 'parameter EdgeType: String = "invalid";', 'EdgeType'),
    (TwoNCodeData, 'parameter Code: String = "123x";', 'Code'),
    (TwoNCodeData, 'parameter Code: String = "1234567890123456";', 'Code'),
    (CsvWriterParameters, 'parameter Type: ValueType = List[uint8];', 'Type'),
    (KnxData, 'parameter GroupAddress: String = "invalid";'
     'parameter Type: ValueType = boolean;', 'GroupAddress'),
    (KnxData, 'parameter GroupAddress: String = "1/2/3";'
     'parameter Type: ValueType = String;', 'Type'),
    (MqttData, 'parameter Type: ValueType = MonotonicTime;'
     'parameter Topic: String = "topic";', 'Type'),
    (MqttData, 'parameter Type: ValueType = String;'
     'parameter Topic: String = "topic";'
     'parameter Template: String = "%x";', 'Template'),
    (GPIOData, 'parameter ChipDeviceFile: String = "/dev/gpiochip0";'
     'parameter Name: String = "test";'
     'parameter ActiveState: String = "invalid";'
     'parameter Drive: String = "PushPull";'
     'parameter Bias: String = "AsIs";', 'ActiveState'),
    (IsInDailyIntervalData, 'parameter Start: TimeOfDay = "12:00";'
     'parameter End: TimeOfDay = "12:00";', 'End'),
    (TriggerAtData, 'parameter OutputType: ValueType = boolean;'
     'optional parameter OutputValue: OutputType;', 'OutputValue'),
    (ToggleFlipFlopTriggerData, 'parameter OutputType: ValueType = boolean;'
     'optional parameter OutputValue: OutputType;', 'OutputValue'),
]
for data_type, declarations, parameter in invalid_configurations:
    prepare = lambda context: factories[data_type](context=context)
    with checks.assertRaises(compiler_model.ProgramError) as caught:
        probe(declarations)
    error = caught.exception
    assert type(error) is compiler_model.ProgramError, data_type
    assert error.source_element is contexts[-1]._origins[parameter][0], data_type

# Explicit arguments take precedence over the default's source location.
prepare = lambda context: prepare_edge_data(context=context)
with checks.assertRaises(compiler_model.ProgramError) as caught:
    probe('parameter EdgeType: String = "Both";',
          arguments='EdgeType: "invalid"')
assert caught.exception.source_element.source_position.start_line == 8
