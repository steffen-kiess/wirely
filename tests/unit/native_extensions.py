import importlib
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple
from unittest import TestCase

import wirely
from wirely.compiler import compile_files, compile_strs
from wirely.compiler import program as program_compiler
from wirely.compiler.extensions import (
    CompilerNativeExtension, NativeConstantFunction, resolve_compiler_extensions,
)
from wirely.compiler.native_contracts import generated_files
from wirely.compiler.native_functions import ConstantFunctionContract
from wirely.generated import extension as builtin_extension
from wirely.runtime.engine import Engine
from wirely.runtime.extensions import NativeFunction, NativeTypes, RuntimeNativeExtension, resolve_extensions
from wirely.runtime.native_api import FunctionContract
from wirely.runtime.signatures import Enum, Function, Int, List, Option, Struct
from wirely.types.value_types import NativeEnum, NativeStruct, OpenIntEnum


checks = TestCase()


def check_resolution():
    first = RuntimeNativeExtension(resources={'Demo.Service': lambda: int})
    second = RuntimeNativeExtension(resources={'Demo.Service': lambda: str})
    for normal, overrides in [([first, second], []), ([], [first, second])]:
        with checks.assertRaisesRegex(ValueError, "Duplicate native resources `Demo.Service`"):
            resolve_extensions(normal, override_extensions=overrides)
    selected = resolve_extensions([first], override_extensions=[second], include_builtin_extensions=False)
    assert selected.resources['Demo.Service']() is str
    assert not selected.functions
    with checks.assertRaises(TypeError):
        selected.resources['Demo.Other'] = lambda: bool
    mutable = {'Demo.Service': lambda: int}
    snapshot = RuntimeNativeExtension(resources=mutable)
    mutable.clear()
    assert snapshot.resources['Demo.Service']() is int
    assert not resolve_compiler_extensions(include_builtin_extensions=False).functions
    for normal, overrides in [([builtin_extension], []), ([], [builtin_extension, builtin_extension])]:
        with checks.assertRaises(ValueError):
            resolve_extensions(normal, override_extensions=overrides)
    with checks.assertRaisesRegex(TypeError, 'native_types'):
        wirely.value_types.boolean.from_native(True)


def value_extension(number):
    class Mode(OpenIntEnum, minimum=0, maximum=255):
        On = 1

    class Payload(NamedTuple):
        modes: wirely.Some[Mode] | None

    signature = Function((Struct('Demo.Payload'),), Struct('Demo.Payload'))
    context = NativeTypes(
        {'Demo.Mode': NativeEnum(Mode, is_open=True, underlying_type='uint8')},
        {'Demo.Payload': NativeStruct(Payload, (Option(Enum('Demo.Mode')),))})

    def implementation(value):
        assert type(value) is Payload and type(value.modes.value) is Mode
        return Payload(wirely.Some(Mode(number)))

    def runtime_call(implementation, binding, arguments):
        return binding.result(implementation(binding.argument(arguments, 0)))

    def constant_call(implementation, arguments, services):
        assert services.native_types.enums['Demo.Mode'].python_type is Mode
        return implementation(*arguments)

    runtime_contract = FunctionContract('Demo.Change', runtime_call)
    constant_contract = ConstantFunctionContract('Demo.Constant', constant_call)
    runtime = RuntimeNativeExtension(
        functions={'Demo.Change': NativeFunction(signature, lambda: runtime_contract.implementation(implementation))},
        enums=context.enums, structs=context.structs)
    compiler = CompilerNativeExtension(functions={
        'Demo.Constant': NativeConstantFunction(signature, lambda: constant_contract.implementation(implementation))})
    return runtime, compiler, Payload, Mode


def check_isolation():
    declarations = '''namespace Demo {
  native open enum type Mode: uint8 { On = 1, }
  native struct type Payload { modes: Option[Mode], }
  native function Change(value: Payload): Payload;
  native constant function Constant(value: Payload): Payload;
}
node type Probe {
  output Value: value Demo.Payload;
  output Constant: value Demo.Payload;
  state mode: Demo.Mode = Demo.Mode.On;
  procedure run reads mode.old as current writes mode, Value, Constant {
    mode = current;
    Value = Demo.Change(Demo.Payload { modes: Some(current) });
    Constant = Demo.Constant(Demo.Payload { modes: Some(Demo.Mode.On) });
  }
}
node probe: Probe {}
'''
    variants = [value_extension(2), value_extension(3)]
    graphs = [compile_strs([('<isolation>', declarations)], extensions=[runtime], compiler_extensions=[compiler])
              for runtime, compiler, _, _ in variants]
    assert all(graph is not None for graph in graphs)
    # Lower after both compilations have finished; retain each compilation's choices.
    programs = [program_compiler.compile_nocatch(graph) for graph in graphs]
    engines = [Engine(replace(program, graph=None), extensions=[runtime])
               for program, (runtime, _, _, _) in zip(programs, variants)]
    for index in (0, 1, 0, 1):
        engine = engines[index]
        runtime, _, payload, mode = variants[index]
        engine.step()
        descriptor = wirely.ValueType(engine.program.symbols.variable_aliases['probe.Value'].value_type)
        value = descriptor.from_native(payload(wirely.Some(mode(index + 2))), native_types=engine.native_types)
        native = value.to_native(native_types=engine.native_types)
        assert type(native) is payload and type(native.modes.value) is mode
        assert descriptor.from_native(native, native_types=engine.native_types) == value
        nested = wirely.value_types.list_of(wirely.value_types.option(descriptor))
        collection = (wirely.Some(native), None)
        assert nested.from_native(collection, native_types=engine.native_types).extract(
            nested, native_types=engine.native_types) == collection
        assert engine.get_value('probe.Value') == (wirely.Some(index + 2),)
        assert engine.get_value('probe.Constant') == (wirely.Some(index + 2),)
        with checks.assertRaisesRegex(ValueError, 'Expected a Payload'):
            descriptor.from_native(variants[1 - index][2](()), native_types=engine.native_types)
    # Selected compiler signatures are checked before implementations are invoked.
    runtime, compiler, _, _ = variants[0]
    wrong = CompilerNativeExtension(functions={
        'Demo.Constant': replace(compiler.functions['Demo.Constant'], signature=Function((), Int(16, False)))})
    diagnostics = []
    graph = compile_strs([('<mismatch>', declarations)], extensions=[runtime], compiler_extensions=[wrong],
                         on_diagnostic=diagnostics.append)
    if graph is not None:
        assert program_compiler.compile(graph, on_diagnostic=diagnostics.append) is None
    assert any('has 1 parameters, expected 0' in item.message for item in diagnostics), diagnostics
    # Compiler duplicate handling is independent of runtime extension categories.
    with checks.assertRaises(ValueError):
        resolve_compiler_extensions([compiler, compiler])
    with checks.assertRaises(ValueError):
        compile_strs([], compiler_extensions=[compiler, compiler])
    selected = resolve_compiler_extensions([compiler], override_extensions=[wrong])
    assert selected.functions['Demo.Constant'].signature == Function((), Int(16, False))


