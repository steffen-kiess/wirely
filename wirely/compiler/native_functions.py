"""Implementations and invocation of declared native functions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar


from . import model

if TYPE_CHECKING:
    from .diagnostics import WarningSink

F = TypeVar('F', bound=Callable[..., object])



class FunctionServices:
    """Compilation services for one call of a constant native function."""

    __slots__ = ('_function_name', '_services', '_call', '_arguments', '_warnings')

    def __init__(self, *, function_name: str, services: Mapping[str, object], call: model.ast.SourceElement,
                 arguments: Sequence[object], warnings: WarningSink) -> None:
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
        raise model.ProgramError(declaration, f'Missing implementation for function {name!r}')


def validate_collisions(declarations: Iterable[model.Declaration]) -> None:
    scopes: dict[Any, dict] = {}

    def visit(items: Iterable[model.Declaration], scope: tuple[str | model.NodeType, ...]) -> None:
        names = scopes.setdefault(scope, {})
        for item in items:
            name = getattr(item, 'name', None)
            if name is not None:
                key = name.id_str
                previous = names.get(key)
                if previous is not None and (isinstance(item, model.DeclarationNativeFunction)
                                             or isinstance(previous, model.DeclarationNativeFunction)):
                    raise model.ProgramError(item, f'Duplicate or conflicting function declaration {key!r}')
                names[key] = item
            if isinstance(item, model.DeclarationLibrary):
                visit(item.declarations, scope + (item.name.id_str,))
            elif isinstance(item, model.NodeType):
                visit(item.declarations, scope + (item,))

    visit(declarations, ())


def validate_native_names(declarations: Iterable[model.Declaration]) -> None:
    """Reject native names shared by two node types, resource types, enum types, struct types, or functions."""
    names: dict[tuple[str, str], Any] = {}

    # Native declarations have different concrete classes but share name and alias fields.
    def register(kind: str, item: Any, path: tuple[str, ...]) -> None:
        name = item.alias.value if item.alias is not None else '.'.join(path + (item.name.id_str,))
        previous = names.setdefault((kind, name), item)
        # Wirely name checks report clashes between unkeyed declarations.
        if previous is not item and (item.alias is not None or previous.alias is not None):
            raise model.ProgramError(item.alias or item.name, f'Duplicate native name {name!r}', hints=[
                model.CompilerHint(previous.alias or previous.name, 'Previous declaration was here'),
            ])

    def visit(items: Iterable[model.Declaration], path: tuple[str, ...]) -> None:
        for item in items:
            if isinstance(item, model.DeclarationLibrary):
                visit(item.declarations, path + (item.name.id_str,))
            elif isinstance(item, model.NodeType):
                if item.is_native:
                    register('node type', item, path)
                elif item.alias is not None:
                    raise model.ProgramError(item.alias, 'Node type keys require native state')
                assert item.name is not None
                visit(item.declarations, path + (item.name.id_str,))
            elif isinstance(item, model.DeclarationResourceType):
                register('resource type', item, path)
            elif isinstance(item, model.DeclarationEnumType) and item.is_native:
                register('enum type', item, path)
            elif isinstance(item, model.DeclarationStructType) and item.is_native:
                register('struct type', item, path)
            elif isinstance(item, model.DeclarationNativeFunction):
                register('function', item, path)

    visit(declarations, ())


def get_constant_function(native_name: str) -> ConstantFunctionImplementation:
    """Load the generated binding of a constant function."""
    from .native_bindings import functions
    loader = functions.get(native_name)
    if loader is None:
        raise ValueError(f'Cannot find native function {native_name!r}')
    return loader()


def resolve_implementation(function: model.DeclarationNativeFunction,
                           call: model.ast.SourceElement) -> ConstantFunctionImplementation:
    # Declaration objects belong to one compilation, so this cache cannot leak.
    if hasattr(function, '_resolved_implementation'):
        return function._resolved_implementation
    try:
        binding = get_constant_function(function.native_name)
    except (ImportError, TypeError, ValueError) as error:
        raise model.ProgramError(call, f'Cannot load function {function.function_name!r}: {error}') from error
    function._resolved_implementation = binding
    return binding


def invoke(function: model.DeclarationNativeFunction, values: Sequence[object], sources: Sequence[object],
           call: model.ast.SourceElement) -> object:
    from wirely.runtime.native_values import from_native, to_native

    binding = resolve_implementation(function, call)
    name = function.function_name
    try:
        arguments = [to_native(None, value_type, value)  # type: ignore[arg-type]  # Values are internal.
                     for value_type, value in zip(function.parameter_types, values)]
    except ValueError as error:
        raise model.ProgramError(call, f'Cannot pass the arguments of function {name!r}: {error}') from error
    warnings = function.scope.coordinator.warnings
    assert warnings is not None
    services = FunctionServices(function_name=function.native_name, services=function.scope.services, call=call,
                                arguments=sources, warnings=warnings)
    try:
        result = binding.contract.call(binding.implementation, arguments, services)
    except model.ProgramError:
        raise
    except (ValueError, ArithmeticError, OSError) as error:
        raise model.ProgramError(call, str(error)) from error
    try:
        return from_native(None, function.return_type, result)
    except ValueError as error:
        raise model.ProgramError(call, f'Function {name!r} expected result of type {function.return_type}: '
                                 f'{error}') from error


def invoke_runtime_function(function: model.DeclarationNativeFunction, values: Sequence[object],
                            call: model.ast.SourceElement) -> object:
    """Evaluate a runtime-callable native function during compilation."""
    from wirely.functions import bind_native_function
    name = function.function_name
    if not hasattr(function, '_runtime_implementation'):
        try:
            function._runtime_implementation = bind_native_function(
                function.native_name, function.parameter_types, function.return_type)
        except (ImportError, ValueError) as error:
            raise model.ProgramError(call, f'Cannot load function {name!r}: {error}') from error
    try:
        return function._runtime_implementation(values)
    except Exception as error:
        # Runtime functions must not fail, so an exception is a library error.
        inner = RuntimeError(f'Internal error in function {name!r}: {type(error).__name__}: {error}')
        inner.__cause__ = error
        raise model.ast.InternalCompilerError(call, inner) from error
