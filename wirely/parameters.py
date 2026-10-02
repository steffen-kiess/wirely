"""Resolve compiler parameter bindings and declaration defaults."""

import wirely as blocks


class ParameterValues(dict):
    def __init__(self, values):
        super().__init__(values)
        self._resolved = {}
        self._resolving = []

    def __contains__(self, parameter):
        return (super().__contains__(parameter)
                or parameter.default_value is not None)

    def __getitem__(self, parameter):
        if parameter in self._resolved:
            return self._resolved[parameter]
        if parameter in self._resolving:
            names = [item.name.id_str for item in self._resolving] + [parameter.name.id_str]
            raise blocks.ProgramError(parameter, f'Cyclic parameter resolution: {" -> ".join(names)}')
        self._resolving.append(parameter)
        try:
            if super().__contains__(parameter):
                value = super().__getitem__(parameter)
            elif parameter.default_value is not None:
                value_type = parameter.type_resolved.resolve_self(self)
                if value_type == blocks.types.ValueType:
                    value = parameter.interface.resolve_type(parameter.default_value, par_values=self)
                else:
                    value = parameter.interface.get_const_value(
                        parameter.default_value, value_type, par_values=self,
                    )
            else:
                raise blocks.ProgramError(parameter, f'Unable to resolve parameter {parameter.name.id_str}')
            value = value.resolve_self(par_values=self)
            self._resolved[parameter] = value
            return value
        finally:
            self._resolving.pop()


def resolve_parameter(parameter, values):
    if not isinstance(values, ParameterValues):
        values = ParameterValues(values)
    return values[parameter]
