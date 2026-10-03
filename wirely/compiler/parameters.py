"""Resolve compiler parameter bindings and declaration defaults."""

from types import MappingProxyType

from wirely.compiler import model as blocks
from wirely.compiler.types import resolve_type


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
                value_type = resolve_type(parameter.type_resolved, self)
                if value_type == blocks.types.ValueType:
                    value = parameter.interface.resolve_type(parameter.default_value, par_values=self)
                else:
                    value = parameter.interface.get_const_value(
                        parameter.default_value, value_type, par_values=self,
                    )
            else:
                raise blocks.ProgramError(parameter, f'Unable to resolve parameter {parameter.name.id_str}')
            value = (resolve_type(value, self) if isinstance(value, blocks.Type)
                     else value.resolve_self(par_values=self))
            self._resolved[parameter] = value
            return value
        finally:
            self._resolving.pop()


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


def parameter_origins(instance):
    arguments = {}
    if instance.block is not None:
        arguments = {argument.name.id_str: argument.value
                     for argument in instance.block.instantiation.instantiation.parameters}
    origins = {}

    def origin(parameter):
        if parameter in origins:
            return origins[parameter]
        expression = arguments.get(parameter.name.id_str, parameter.default_value)
        result = (expression if expression is not None else parameter, ())
        # Record a fallback before following references to avoid provenance cycles.
        origins[parameter] = result
        if isinstance(expression, blocks.Reference) and expression.length == 1:
            scope = (instance.block.graph if parameter.name.id_str in arguments
                     else instance.block_type)
            source = scope.parsed_parameters.get(expression.identifiers[0].id_str)
            if source is not None and source is not parameter:
                if source.interface is instance.block_type:
                    source_origin = origin(source)
                else:
                    parent = instance.parent_instance
                    while parent is not None and source not in parent.parameter_origins:
                        parent = parent.parent_instance
                    source_origin = parent.parameter_origins[source] if parent is not None else None
                if source_origin is not None:
                    location, forwarding = source_origin
                    result = (location, forwarding + (expression,))
        origins[parameter] = result
        return result

    for parameter in instance.block_type.parsed_parameters.values():
        origin(parameter)
    return origins
