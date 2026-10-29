"""Helpers for tests: engines and native bindings using the test fixture extension."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import itertools
import sys
import types
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from wirely.compiler import model
from wirely.runtime.engine import Engine
from wirely.runtime.loading import LoadedProgram
from wirely.program.types import TypeStruct

if TYPE_CHECKING:
    from wirely.program.program import Program


_test_contract_modules = itertools.count()


@contextlib.contextmanager
def test_contracts(graph: object) -> Iterator[types.ModuleType]:
    """Generate test contracts locally without changing binding tables."""
    from wirely.compiler import native_contracts
    from wirely.compiler.native_signatures import native_signatures
    from tests.infra.native_extension.generated import extension
    from tests.infra.native_extension.generated import test_library

    declared = native_signatures(cast(model.Scope, graph), 'TestLibrary')
    tables: tuple[dict[str, Any], ...] = (
        {name: entry.signature for name, entry in extension.states.items()},
        {name: entry.signature for name, entry in extension.procedures.items()},
        {name: entry.signature for name, entry in extension.functions.items()},
    )
    generated = [{name: entry for name, entry in cast(Mapping[str, Any], entries).items() if table.get(name) != entry}
                 for table, entries in zip(tables, declared)]
    module = types.ModuleType(f'_wirely_test_contracts_{next(_test_contract_modules)}')
    module.__dict__.update({name: value for name, value in vars(test_library).items() if not name.startswith('__')})
    interfaces = cast(native_contracts.Interfaces, tuple(generated))
    sources = native_contracts.contract_sources(
        interfaces, resource_classes=native_contracts.resource_classes(), strict=False)
    sys.modules[module.__name__] = module
    try:
        for source in sources.values():
            exec(compile(source, f'<{module.__name__}>', 'exec'), module.__dict__)
        module.interfaces = declared  # type: ignore[attr-defined]
        yield module
    finally:
        sys.modules.pop(module.__name__, None)


def test_extension(*, contracts: types.ModuleType | None = None, initializers: Mapping[str, Any] = {},
                   procedures: Mapping[str, Any] = {}, resources: Mapping[str, type] = {},
                   functions: Mapping[str, Any] = {}) -> Any:
    """Build explicit test bindings from contracts and implementations."""
    from wirely.compiler.native_contracts import contract_location
    from wirely.generated import extension
    from wirely.runtime.extensions import NativeFunction, NativeInitializer, NativeProcedure, RuntimeNativeExtension
    from wirely.runtime.native_api import ProcedureImplementation, StateImplementation
    from tests.infra.native_extension.generated import extension as fixture

    tables: tuple[dict[str, Any], ...] = (
        {name: entry.signature for ext in (extension, fixture) for name, entry in ext.states.items()},
        {name: entry.signature for ext in (extension, fixture) for name, entry in ext.procedures.items()},
        {name: entry.signature for ext in (extension, fixture) for name, entry in ext.functions.items()},
    )
    if contracts is not None:
        for table, entries in zip(tables, contracts.interfaces):
            table.update(entries)

    def load(kind: str, name: str, factory: Any) -> Any:
        _, attribute = contract_location(kind, name)
        contract = getattr(contracts, attribute, factory) if contracts is not None else factory
        cls = StateImplementation if kind == 'initializer' else ProcedureImplementation
        return lambda: cls(factory, contract._adapter)

    def loader(value: Any) -> Callable[[], Any]:
        return lambda: value

    return RuntimeNativeExtension(
        states={name: NativeInitializer(tables[0][name], load('initializer', name, factory))
                for name, factory in initializers.items()},
        procedures={name: NativeProcedure(tables[1][name], load('procedure', name, factory))
                    for name, factory in procedures.items()},
        resources={name: loader(cls) for name, cls in resources.items()},
        functions={name: NativeFunction(tables[2][name], loader(value))
                   for name, value in functions.items()},
    )


def test_constant_function(native_name: str, implementation: Callable[..., object], signature: Any) -> Any:
    """Build a constant function entry with an explicit signature."""
    from wirely.compiler.extensions import NativeConstantFunction
    from wirely.compiler.native_functions import ConstantFunctionContract, ConstantFunctionImplementation

    def call(implementation: Callable[..., object], arguments: Sequence[object], services: object) -> object:
        return implementation(*arguments, services=services)

    return NativeConstantFunction(signature, lambda: ConstantFunctionImplementation(
        implementation, ConstantFunctionContract(native_name, call)))


def test_engine(program: Program, graph: object = None, *, services: Mapping[str, object] | None = None,
                **kwargs: Any) -> Engine:
    """Create an engine using the test fixture extension, with fake GPIO chips and the given runtime services."""
    from tests.infra.native_extension.generated import extension
    from tests.infra.bytecode import bytecode_roundtrip_mode, roundtrip_program
    from tests.infra.gpio import FakeChip
    if bytecode_roundtrip_mode.get():
        program = roundtrip_program(program)
    # wirely.native.gpio.OPEN_CHIP_SERVICE, named here so that tests without GPIO do not need gpiod.
    return Engine(LoadedProgram.load(program, extensions=[extension],
                                     services={'wirely.gpio.open_chip': FakeChip, **(services or {})}, **kwargs))


def internal(value: Any) -> Any:
    """Return the internal value of an opaque struct value, also inside options.

    Engine values of opaque structs, such as wirely.Duration, are tuples of their fields.
    """
    if isinstance(value, model.Some):
        return model.Some(internal(value.value))
    if hasattr(value, 'to_fields'):
        return tuple(field.value for field in value.to_fields())
    return value


def send_event_text(source: Any, text: str) -> None:
    """Send an event from a Std.DebugEventSource state, with its payload in the value text format."""
    value = source.value_type.parse(text, native_types=source.services.native_types)
    source.send_event(model.Some(value._value))


@functools.cache
def library_type(name: str) -> TypeStruct:
    """Return a struct type declared by the standard libraries, such as Std.Duration."""
    from wirely.compiler.compilation import compile_strs
    from wirely.compiler.sources import standard_library_sources
    graph = compile_strs(standard_library_sources(['std', 'schedule', 'timer']))
    assert graph is not None
    *namespaces, type_name = name.split('.')
    scope: model.Scope = graph
    for namespace in namespaces:
        scope = scope.libraries[namespace]
    return scope.struct_types[type_name].resolve_fields()


class Proxy:
    """Forward connections at a Unix socket path to a service, until they are cut.

    With forward set to False, new connections are accepted but not forwarded, so that connecting hangs.
    """

    def __init__(self, path: str, target: str) -> None:
        self.path = path
        self.target = target
        self.forward = True
        self.server: asyncio.Server | None = None
        self.writers: set[asyncio.StreamWriter] = set()
        # The number of connections from clients that are still open.
        self.clients = 0

    async def start(self) -> None:
        self.server = await asyncio.start_unix_server(self.handle, path=self.path)

    def cut(self) -> None:
        for writer in self.writers:
            writer.transport.abort()
        self.writers.clear()

    def close(self) -> None:
        """Cut the connections and stop listening; start() listens again."""
        self.cut()
        if self.server is not None:
            self.server.close()
            self.server = None

    async def handle(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        self.clients += 1
        try:
            if not self.forward:
                self.writers.add(client_writer)
                with contextlib.suppress(OSError):
                    while await client_reader.read(65536):
                        pass
                return
            service_reader, service_writer = await asyncio.open_unix_connection(self.target)
            self.writers.update((client_writer, service_writer))

            async def forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                with contextlib.suppress(OSError):
                    while data := await reader.read(65536):
                        writer.write(data)
                        await writer.drain()
                writer.transport.abort()

            await asyncio.gather(forward(client_reader, service_writer), forward(service_reader, client_writer))
        finally:
            self.clients -= 1