def check_external_generation():
    declarations = '''namespace Demo {
  native open enum type Mode: uint8 { On = 1, }
  native struct type Payload { modes: List[Option[Mode]], format: Std.FormatParts, }
  resource type Service;
  resource type Spare;
  native constant function Seed(): uint16;
  native function Add(value: uint16): uint16;
  node type Counter {
    output Count: value uint16;
    output Added: value uint16;
    binding state: fixed Service;
    binding count: value uint16;
    constant Seed: uint16 = Demo.Seed();
    native initializer create writes state as "state";
    native procedure run reads state as "state", Seed as seed writes count as count;
    procedure add reads count as value writes Count, Added { Count = value; Added = Demo.Add(value); }
  }
}
'''
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / 'library.wrl'
        source.write_text(declarations)
        files = generated_files([source], package='acme', output_root=root)
        # Generation starts without any implementation or generated files.
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        for name in ('acme', 'acme/native', 'acme/functions', 'acme/compiler',
                     'acme/compiler/function_implementations'):
            (root / name).mkdir(exist_ok=True)
            (root / name / '__init__.py').write_text('')
        (root / 'acme/native/demo.py').write_text('''from acme.generated import demo as contracts
from wirely import UInt16
from wirely.runtime.native_api import StateServices, PreparationServices, ProcedureServices

class CounterCreate(contracts.CounterCreate):
    def initialize(self, *, services: StateServices) -> contracts.CounterCreateResult:
        self.context = services.native_types
        return contracts.CounterCreateResult(state=self)

class CounterRun(contracts.CounterRun):
    def prepare(self, *, services: PreparationServices, state: "CounterCreate", seed: UInt16) -> None:
        assert state.context is services.native_types
        self.value = seed

    def run(self, *, services: ProcedureServices, state: "CounterCreate", seed: UInt16) -> contracts.CounterRunResult:
        assert state.context is services.native_types
        return contracts.CounterRunResult(count=UInt16(self.value.value + 1))

Service = CounterCreate  # Used as a resource type.
Spare = CounterCreate  # Used as a resource type.
''')
        (root / 'acme/functions/demo.py').write_text('''from wirely import UInt16

def add(value: UInt16) -> UInt16:
    return UInt16(value.value + 2)
''')
        (root / 'acme/compiler/function_implementations/demo.py').write_text('''from wirely import UInt16
from wirely.compiler.native_functions import FunctionServices

def seed(*, services: FunctionServices) -> UInt16:
    assert 'Demo.Payload' in services.native_types.structs
    return UInt16(3)
''')
        sys.path.insert(0, str(root))
        try:
            runtime = importlib.import_module('acme.generated').extension
            compiler = importlib.import_module('acme.compiler.generated').extension
            assert set(runtime.states) == {'Demo.Counter:create'}
            assert set(runtime.structs) == {'Demo.Payload'}
            assert set(runtime.enums) == {'Demo.Mode'}
            assert set(runtime.resources) == {'Demo.Service', 'Demo.Spare'}
            # Standard value dependencies stay in the standard generated package.
            from acme.generated.structs import DemoPayload
            assert 'StdFormatParts' in str(DemoPayload.__annotations__['format'])
            program_source = root / 'program.wrl'
            program_source.write_text('node counter: Demo.Counter {}\n')
            # Literals of the standard library's opaque structs need their implementations.
            standard = RuntimeNativeExtension(opaque_types=builtin_extension.opaque_types)
            graph = compile_files([source, program_source], extensions=[standard, runtime],
                                  compiler_extensions=[compiler], include_builtin_extensions=False)
            assert graph is not None
            program = replace(program_compiler.compile_nocatch(graph), graph=None)
            engine = Engine(program, extensions=[runtime], include_builtin_extensions=False)
            engine.step()
            engine.assert_value_is('counter.Count', 4)
            engine.assert_value_is('counter.Added', 6)
            assert generated_files([source], package='acme', output_root=root) == files
            # The selected bindings, rather than compiler debug data, are required at runtime.
            with checks.assertRaisesRegex(ValueError, 'Demo.Counter'):
                Engine(program)
        finally:
            sys.path.remove(str(root))
            for name in list(sys.modules):
                if name == 'acme' or name.startswith('acme.'):
                    del sys.modules[name]


check_resolution()
check_isolation()
check_external_generation()


async def run_test():
    pass
