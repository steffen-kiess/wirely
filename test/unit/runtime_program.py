from dataclasses import replace
import subprocess
import sys
from textwrap import dedent
from unittest.mock import patch

import wirely
import wirely.compiler
import wirely.native.test_library
from wirely.compiler import program as program_compiler
from wirely.native import native_block_type


# Runtime imports must work without loading the optional compiler dependencies.
subprocess.run([sys.executable, '-c', dedent('''
    import importlib
    import pkgutil
    import sys

    from wirely import Program, Engine
    import wirely.native

    for module in pkgutil.iter_modules(wirely.native.__path__, 'wirely.native.'):
        importlib.import_module(module.name)
    unexpected = {name for name in sys.modules
                  if name == 'lark' or name.startswith(('lark.', 'wirely.compiler'))}
    assert not unexpected, unexpected
''')], check=True)


def check_engine_isolation():
    program = replace(engine.program, graph=None)
    first = wirely.Engine(program)
    second = wirely.Engine(program)
    first.get_native_state('source').set_value(True)
    second.get_native_state('source').set_value(False)
    first.step()
    second.step()
    first.assert_value_is('envelope', False)
    second.assert_value_is('envelope', False)
    first.step()
    first.assert_value_is('envelope', True)
    second.assert_is_idle()
    second.assert_value_is('envelope', False)
    for runtime in (first, second):
        runtime.assert_value_is('shared', True)
        runtime.assert_value_is('shared_again', True)


def check_resource_isolation():
    registry = {}

    @native_block_type(registry, has_native_state=True)
    class Provider(wirely.NativeState):
        pass

    @native_block_type(registry, has_native_state=True)
    class Consumer(wirely.NativeState):
        def __init__(self, *, context):
            super().__init__(context=context)
            self.count = 0

        def run(self, *, context):
            self.count += 1
            context.set_result('output', self.count)

    source = '''namespace TestLibrary {
  resource type Service;
  singleton native node type Provider {
    default output: resource Service;
  }
  native node type Consumer {
    input Service: resource Service;
    default output: value uint8;
    native initializer depends on Service as service;
    native procedure run produces default output as output;
  }
}
node provider: TestLibrary.Provider {}
node alias: TestLibrary.Provider {}
node consumer: TestLibrary.Consumer {}
connect alias -> consumer.Service;
'''
    with patch.dict(wirely.native.test_library.native_block_types, registry):
        graph = wirely.compiler.compile_strs([('<resource-isolation>', source)])
        assert graph is not None
        program = replace(program_compiler.compile_nocatch(graph), graph=None)
        first = wirely.Engine(program)
        second = wirely.Engine(program)
    for runtime in (first, second):
        provider = runtime.get_native_state('provider')
        assert provider is runtime.get_native_state('alias')
        assert runtime.get_native_state('consumer').inputs['service'] is provider
    assert first.get_native_state('provider') is not second.get_native_state('provider')
    first.step()
    first.trigger('consumer', 'run')
    first.step()
    first.assert_value_is('consumer', 2)
    second.step()
    second.assert_value_is('consumer', 1)


check_engine_isolation()
check_resource_isolation()
