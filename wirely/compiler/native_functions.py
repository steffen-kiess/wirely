"""Implementations and invocation of declared native functions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar


from . import model
from ..runtime.extensions import NativeTypes

if TYPE_CHECKING:
    from .diagnostics import WarningSink

F = TypeVar('F', bound=Callable[..., object])



class FunctionServices:
    """Compilation services for one call of a constant native function."""

    __slots__ = ('_function_name', '_services', '_call', '_arguments', '_warnings', 'native_types')

    def __init__(self, *, function_name: str, services: Mapping[str, object], call: model.ast.SourceElement,
                 arguments: Sequence[object], warnings: WarningSink, native_types: NativeTypes) -> None:
        self.native_types = native_types
        self._function_name = function_name
        self._services = services
        self._call = call
        self._arguments = tuple(arguments)  # Call-site expressions in declaration order.
        self._warnings = warnings

    @property
    def function_name(self) -> str:
        """The native name, such as 'KNX.ParseGroupAddress'."""
        return self._function_name

    @property
    def services(self) -> Mapping[str, object]:
        """The services supplied to compile_files() or compile_strs()."""
        return self._services

    @property
    def source_path(self) -> str | None:
        """The path of the file containing the call, if it is a file."""
        return self._call.source_position.source_path

    def _source(self, argument: int | None) -> model.ast.SourceElement:
        if argument is None:
            return self._call
        if type(argument) is not int or not 0 <= argument < len(self._arguments):
            raise ValueError(f'Invalid function argument index {argument!r}')
        source = self._arguments[argument]
        assert isinstance(source, model.ast.SourceElement)
        return source

    def error(self, message: str, *, argument: int | None = None) -> model.ProgramError:
        """Return an error at the call or at an argument (from 0), to raise."""
        return model.ProgramError(self._source(argument), message)

    def warn(self, message: str, *, argument: int | None = None) -> None:
        """Report a NativeFunction warning at the call or at an argument."""
        self._warnings.warn('NativeFunction', self._source(argument), message)

    def __repr__(self) -> str:
        return f'FunctionServices({self._function_name!r})'


class ConstantFunctionContract(Generic[F]):
    """The contract of a constant native function, whose implementations have type F."""

    __slots__ = ('name', '_call')

    def __init__(self, name: str, call: Callable[[F, Sequence[object], FunctionServices], object]) -> None:
        self.name = name  # Native name.
        self._call = call

    def call(self, implementation: F, arguments: Sequence[object], services: FunctionServices) -> object:
        """Call an implementation with native arguments and return its native result."""
        return self._call(implementation, arguments, services)

    def implementation(self, implementation: F) -> ConstantFunctionImplementation:
        """Bind a statically checked implementation to this contract."""
        return ConstantFunctionImplementation(implementation, self)

    def __repr__(self) -> str:
        return f'ConstantFunctionContract({self.name!r})'


@dataclass(frozen=True, slots=True)
class ConstantFunctionImplementation:
    implementation: Callable[..., object]
    contract: ConstantFunctionContract[Any]


def validate_entry(name: str, declaration: model.DeclarationNativeFunction) -> None:
    # Loading is deferred until evaluation; generation must work before bindings exist.
    if not name.rpartition('.')[0]:
        raise model.ProgramError(declaration, f'Missing implementation for function `{name}`')


def validate_collisions(declarations: Iterable[model.Declaration]) -> None:
    scopes: dict[Any, dict] = {}

    def visit(items: Iterable[model.Declaration], scope: tuple[str | model.DeclarationNodeType, ...]) -> None:
        names = scopes.setdefault(scope, {})
        for item in items:
            name = getattr(item, 'name', None)
            if name is not None:
                key = name.id_str
                previous = names.get(key)
                if previous is not None and (isinstance(item, model.DeclarationNativeFunction)
                                             or isinstance(previous, model.DeclarationNativeFunction)):
                    raise model.ProgramError(item, f'Duplicate or conflicting function declaration `{key}`')
                names[key] = item
            if isinstance(item, model.DeclarationLibrary):
                visit(item.declarations, scope + (item.name.id_str,))
            elif isinstance(item, model.DeclarationNodeType):
                visit(item.declarations, scope + (item,))
            elif isinstance(item, model.DeclarationEnumType):
                enums.append((scope, item))

    enums: list[tuple[tuple, model.DeclarationEnumType]] = []
    visit(declarations, ())
    # Enum.Name refers to a member, so the enum's namespace cannot declare a function with that name.
    for scope, enum in enums:
        names = scopes.get(scope + (enum.name.id_str,), {})
        for member in enum.members:
            function = names.get(member.name.id_str)
            if isinstance(function, model.DeclarationNativeFunction):
                raise model.ProgramError(function, f'Function `{member.name.id_str}` conflicts with a member of '
                                         f'enum type `{enum.name.id_str}`', hints=[
                    model.CompilerHint(member, 'Enum member was here'),
                ])


def validate_native_names(declarations: Iterable[model.Declaration]) -> None:
    """Reject native names shared by two initializers, resource types, enum types, struct types, opaque structs, or functions."""
    names: dict[tuple[str, str], Any] = {}

    # Native declarations have different concrete classes but share name and alias fields.
    def register(kind: str, item: Any, path: tuple[str, ...]) -> None:
        name = item.alias.value if item.alias is not None else '.'.join(path + (item.name.id_str,))
        previous = names.setdefault((kind, name), item)
        # Wirely name checks report clashes between unkeyed declarations.
        if previous is not item and (item.alias is not None or previous.alias is not None):
            raise model.ProgramError(item.alias or item.name, f'Duplicate native name `{name}`', hints=[
                model.CompilerHint(previous.alias or previous.name, 'Previous declaration was here'),
            ])

    def visit(items: Iterable[model.Declaration], path: tuple[str, ...]) -> None:
        for item in items:
            if isinstance(item, model.DeclarationLibrary):
                visit(item.declarations, path + (item.name.id_str,))
            elif isinstance(item, model.DeclarationNodeType):
                assert item.name is not None
                prefix = item.alias.value if item.alias is not None else '.'.join(path + (item.name.id_str,))
                for member in item.declarations:
                    if not isinstance(member, model.DeclarationInitializer):
                        continue
                    name = member.alias.value if member.alias is not None else f'{prefix}:{member.name.id_str}'
                    keyed = member.alias is not None or item.alias is not None
                    previous, previous_keyed = names.setdefault(('initializer', name), (member, keyed))
                    # Wirely name checks report clashes between unkeyed declarations.
                    if previous is not member and (keyed or previous_keyed):
                        raise model.ProgramError(member.alias or member.name, f'Duplicate native name `{name}`', hints=[
                            model.CompilerHint(previous.alias or previous.name, 'Previous declaration was here'),
                        ])
                visit(item.declarations, path + (item.name.id_str,))
            elif isinstance(item, model.DeclarationResourceType):
                register('resource type', item, path)
            elif isinstance(item, model.DeclarationEnumType) and item.is_native:
                register('enum type', item, path)
            elif isinstance(item, model.DeclarationStructType) and item.is_native:
                register('struct type', item, path)
            elif isinstance(item, model.DeclarationStructType) and item.is_opaque:
                register('opaque struct type', item, path)
            elif isinstance(item, model.DeclarationNativeFunction):
                register('function', item, path)

    visit(declarations, ())


def resolve_implementation(function: model.CompiledFunction,
                           call: model.ast.SourceElement) -> ConstantFunctionImplementation:
    if function.constant_implementation is not None:
        return function.constant_implementation
    try:
        entry = function.scope.coordinator.global_node_type.compiler_native_extension.functions.get(function.native_name)
        if entry is None:
            raise ValueError(f'Cannot find native function `{function.native_name}`')
        from ..runtime.signatures import validate_function
        validate_function(function.native_name, entry.signature, function.parameter_types, function.return_type)
        binding = entry.load()
    except (ImportError, TypeError, ValueError) as error:
        raise model.ProgramError(call, f'Cannot load function `{function.function_name}`: {error}') from error
    function.constant_implementation = binding
    return binding


def invoke(function: model.CompiledFunction, values: Sequence[object], sources: Sequence[object],
           call: model.ast.SourceElement) -> object:
    from wirely.runtime.native_values import from_native, to_native

    native_types = function.scope.coordinator.global_node_type.native_extension.native_types
    binding = resolve_implementation(function, call)
    name = function.function_name
    try:
        arguments = [to_native(None, value_type, value, native_types=native_types)  # type: ignore[arg-type]  # Values are internal.
                     for value_type, value in zip(function.parameter_types, values)]
    except ValueError as error:
        raise model.ProgramError(call, f'Cannot pass the arguments of function `{name}`: {error}') from error
    warnings = function.scope.coordinator.warnings
    assert warnings is not None
    services = FunctionServices(function_name=function.native_name, services=function.scope.services, call=call,
                                arguments=sources, warnings=warnings, native_types=native_types)
    try:
        result = binding.contract.call(binding.implementation, arguments, services)
    except model.ProgramError:
        raise
    except (ValueError, ArithmeticError, OSError) as error:
        raise model.ProgramError(call, str(error)) from error
    try:
        return from_native(None, function.return_type, result, native_types=native_types)
    except ValueError as error:
        raise model.ProgramError(call, f'Function `{name}` expected result of type `{function.return_type}`: '
                                 f'{error}') from error


def runtime_implementation(function: model.CompiledFunction, call: model.ast.SourceElement) -> Callable[..., object]:
    if function.runtime_implementation is None:
        from wirely.functions import bind_native_function
        try:
            function.runtime_implementation = bind_native_function(
                function.native_name, function.parameter_types, function.return_type,
                extension=function.scope.coordinator.global_node_type.native_extension)
        except (ImportError, ValueError) as error:
            raise model.ProgramError(call, f'Cannot load function `{function.function_name}`: {error}') from error
    return function.runtime_implementation


def invoke_runtime_function(function: model.CompiledFunction, values: Sequence[object],
                            call: model.ast.SourceElement) -> object:
    """Evaluate a runtime-callable native function during compilation."""
    name = function.function_name
    implementation = runtime_implementation(function, call)
    try:
        return implementation(values)
    except Exception as error:
        # Runtime functions must not fail, so an exception is a library error.
        inner = RuntimeError(f'Internal error in function `{name}`: {type(error).__name__}: {error}')
        inner.__cause__ = error
        raise model.ast.InternalCompilerError(call, inner) from error
