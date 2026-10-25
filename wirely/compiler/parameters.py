"""Resolve per-instance parameters and constants on demand."""

from __future__ import annotations

from types import MappingProxyType
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    StaticValue = model.Value | model.Type

from wirely.compiler import model
from wirely.compiler.types import resolve_type


class NodeParameter:
    """A parameter or constant of a node type in a compilation."""

    type_resolved: model.Type  # Set by resolve_type().

    def __init__(self, declaration: model.DeclarationParameter, node_type: model.NodeType) -> None:
        self.declaration = declaration
        self.node_type = node_type
        # Set while the type resolves; a type referring to its own parameter is not a type.
        self.resolving_type = False

    @property
    def is_constant(self) -> bool:
        return isinstance(self.declaration, model.DeclarationConstant)

    @property
    def name(self) -> model.Identifier:
        return self.declaration.name

    @property
    def ty(self) -> model.ast.Value:
        return self.declaration.ty

    @property
    def default_value(self) -> model.ast.Value | None:
        return self.declaration.default_value

    @property
    def optional_specifier(self) -> model.OptionalSpecifier | None:
        return self.declaration.optional_specifier

    @property
    def is_optional(self) -> bool:
        return self.declaration.is_optional

    def resolve_type(self) -> None:
        # Types may refer to parameters declared later, which resolve on demand.
        if hasattr(self, 'type_resolved'):
            return
        self.resolving_type = True
        try:
            # TODO: Is this correct?
            self.type_resolved = self.node_type.resolve_type(self.ty)
            if isinstance(self.type_resolved, model.types.TypeResource):
                raise model.ProgramError(self.ty, 'Resources cannot be parameter value types')
        finally:
            self.resolving_type = False

    def bind_body(self) -> None:
        """Check the default value or, for a constant, its initializer."""
        from .expressions import compile_constant
        if self.default_value is None:
            return
        if self.is_constant and self.type_resolved == model.types.ValueType:
            # Check concrete types and expression syntax without running initializers.
            self.node_type.get_const_value(self.default_value, self.type_resolved)
        else:
            compile_constant(self.node_type, self.default_value, self.type_resolved, {})

    def __repr__(self) -> str:
        return f'{self.node_type.name}.{self.name}'


class NamespaceConstant:
    """A constant declared in a namespace, with one value per compilation, evaluated on first use."""

    type_resolved: model.Type  # Set by resolve_type().
    # Constants whose values are being resolved, outermost first.
    _resolving: list[NamespaceConstant] = []

    def __init__(self, declaration: model.DeclarationConstant, namespace: model.Namespace) -> None:
        self.declaration = declaration
        self.namespace = namespace
        self._value: StaticValue | None = None

    @property
    def name(self) -> model.Identifier:
        return self.declaration.name

    def resolve_type(self) -> None:
        if not hasattr(self, 'type_resolved'):
            self.type_resolved = self.namespace.resolve_type(self.declaration.ty)
            if isinstance(self.type_resolved, model.types.TypeResource):
                raise model.ProgramError(self.declaration.ty, 'Resources cannot be constant value types')

    def check(self) -> None:
        """Check the initializer without evaluating it, like NodeParameter.bind_body()."""
        from .expressions import compile_constant
        self.resolve_type()
        initializer = self.declaration.default_value
        assert initializer is not None
        with initializer.context:
            if self.type_resolved == model.types.ValueType:
                self.namespace.get_const_value(initializer, self.type_resolved)
            else:
                compile_constant(self.namespace, initializer, self.type_resolved, {})

    def value(self) -> StaticValue:
        """Return the value, a type for `type` constants, resolving it on first use."""
        if self._value is not None:
            return self._value
        if self in NamespaceConstant._resolving:
            names = [item.name.id_str for item in NamespaceConstant._resolving[NamespaceConstant._resolving.index(self):]]
            names.append(self.name.id_str)
            raise model.ProgramError(self.declaration,
                                     f'Cyclic static value resolution: {" -> ".join(f"`{name}`" for name in names)}')
        NamespaceConstant._resolving.append(self)
        try:
            self.resolve_type()
            initializer = self.declaration.default_value
            assert initializer is not None
            with initializer.context:
                value = self.namespace.get_const_value(initializer, self.type_resolved).resolve_self(par_values={})
                if isinstance(value, model.Value):
                    model.value.check_value_type(value.value_type, self.type_resolved, value.ast_element)
                    try:
                        concrete = self.type_resolved.verify_value(value.evaluate())
                    except (ValueError, ArithmeticError) as error:
                        raise model.ProgramError(value.ast_element, str(error)) from error
                    value = (concrete if self.type_resolved == model.types.ValueType else
                             model.ConstantValue(self.type_resolved, concrete, ast_element=value.ast_element))
            self._value = value
            return value
        finally:
            NamespaceConstant._resolving.pop()

    def __repr__(self) -> str:
        return f'{self.namespace.fullname}.{self.name}'


