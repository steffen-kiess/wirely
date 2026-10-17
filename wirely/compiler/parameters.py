"""Resolve per-instance parameters and constants on demand."""

from __future__ import annotations

from types import MappingProxyType
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    StaticValue = model.Value | model.Type

from wirely.compiler import model
from wirely.compiler.types import resolve_type


class ParameterValues(dict['model.DeclarationParameter', 'StaticValue']):
    def __init__(self, values: Mapping[model.DeclarationParameter, StaticValue], *,
                 instance: model.NodeTypeInstance | None = None) -> None:
        super().__init__(values)
        self.instance = instance if instance is not None else getattr(values, 'instance', None)
        self._resolved: dict[model.DeclarationParameter, StaticValue] = {}
        self._bindings: dict[model.DeclarationParameter, model.Some | None] = {}
        self._resolving: list[model.DeclarationParameter] = []

    def __contains__(self, parameter: object) -> bool:
        assert isinstance(parameter, model.DeclarationParameter)
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

    def _binding(self, parameter: model.DeclarationParameter) -> model.Some | None:
        if parameter not in self._bindings:
            binding = super().__getitem__(parameter)
            assert isinstance(binding, model.value.OptionalBinding)
            value = binding.resolve_self(self).evaluate()
            self._bindings[parameter] = value
        return self._bindings[parameter]

    def _check_cycle(self, parameter: model.DeclarationParameter) -> None:
        if parameter in self._resolving:
            names = [item.name.id_str for item in self._resolving] + [parameter.name.id_str]
            kind = ('static value' if any(isinstance(item, model.DeclarationConstant)
                                         for item in self._resolving + [parameter])
                    else 'parameter')
            raise model.ProgramError(parameter, f'Cyclic {kind} resolution: {" -> ".join(names)}')

    def __getitem__(self, parameter: model.DeclarationParameter) -> StaticValue:
        if parameter in self._resolved:
            return self._resolved[parameter]
        self._check_cycle(parameter)
        self._resolving.append(parameter)
        try:
            return self._resolve(parameter)
        finally:
            self._resolving.pop()

    def _resolve(self, parameter: model.DeclarationParameter) -> StaticValue:
        source = parameter.default_value or parameter
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
                value = parameter.interface.get_const_value(
                    parameter.default_value, value_type, par_values=self,
                )
            if value is None:
                raise model.ProgramError(parameter, f'Unable to resolve parameter {parameter.name.id_str}')
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


def resolve_parameter(parameter: model.DeclarationParameter,
                      values: Mapping[model.DeclarationParameter, StaticValue]) -> StaticValue:
    if not isinstance(values, ParameterValues):
        values = ParameterValues(values)
    return values[parameter]


def native_parameters(values: Mapping[str, StaticValue]) -> Mapping[str, Any]:
    """Return ValueType parameters as ValueTypes and others as DynamicValues."""
    return MappingProxyType({
        name: (model.ValueType(value) if isinstance(value, model.Type)
               else model.value_types.DynamicValue._make(value.value_type, value.evaluate()))
        for name, value in values.items()
    })
