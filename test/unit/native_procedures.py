from unittest.mock import patch

import wirely
from wirely.compiler import program as program_compiler
import wirely.compiler
import wirely.native.test_library
from wirely.native import native_block_type


registry = {}
calls = []


class Methods:
    def instance(self, *, context):
        assert context.name == self.context.name
        calls.append(('instance', context.name))

    @staticmethod
    def shared(*, context, optional=7):
        calls.append(('shared', context.name, optional))

    @classmethod
    def class_method(cls, *, context):
        calls.append(('class', context.name, cls.__name__))

    @staticmethod
    def flexible(**kwargs):
        calls.append(('flexible', kwargs['context'].name))

    @staticmethod
    def positional(context):
        calls.append(('positional', context.name))


@native_block_type(registry, has_native_state=True)
class InstanceProbe(Methods, wirely.NativeState):
    pass


@native_block_type(registry, has_native_state=True)
class OverrideProbe(InstanceProbe):
    def shared(self, *, context):
        calls.append(('override', context.name))


@native_block_type(registry, has_native_state=False)
class ClassProbe(Methods):
    def plain(*, context):
        calls.append(('plain', context.name))


@native_block_type(registry, has_native_state=True)
class SharedProbe(InstanceProbe):
    pass


def compile_program(source, implementations):
    with patch.dict(wirely.native.test_library.native_block_types, implementations):
        graph = wirely.compiler.compile_strs([('<native-procedures>', source)])
        assert graph is not None
        return program_compiler.compile_nocatch(graph)



program = compile_program('''namespace TestLibrary {
  native node type InstanceProbe {
    native procedure Poll as "instance";
    native procedure shared;
    native procedure class_method;
  }
  native node type OverrideProbe {
    native procedure instance;
    native procedure shared;
    native procedure class_method;
  }
  node type ClassProbe {
    native procedure shared;
    native procedure class_method;
    native procedure flexible;
    native procedure positional;
    native procedure plain;
  }
  singleton native node type SharedProbe {
    native procedure instance;
    native procedure shared;
    native procedure class_method;
  }
}
node instance: TestLibrary.InstanceProbe {}
node overridden: TestLibrary.OverrideProbe {}
node class_only: TestLibrary.ClassProbe {}
node shared: TestLibrary.SharedProbe {}
node shared_again: TestLibrary.SharedProbe {}
''', registry)
with patch.dict(wirely.native.test_library.native_block_types, registry):
    probe_engine = wirely.Engine(program)
probe_engine.step()
assert len(calls) == 14
for name, class_name in [('instance', 'InstanceProbe'),
                         ('overridden', 'OverrideProbe'),
                         ('TestLibrary.SharedProbe', 'SharedProbe')]:
    assert ('instance', name) in calls
    assert ('class', name, class_name) in calls
assert ('shared', 'instance', 7) in calls
assert ('override', 'overridden') in calls
assert ('shared', 'class_only', 7) in calls
assert ('class', 'class_only', 'ClassProbe') in calls
assert ('flexible', 'class_only') in calls
assert ('positional', 'class_only') in calls
assert ('plain', 'class_only') in calls

# Lookup uses the live Python instance and ordinary descriptor binding.
state = probe_engine.get_native_state('instance')
state.shared = lambda *, context: calls.append(('replacement', context.name))
state.trigger('shared')
probe_engine.step()
assert calls[-1] == ('replacement', 'instance')
probe_engine.assert_is_idle()
