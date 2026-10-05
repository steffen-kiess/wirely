"""Resolve per-instance parameters and constants on demand."""

from types import MappingProxyType

from wirely.compiler import model as blocks
from wirely.compiler.types import resolve_type


class ParameterValues(dict):
    def __init__(self, values):
        super().__init__(values)
        self._resolved = {}
        self._bindings = {}
        self._resolving = []

    def __contains__(self, parameter):
        if parameter.default_value is not None:
            return True
        if not super().__contains__(parameter):
            return False
        if isinstance(super().__getitem__(parameter), blocks.value.OptionalBinding):
            self._check_cycle(parameter)
            self._resolving.append(parameter)
            try:
                return self._binding(parameter) is not None
            finally:
                self._resolving.pop()
        return True

    def _binding(self, parameter):
        if parameter not in self._bindings:
            binding = super().__getitem__(parameter)
            value = binding.resolve_self(self).evaluate()
            self._bindings[parameter] = value
        return self._bindings[parameter]

    def _check_cycle(self, parameter):
        if parameter in self._resolving:
            names = [item.name.id_str for item in self._resolving] + [parameter.name.id_str]
            kind = ('static value' if any(isinstance(item, blocks.DeclarationConstant)
                                         for item in self._resolving + [parameter])
                    else 'parameter')
            raise blocks.ProgramError(parameter, f'Cyclic {kind} resolution: {" -> ".join(names)}')

    def __getitem__(self, parameter):
        if parameter in self._resolved:
            return self._resolved[parameter]
        self._check_cycle(parameter)
        self._resolving.append(parameter)
        try:
            return self._resolve(parameter)
        finally:
            self._resolving.pop()

    def _resolve(self, parameter):
        source = parameter.default_value or parameter
        with source.context:
            if super().__contains__(parameter):
                value = super().__getitem__(parameter)
                if isinstance(value, blocks.value.OptionalBinding):
                    option = self._binding(parameter)
                    if option is None:
                        value = None
                    else:
                        value_type = resolve_type(parameter.type_resolved, self)
                        value = blocks.ConstantValue(value_type, option.value, ast_element=value.ast_element)
            else:
                value = None
            if value is None and parameter.default_value is not None:
                value_type = resolve_type(parameter.type_resolved, self)
                value = parameter.interface.get_const_value(
                    parameter.default_value, value_type, par_values=self,
                )
            if value is None:
                raise blocks.ProgramError(parameter, f'Unable to resolve parameter {parameter.name.id_str}')
            value = (resolve_type(value, self) if isinstance(value, blocks.Type)
                     else value.resolve_self(par_values=self))
            if isinstance(value, blocks.Value):
                value_type = resolve_type(parameter.type_resolved, self)
                blocks.value.check_value_type(value.value_type, value_type, value.ast_element)
                try:
                    concrete = value_type.verify_value(value.evaluate())
                except (ValueError, ArithmeticError) as error:
                    raise blocks.ProgramError(value.ast_element, str(error)) from error
                value = (concrete if value_type == blocks.types.ValueType else
                         blocks.ConstantValue(value_type, concrete, ast_element=value.ast_element))
            self._resolved[parameter] = value
            return value


def resolve_parameter(parameter, values):
    if not isinstance(values, ParameterValues):
        values = ParameterValues(values)
    return values[parameter]


def native_parameters(values):
    return MappingProxyType({
        name: (blocks.ValueType(value) if isinstance(value, blocks.Type)
               else blocks.value_types._to_native(value.value_type, value.evaluate()))
        for name, value in values.items()
    })
