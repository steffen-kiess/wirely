import contextlib
import io
from functools import wraps
from unittest.mock import patch

import wirely
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
        return wirely.Program.compile_nocatch(graph)



def check_invalid(source, implementations, message, line):
    output = io.StringIO()
    errors = io.StringIO()
    with patch.dict(wirely.native.test_library.native_block_types, implementations):
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            graph = wirely.compiler.compile_strs([('<native-procedures>', source)])
            result = wirely.Program.compile(graph) if graph is not None else None
    assert result is None
    assert not errors.getvalue(), errors.getvalue()
    assert message in output.getvalue(), output.getvalue()
    assert f'Error in <native-procedures>:{line}:' in output.getvalue()
    assert 'Traceback' not in output.getvalue()


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
node instance : TestLibrary.InstanceProbe;
node overridden : TestLibrary.OverrideProbe;
node class_only : TestLibrary.ClassProbe;
node shared : TestLibrary.SharedProbe;
node shared_again : TestLibrary.SharedProbe;
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


def missing_context():
    pass


def needs_instance(self, *, context):
    pass


def required_argument(*, context, extra):
    pass


def positional_only(context, /):
    pass


async def asynchronous(*, context):
    pass


def generator(*, context):
    yield None


async def async_generator(*, context):
    yield None


@wraps(asynchronous)
def wrapped_asynchronous(*, context):
    return asynchronous(context=context)


descriptor_accesses = []


def property_method(self):
    descriptor_accesses.append('property')
    raise AssertionError('Property must not execute during compilation')


class Descriptor:
    def __get__(self, instance, owner):
        descriptor_accesses.append('descriptor')
        raise AssertionError('Descriptor must not execute during compilation')


invalid_methods = [
    (False, staticmethod(missing_context), 'Invalid signature'),
    (False, needs_instance, 'Invalid signature'),
    (False, staticmethod(required_argument), 'Invalid signature'),
    (False, staticmethod(positional_only), 'Invalid signature'),
    (False, classmethod(required_argument), 'Invalid signature'),
    (False, staticmethod(asynchronous), 'must be synchronous'),
    (False, staticmethod(wrapped_asynchronous), 'must be synchronous'),
    (False, staticmethod(generator), 'cannot be a generator'),
    (False, staticmethod(async_generator), 'cannot be a generator'),
    (False, 7, 'Unsupported native procedure method'),
    (True, property(property_method), 'Unsupported native procedure method'),
    (True, Descriptor(), 'Unsupported native procedure method'),
    (True, staticmethod(required_argument), 'Invalid signature'),
    (True, missing_context, 'Invalid signature'),
]
for has_native_state, method, message in invalid_methods:
    implementations = {}
    native_block_type(implementations, has_native_state=has_native_state)(
        type('InvalidProbe', (), {'selected': method}),
    )
    qualifier = 'native ' if has_native_state else ''
    source = f'''namespace TestLibrary {{
  {qualifier}node type InvalidProbe {{
    native procedure Poll
      as "selected";
  }}
}}
node probe : TestLibrary.InvalidProbe;
'''
    check_invalid(source, implementations, message, 4)
assert not descriptor_accesses

# A subclass attribute hides an inherited method, even if not callable.
implementations = {}
native_block_type(implementations, has_native_state=False)(
    type('HiddenProbe', (Methods,), {'shared': None}),
)
check_invalid('''namespace TestLibrary {
  node type HiddenProbe {
    native procedure shared;
  }
}
node probe : TestLibrary.HiddenProbe;
''', implementations, 'Unsupported native procedure method', 3)
