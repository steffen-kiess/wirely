from pathlib import Path
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

import wirely
from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_block_type
from wirely.native.memory import ToggleFlipFlopTriggerData
from wirely.native.time import TriggerAtData
from wirely.compiler.native_preparation.memory import prepare_toggle_flip_flop_trigger_data
from wirely.compiler.native_preparation.time import prepare_trigger_at_data
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
        graph = wirely.compiler.compile_strs(
            wirely.compiler._standard_library_sources(['path']) + list(sources),
        )
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

factories = {
    TriggerAtData: prepare_trigger_at_data,
    ToggleFlipFlopTriggerData: prepare_toggle_flip_flop_trigger_data,
}
invalid_configurations = [
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
