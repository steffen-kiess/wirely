"""Parameter, constant, and assertion declarations."""

import dataclasses

from wirely.compiler import model

from .base import SourcePosition, ProgramError, Identifier, Declaration, Value, syntax
from .specifiers import OptionalSpecifier
from .values import StringConstant


@syntax
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


@syntax
class DeclarationConstant(DeclarationParameter):
    @staticmethod
    def make(name: Identifier, ty: Value, initializer: Value, *, source_position: SourcePosition) -> 'DeclarationConstant':
        return DeclarationConstant(None, name, ty, default_value=initializer, source_position=source_position)


@syntax
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
                    error.hints = [*error.hints, hint]
                raise
