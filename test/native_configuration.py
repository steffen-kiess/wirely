import tempfile
from pathlib import Path
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

import wirely
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_block_type, native_procedure
from wirely.native.csv import CsvWriterParameters
from wirely.native.gpio import GPIOData
from wirely.native.knx import KnxData
from wirely.native.memory import ToggleFlipFlopTriggerData
from wirely.native.mqtt import MqttData
from wirely.native.std import EdgeData
from wirely.native.time import IsBetweenData, TriggerAtData
from wirely.native.two_n import TwoNCodeData, TwoNDeviceData


checks = TestCase()
registry = {}
contexts = []
prepare = None


class Configuration:
    @staticmethod
    def create(*, context):
        contexts.append(context)
        return prepare(context) if prepare else context.parameters


@native_block_type(registry, has_state=False, data_type=Configuration)
class ConfigurationProbe:
    @native_procedure
    @staticmethod
    def run(*, context):
        assert context.configuration is context.parameters


def compile_sources(*sources):
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = wirely.compiler.compile_strs(sources)
        assert graph is not None
        return wirely.Program.compile_nocatch(graph)


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
node probe : TestLibrary.ConfigurationProbe{arguments};
node again : TestLibrary.ConfigurationProbe{arguments};
'''))


program = probe('parameter Path : String = "../data/config.json";')
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
wirely.Engine(program).step()
assert len(contexts) == 2

contexts.clear()
program = probe('', singleton=True)
assert len(contexts) == 1
assert contexts[0].name == 'TestLibrary.ConfigurationProbe'
assert contexts[0].resolve_path('settings.json') == Path('/tmp/config/settings.json')

probe('parameter Path : String = "settings.json";', filename='<test>')
context = contexts[-1]
assert context.resolve_path('/settings.json', parameter='Path') == Path('/settings.json')
with checks.assertRaises(wirely.ProgramError) as caught:
    context.resolve_path('settings.json', parameter='Path')
assert caught.exception.source_element.source_position.start_line == 4


def fail_parameter(context):
    raise context.error('Invalid path', parameter='Path')


prepare = fail_parameter
with checks.assertRaises(wirely.ProgramError) as caught:
    probe('parameter Path : String = "default.json";')
assert caught.exception.source_element.source_position.start_line == 4
with checks.assertRaises(wirely.ProgramError) as caught:
    probe('optional parameter Path : String;')
assert caught.exception.source_element.source_position.start_line == 4

with checks.assertRaises(wirely.ProgramError) as caught:
    compile_sources(
        ('/tmp/config/types.wrl', '''
namespace TestLibrary {
  node type ConfigurationProbe {
    parameter Path : String;
    native procedure run;
  }
}
node type Inner {
  parameter Path : String;
  node leaf : TestLibrary.ConfigurationProbe(Path = Path);
}
node type Outer {
  parameter Path : String;
  node child : Inner(Path = Path);
}
'''),
        ('/tmp/settings/main.wrl', 'node outer : Outer(Path = "relative.json");'),
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
    parameter Path : String;
    native procedure run;
  }
}
node type Outer {
  parameter Path : String;
  node child : TestLibrary.ConfigurationProbe(Path = Path);
}
'''),
    ('/tmp/settings/main.wrl', 'node outer : Outer(Path = "relative.json");'),
)
assert contexts[-1].resolve_path('relative.json', parameter='Path') == Path('/tmp/settings/relative.json')


def plain_error(context):
    raise ValueError('Invalid configuration')


prepare = plain_error
with checks.assertRaises(wirely.ProgramError) as caught:
    probe('')
assert type(caught.exception) is wirely.ProgramError
assert caught.exception.source_element.source_position.start_line == 8


def unexpected_error(context):
    raise RuntimeError('Unexpected failure')


prepare = unexpected_error
with checks.assertRaises(wirely.InternalCompilerError):
    probe('')

# Credentials are prepared for execution on a host without the source file.
with tempfile.TemporaryDirectory() as directory:
    filename = Path(directory) / 'credentials.txt'
    filename.write_text('username\npassword\n')
    prepare = lambda context: TwoNDeviceData.create(context=context)
    program = probe('''parameter Host : String = "localhost";
    parameter Port : uint16 = 443;
    parameter TLSA : List[String] = [];
    parameter UsernamePasswordFile : String = "credentials.txt";''',
                    filename=str(Path(directory) / 'main.wrl'))
    filename.unlink()
    data = next(instance.static_context.parsed_native_instance
                for instance in program.instances.values()
                if isinstance(instance.static_context.parsed_native_instance, TwoNDeviceData))
    assert data.username == 'username'
    assert data.password == 'password'
    assert not hasattr(data, 'filename')
    with checks.assertRaises(wirely.ProgramError) as caught:
        probe('''parameter Host : String = "localhost";
        parameter Port : uint16 = 443;
        parameter TLSA : List[String] = [];
        parameter UsernamePasswordFile : String = "credentials.txt";''',
              filename=str(Path(directory) / 'main.wrl'))
    assert caught.exception.source_element.source_position.start_line == 7


invalid_configurations = [
    (EdgeData, 'parameter EdgeType : String = "invalid";', 'EdgeType'),
    (TwoNCodeData, 'parameter Code : String = "123x";', 'Code'),
    (TwoNCodeData, 'parameter Code : String = "1234567890123456";', 'Code'),
    (CsvWriterParameters, 'parameter Type : ValueType = List[uint8];', 'Type'),
    (KnxData, 'parameter GroupAddress : String = "invalid";'
     'parameter Type : ValueType = boolean;', 'GroupAddress'),
    (KnxData, 'parameter GroupAddress : String = "1/2/3";'
     'parameter Type : ValueType = String;', 'Type'),
    (MqttData, 'parameter Type : ValueType = MonotonicTime;'
     'parameter Topic : String = "topic";', 'Type'),
    (MqttData, 'parameter Type : ValueType = String;'
     'parameter Topic : String = "topic";'
     'parameter Template : String = "%x";', 'Template'),
    (GPIOData, 'parameter ChipDeviceFile : String = "/dev/gpiochip0";'
     'parameter Name : String = "test";'
     'parameter ActiveState : String = "invalid";'
     'parameter Drive : String = "PushPull";'
     'parameter Bias : String = "AsIs";', 'ActiveState'),
    (IsBetweenData, 'parameter Start : TimeOfDay = "12:00";'
     'parameter End : TimeOfDay = "12:00";', 'End'),
    (TriggerAtData, 'parameter OutputType : ValueType = boolean;'
     'optional parameter OutputValue : OutputType;', 'OutputValue'),
    (ToggleFlipFlopTriggerData, 'parameter OutputType : ValueType = boolean;'
     'optional parameter OutputValue : OutputType;', 'OutputValue'),
]
for data_type, declarations, parameter in invalid_configurations:
    prepare = lambda context: data_type.create(context=context)
    with checks.assertRaises(wirely.ProgramError) as caught:
        probe(declarations)
    error = caught.exception
    assert type(error) is wirely.ProgramError, data_type
    assert error.source_element is contexts[-1]._origins[parameter][0], data_type

# Explicit arguments take precedence over the default's source location.
prepare = lambda context: EdgeData.create(context=context)
with checks.assertRaises(wirely.ProgramError) as caught:
    probe('parameter EdgeType : String = "Both";',
          arguments='(EdgeType = "invalid")')
assert caught.exception.source_element.source_position.start_line == 8
