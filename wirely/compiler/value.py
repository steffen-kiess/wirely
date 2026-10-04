import abc

from wirely.compiler import model as blocks
from wirely.compiler.types import resolve_type


# TODO: Move value types out of types.py

# A value which can be either a concrete value or value type parameter / a value depending in some way on a value parameter
class Value(abc.ABC):
    def __init__(self, *, ast_element):
        self.ast_element = ast_element

    @property
    @abc.abstractmethod
    def contains_parameters(self):
        ...

    # TODO: Pass block instead of par_values?
    @abc.abstractmethod
    def resolve_self(self, par_values):
        ...

    # Only for concrete values
    @abc.abstractmethod
    def evaluate(self):
        ...


# A constant value which does not depend on any parameter
class ConstantValue(Value):
    def __init__(self, value_type, value, *, ast_element):
        super().__init__(ast_element=ast_element)
        self.value_type = value_type
        self.value = value

        if self.value_type.contains_parameters:
            raise Exception('Type for ConstantValue must not contain parameters')
        # TODO: Call verify_value for value?

    def __repr__(self):
        return f'ConstantValue({self.value_type}, {self.value})'

    @property
    def contains_parameters(self):
        return False

    def resolve_self(self, par_values):
        if self.value_type == blocks.types.ValueType and self.value.contains_parameters:
            return ConstantValue(self.value_type, resolve_type(self.value, par_values),
                                 ast_element=self.ast_element)
        return self

    def evaluate(self):
        return self.value


class ValueParameter(Value):
    def __init__(self, value_type, parameter, *, ast_element):
        super().__init__(ast_element=ast_element)
        self.value_type = value_type
        self.parameter = parameter

    def __repr__(self):
        return f'ValueParameter({self.value_type}, {self.parameter})'

    @property
    def contains_parameters(self):
        return True

    def resolve_self(self, par_values):
        from .parameters import ParameterValues, resolve_parameter

        if not isinstance(par_values, ParameterValues) and self.parameter in par_values:
            return par_values[self.parameter]
        if self.parameter in par_values or self.parameter.default_value is not None:
            return resolve_parameter(self.parameter, par_values)
        else:
            # TODO
            hints = []
            for par in par_values:
                if not isinstance(par, blocks.DeclarationParameter):
                    raise Exception("not isinstance(par, blocks.DeclarationParameter)")
                hints += [
                    blocks.CompilerHint(par, f'Available parameter: {par.interface}.{par.name}'),
                ]
            raise blocks.ProgramError(self.parameter, f'Unable to resolve value parameter {self.parameter.interface}.{self.parameter.name}', hints=hints)

    def evaluate(self):
        raise Exception('Attempt to call ValueParameter.evaluate()')


class DeferredValue(Value):
    """An initializer whose expected type needs instance values first."""

    def __init__(self, scope, expression, value_type):
        super().__init__(ast_element=expression)
        self.scope = scope
        self.value_type = value_type

    @property
    def contains_parameters(self):
        return True

    def resolve_self(self, par_values):
        value_type = resolve_type(self.value_type, par_values)
        return self.scope.get_const_value(self.ast_element, value_type, par_values)

    def evaluate(self):
        raise Exception('Attempt to evaluate an unresolved initializer')


def check_value_type(actual_type, expected_type, ast_element):
    if (not actual_type.contains_parameters and not expected_type.contains_parameters
            and actual_type != expected_type):
        raise blocks.ProgramError(ast_element, f'Expected value of type {expected_type}, got {actual_type}')


class ComputedValue(Value):
    @property
    def value(self):
        return self.evaluate()


class ValueOptionSome(ComputedValue):
    def __init__(self, value_type, inner_value, *, ast_element):
        super().__init__(ast_element=ast_element)
        self.value_type = value_type
        self.inner_value = inner_value
        check_value_type(inner_value.value_type, value_type.inner_type, inner_value.ast_element)

    @property
    def contains_parameters(self):
        return self.value_type.contains_parameters or self.inner_value.contains_parameters

    def resolve_self(self, par_values):
        return ValueOptionSome(
            resolve_type(self.value_type, par_values),
            self.inner_value.resolve_self(par_values), ast_element=self.ast_element,
        )

    def evaluate(self):
        return self.value_type.verify_value(blocks.Some(self.inner_value.evaluate()))


class ValueList(ComputedValue):
    def __init__(self, value_type, elements, *, ast_element):
        super().__init__(ast_element=ast_element)
        self.value_type = value_type
        self.elements = elements
        for element in elements:
            check_value_type(element.value_type, value_type.inner_type, element.ast_element)

    @property
    def contains_parameters(self):
        return self.value_type.contains_parameters or any(element.contains_parameters for element in self.elements)

    def resolve_self(self, par_values):
        return ValueList(
            resolve_type(self.value_type, par_values),
            [element.resolve_self(par_values) for element in self.elements],
            ast_element=self.ast_element,
        )

    def evaluate(self):
        return self.value_type.verify_value(tuple(element.evaluate() for element in self.elements))


class ValueFunctionCall(ComputedValue):
    def __init__(self, function, parameters, *, ast_element):
        super().__init__(ast_element=ast_element)
        self.function = function
        self.value_type = function.return_type
        self.parameters = parameters
        for parameter, parameter_type in zip(parameters, function.parameter_types):
            check_value_type(parameter.value_type, parameter_type, parameter.ast_element)

        self._contains_parameters = False
        for parameter in self.parameters:
            if parameter.contains_parameters:
                self._contains_parameters = True

        # TODO: Check type of parameters?
        if len(self.parameters) != len(self.function.parameter_types):
            raise Exception('Unexpected number of parameters')

    def __repr__(self):
        return f'ValueFunctionCall({self.function}, {self.parameters})'

    @property
    def contains_parameters(self):
        return self._contains_parameters

    def resolve_self(self, par_values):
        if not self.contains_parameters:
            return self
        resolved_parameters = [parameter.resolve_self(par_values=par_values) for parameter in self.parameters]
        return ValueFunctionCall(function=self.function, parameters=resolved_parameters, ast_element=self.ast_element)

    def evaluate(self):
        if self.contains_parameters:
            raise Exception('Attempt to call evaluate() for non-concrete function call')

        return self.function.invoke(
            [parameter.evaluate() for parameter in self.parameters],
            self.ast_element.parameters, self.ast_element,
        )


class ValueStruct(ComputedValue):
    def __init__(self, value_type, fields, *, ast_element):
        super().__init__(ast_element=ast_element)
        self.value_type = value_type
        self.fields = fields
        for name, value in fields.items():
            check_value_type(value.value_type, value_type.fields[name], value.ast_element)

    @property
    def contains_parameters(self):
        return any(value.contains_parameters for value in self.fields.values())

    def resolve_self(self, par_values):
        return ValueStruct(self.value_type,
                           {name: value.resolve_self(par_values) for name, value in self.fields.items()},
                           ast_element=self.ast_element)

    def evaluate(self):
        return self.value_type.make_value({name: value.evaluate() for name, value in self.fields.items()})
