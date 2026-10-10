"""Parameter, constant, and assertion declarations."""

import typing

from wirely.compiler import model
from wirely.util import check_parameters

from .base import SourcePosition, CompilerHint, ProgramError, Identifier, Declaration
from .specifiers import OptionalSpecifier


class DeclarationParameter(Declaration):
    @check_parameters
    def __init__(self, optional_specifier: typing.Optional[OptionalSpecifier], name: Identifier, ty, *, default_value=None, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.ty = ty
        self.optional_specifier = optional_specifier
        self.default_value = default_value

        if self.optional_specifier is not None and self.default_value is not None:
            raise Exception('self.optional_specifier is not None and self.default_value is not None')

        self.is_optional = self.optional_specifier is not None or self.default_value is not None

    @staticmethod
    def with_default_value(name: Identifier, ty, default_value, *, source_position: SourcePosition):
        return DeclarationParameter(optional_specifier=None, name=name, ty=ty, default_value=default_value, source_position=source_position)

    @check_parameters
    def compile_pass1_btd(self, interface):
        self.interface = interface

    def create_io(self, new_graph):
        pass

    def resolve_type(self):
        # Types may refer to parameters declared later, which resolve on demand.
        if hasattr(self, 'type_resolved'):
            return
        self.resolving_type = True
        try:
            # TODO: Is this correct?
            self.type_resolved = self.interface.resolve_type(self.ty)
        finally:
            self.resolving_type = False

    def __repr__(self):
        # return f'Parameter <{self.name}> [{self.name.source_position.start_line}] : <{self.ty}>'
        if not hasattr(self, 'interface'):
            # return f'Parameter <(unknown node).{self.name}> : <{self.ty}>'
            return f'(unknown node).{self.name}'
        else:
            return f'{self.interface.name}.{self.name}'

    # TODO: Clean up compilation passes

    def compile_pass1(self, graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        if self.default_value is not None:
            from ..expressions import compile_constant
            compile_constant(graph, self.default_value, self.type_resolved, {})


class DeclarationConstant(DeclarationParameter):
    def __init__(self, name, ty, initializer, *, source_position):
        super().__init__(None, name, ty, default_value=initializer,
                         source_position=source_position)

    def compile_pass2(self, graph):
        # Check concrete types and expression syntax without running initializers.
        if self.type_resolved == model.types.ValueType:
            graph.get_const_value(self.default_value, self.type_resolved)
        else:
            from ..expressions import compile_constant
            compile_constant(graph, self.default_value, self.type_resolved, {})


class DeclarationAssertion(Declaration):
    def __init__(self, condition, message, *, source_position):
        super().__init__(source_position=source_position)
        self.condition = condition
        self.message = message

    def compile_pass1_btd(self, interface):
        interface.assertions.append(self)

    def create_io(self, new_graph):
        pass

    def compile_pass1(self, graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        from ..expressions import compile_constant
        compile_constant(graph, self.condition, model.types.boolean, {})

    def evaluate(self, instance):
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
                owner = instance.node or instance.node_type.singleton_instance
                if not owner.is_root:
                    name = model.InstanceNode.path_name(instance.path, qualified=True)
                    error.hints = [*error.hints, CompilerHint(
                        owner.instantiation, f'While instantiating {name!r}',
                    )]
                raise