class ParameterValues(dict['NodeParameter', 'StaticValue']):
    def __init__(self, values: Mapping[NodeParameter, StaticValue], *,
                 instance: model.NodeTypeInstance | None = None) -> None:
        super().__init__(values)
        self.instance = instance if instance is not None else getattr(values, 'instance', None)
        self._resolved: dict[NodeParameter, StaticValue] = {}
        self._bindings: dict[NodeParameter, model.Some | None] = {}
        self._resolving: list[NodeParameter] = []

    def __contains__(self, parameter: object) -> bool:
        assert isinstance(parameter, NodeParameter)
        if parameter.default_value is not None:
            return True
        if not super().__contains__(parameter):
            return False
        if isinstance(super().__getitem__(parameter), model.value.OptionalBinding):
            self._check_cycle(parameter)
            self._resolving.append(parameter)
            try:
                return self._binding(parameter) is not None
            finally:
                self._resolving.pop()
        return True

    def _binding(self, parameter: NodeParameter) -> model.Some | None:
        if parameter not in self._bindings:
            binding = super().__getitem__(parameter)
            assert isinstance(binding, model.value.OptionalBinding)
            value = binding.resolve_self(self).evaluate()
            self._bindings[parameter] = value
        return self._bindings[parameter]

    def _check_cycle(self, parameter: NodeParameter) -> None:
        if parameter in self._resolving:
            names = [item.name.id_str for item in self._resolving] + [parameter.name.id_str]
            kind = ('static value' if any(item.is_constant for item in self._resolving + [parameter])
                    else 'parameter')
            raise model.ProgramError(parameter.declaration, f'Cyclic {kind} resolution: {" -> ".join(f"`{name}`" for name in names)}')

    def __getitem__(self, parameter: NodeParameter) -> StaticValue:
        if parameter in self._resolved:
            return self._resolved[parameter]
        self._check_cycle(parameter)
        self._resolving.append(parameter)
        try:
            return self._resolve(parameter)
        finally:
            self._resolving.pop()

    def _resolve(self, parameter: NodeParameter) -> StaticValue:
        source = parameter.default_value or parameter.declaration
        with source.context:
            value: StaticValue | None
            if super().__contains__(parameter):
                value = super().__getitem__(parameter)
                if isinstance(value, model.value.OptionalBinding):
                    option = self._binding(parameter)
                    if option is None:
                        value = None
                    else:
                        value_type = resolve_type(parameter.type_resolved, self)
                        value = model.ConstantValue(value_type, option.value, ast_element=value.ast_element)
            else:
                value = None
            if value is None and parameter.default_value is not None:
                value_type = resolve_type(parameter.type_resolved, self)
                value = parameter.node_type.get_const_value(
                    parameter.default_value, value_type, par_values=self,
                )
            if value is None:
                raise model.ProgramError(parameter.declaration, f'Unable to resolve parameter `{parameter.name.id_str}`')
            value = (resolve_type(value, self) if isinstance(value, model.Type)
                     else value.resolve_self(par_values=self))
            if isinstance(value, model.Value):
                value_type = resolve_type(parameter.type_resolved, self)
                model.value.check_value_type(value.value_type, value_type, value.ast_element)
                try:
                    concrete = value_type.verify_value(value.evaluate())
                except (ValueError, ArithmeticError) as error:
                    raise model.ProgramError(value.ast_element, str(error)) from error
                value = (concrete if value_type == model.types.ValueType else
                         model.ConstantValue(value_type, concrete, ast_element=value.ast_element))
            self._resolved[parameter] = value
            return value


def resolve_parameter(parameter: NodeParameter,
                      values: Mapping[NodeParameter, StaticValue]) -> StaticValue:
    if not isinstance(values, ParameterValues):
        values = ParameterValues(values)
    return values[parameter]


def native_parameters(values: Mapping[str, StaticValue]) -> Mapping[str, Any]:
    """Return `type` parameters as ValueTypes and others as DynamicValues."""
    return MappingProxyType({
        name: (model.ValueType(value) if isinstance(value, model.Type)
               else model.value_types.DynamicValue._make(value.value_type, value.evaluate()))
        for name, value in values.items()
    })
