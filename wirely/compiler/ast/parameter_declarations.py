"""Parameter, constant, and assertion declarations."""

import dataclasses
from typing import Any

from wirely.compiler import model

from .base import SourcePosition, CompilerHint, ProgramError, Identifier, Declaration, Value
from .specifiers import OptionalSpecifier
from .values import StringConstant


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationParameter(Declaration):
    optional_specifier: OptionalSpecifier | None
    name: Identifier
    ty: Value
    _: dataclasses.KW_ONLY
    default_value: Value | None = None

    @property
    def is_optional(self) -> bool:
        return self.optional_specifier is not None or self.default_value is not None

    def __post_init__(self) -> None:
        if self.optional_specifier is not None and self.default_value is not None:
            raise Exception('self.optional_specifier is not None and self.default_value is not None')

    @staticmethod
    def with_default_value(name: Identifier, ty: Value, default_value: Value, *,
                           source_position: SourcePosition) -> 'DeclarationParameter':
        return DeclarationParameter(optional_specifier=None, name=name, ty=ty, default_value=default_value, source_position=source_position)


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationConstant(DeclarationParameter):
    @staticmethod
    def make(name: Identifier, ty: Value, initializer: Value, *, source_position: SourcePosition) -> 'DeclarationConstant':
        return DeclarationConstant(None, name, ty, default_value=initializer, source_position=source_position)

    def collect_declarations(self, scope: Any) -> None:
        # Node types collect their constants as parameters.
        if not isinstance(scope, model.Namespace):
            return
        name = self.name.id_str
        previous = scope.constants.get(name)
        if previous is not None:
            raise ProgramError(self.name, f'Duplicate constant name `{name}`', hints=[
                CompilerHint(previous.declaration, 'Previous declaration was here'),
            ])
        scope.constants[name] = model.NamespaceConstant(self, scope)

    def prepare_interface(self, scope: Any) -> None:
        if not isinstance(scope, model.Namespace):
            return
        name = self.name.id_str
        constant = scope.constants[name]
        if constant.declaration is not self:
            return  # A duplicate, reported when it was collected.
        for other in (scope.native_functions, scope.node_types, scope.libraries, scope.struct_types,
                      scope.enum_types, scope.resource_types):
            if name in other:
                raise ProgramError(self.name, f'Duplicate name `{name}`')
        constant.check()


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DeclarationAssertion(Declaration):
    condition: Value
    message: StringConstant | None

    def register_interface_declarations(self, interface: 'model.NodeType') -> None:
        interface.assertions.append(self)

    def bind_body(self, graph: 'model.NodeType') -> None:
        from ..expressions import compile_constant
        compile_constant(graph, self.condition, model.types.boolean, {})

    def evaluate(self, instance: 'model.NodeTypeInstance') -> None:
        from ..expressions import compile_constant
        with self.context:
            try:
                condition = compile_constant(instance.node_type, self.condition,
                                             model.types.boolean, instance.static_values)
                try:
                    result = condition.resolve_self(instance.static_values).evaluate()
                except (ValueError, ArithmeticError) as error:
                    raise ProgramError(self.condition, str(error)) from error
                if not result:
                    message = 'Compile-time assertion failed'
                    if self.message is not None:
                        message += ': ' + self.message.value
                    raise ProgramError(self, message)
            except ProgramError as error:
                hint = instance.instantiation_hint()
                if hint is not None:
                    error.hints = (*error.hints, hint)
                raise
