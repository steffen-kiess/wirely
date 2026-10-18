"""Native function declarations."""

import dataclasses
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..native_functions import ConstantFunctionImplementation

from wirely.compiler import model

from .base import SourceElement, SyntaxElement, ProgramError, Declaration, Identifier, SourcePosition, Value, syntax
from .values import StringConstant


@syntax
class FunctionArgument(SyntaxElement):
    name: Identifier
    ty: Value


@syntax
class DeclarationNativeFunction(Declaration):
    name: Identifier
    alias: StringConstant | None
    arguments: tuple[FunctionArgument, ...]
    return_type_ast: Value
    _: dataclasses.KW_ONLY
    is_constant: bool = False

    @staticmethod
    def create_constant(name: Identifier, alias: StringConstant | None, arguments: Sequence[FunctionArgument],
                        return_type: Value, *, source_position: SourcePosition) -> 'DeclarationNativeFunction':
        return DeclarationNativeFunction(name, alias, tuple(arguments), return_type, is_constant=True,
                                         source_position=source_position)

    def collect_declarations(self, graph: Any) -> None:
        from ..native_functions import validate_entry
        if not isinstance(graph, model.ast.Namespace) and graph.name is not None:
            raise ProgramError(self, 'Native functions are only allowed at root or in namespaces')
        function = CompiledFunction(self, graph)
        if function.function_name == 'Some':
            raise ProgramError(self.name, 'Some is reserved for the Option constructor')
        names = [argument.name.id_str for argument in self.arguments]
        if len(set(names)) != len(names):
            raise ProgramError(self, 'Duplicate function argument name')
        if self.is_constant:
            validate_entry(function.native_name, self)
        graph.native_functions[self.name.id_str] = function

    def prepare_interface(self, graph: Any) -> None:
        graph.native_functions[self.name.id_str].resolve_signature()


class CompiledFunction:
    """A native function declared in a scope, in a compilation."""

    # Set by resolve_signature().
    parameter_types: list['model.types.Type']
    return_type: 'model.types.Type'

    def __init__(self, declaration: DeclarationNativeFunction, scope: Any) -> None:
        self.declaration = declaration
        self.scope = scope
        self.function_name = '.'.join(getattr(scope, 'fullname_tuple', ()) + (declaration.name.id_str,))
        self.native_name = declaration.alias.value if declaration.alias is not None else self.function_name
        # Loaded on first use.
        self.constant_implementation: 'ConstantFunctionImplementation | None' = None
        self.runtime_implementation: Callable[..., object] | None = None

    @property
    def is_constant(self) -> bool:
        return self.declaration.is_constant

    def resolve_signature(self) -> 'CompiledFunction':
        if not hasattr(self, 'parameter_types'):
            self.parameter_types = [self.scope.resolve_type(argument.ty) for argument in self.declaration.arguments]
            self.return_type = self.scope.resolve_type(self.declaration.return_type_ast)
            if any(ty.contains_parameters for ty in self.parameter_types + [self.return_type]):
                raise ProgramError(self.declaration, 'Native functions require fixed value types')
            if not self.is_constant:
                for ty in self.parameter_types + [self.return_type]:
                    if not ty.is_runtime_type:
                        raise ProgramError(self.declaration,
                                           f'Function {self.function_name!r} uses parameter-only type {ty}; '
                                           'declare it native constant function')
        return self

    def invoke(self, values: Sequence[object], sources: Sequence[object], call: SourceElement) -> object:
        if self.is_constant:
            from ..native_functions import invoke
            return invoke(self, values, sources, call)
        from ..native_functions import invoke_runtime_function
        return invoke_runtime_function(self, values, call)

    def __repr__(self) -> str:
        return repr(self.declaration)
