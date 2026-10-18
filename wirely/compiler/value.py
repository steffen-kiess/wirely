from __future__ import annotations

import abc
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:
    from .parameters import StaticValue

from wirely.compiler import model
from wirely.compiler.types import resolve_type


# TODO: Move value types out of types.py

# A value which can be either a concrete value or value type parameter / a value depending in some way on a value parameter
class Value(abc.ABC):
    value_type: Any  # Set by the subclasses.

    def __init__(self, *, ast_element: model.ast.SourceElement) -> None:
        self.ast_element = ast_element

    @property
    @abc.abstractmethod
    def contains_parameters(self) -> bool:
        ...

    # TODO: Pass node instead of par_values?
    @abc.abstractmethod
    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> StaticValue:
        ...

    # Concrete Wirely values, including type values, are used dynamically.
    @abc.abstractmethod
    def evaluate(self) -> Any:
        ...


# A constant value which does not depend on any parameter
class ConstantValue(Value):
    def __init__(self, value_type: model.Type, value: Any, *, ast_element: model.ast.SourceElement) -> None:
        super().__init__(ast_element=ast_element)
        self.value_type = value_type
        self.value = value

        if self.value_type.contains_parameters:
            raise Exception('Type for ConstantValue must not contain parameters')
        # TODO: Call verify_value for value?

    def __repr__(self) -> str:
        return f'ConstantValue({self.value_type}, {self.value})'

    @property
    def contains_parameters(self) -> bool:
        return False

    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> ConstantValue:
        if self.value_type == model.types.ValueType and self.value.contains_parameters:
            return ConstantValue(self.value_type, resolve_type(self.value, par_values),
                                 ast_element=self.ast_element)
        return self

    def evaluate(self) -> Any:
        return self.value


class ValueParameter(Value):
    def __init__(self, value_type: model.Type, parameter: model.NodeParameter, *,
                 ast_element: model.ast.SourceElement) -> None:
        super().__init__(ast_element=ast_element)
        self.value_type = value_type
        self.parameter = parameter

    def __repr__(self) -> str:
        return f'ValueParameter({self.value_type}, {self.parameter})'

    @property
    def contains_parameters(self) -> bool:
        return True

    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> StaticValue:
        from .parameters import ParameterValues, resolve_parameter

        if not isinstance(par_values, ParameterValues) and self.parameter in par_values:
            return par_values[self.parameter]
        if self.parameter in par_values or self.parameter.default_value is not None:
            return resolve_parameter(self.parameter, par_values)
        else:
            # TODO
            hints = []
            for par in par_values:
                if not isinstance(par, model.NodeParameter):
                    raise Exception("not isinstance(par, model.NodeParameter)")
                hints += [
                    model.CompilerHint(par.declaration, f'Available parameter: {par.node_type}.{par.name}'),
                ]
            raise model.ProgramError(self.ast_element, f'Unable to resolve value parameter {self.parameter.node_type}.{self.parameter.name}', hints=hints)

    def evaluate(self) -> NoReturn:
        raise Exception('Attempt to call ValueParameter.evaluate()')


class InputLengthValue(Value):
    """Structural metadata resolved in the instance owning the input."""

    value_type = model.types.uint32

    def __init__(self, scope: model.NodeType, port: model.TypeInputPort, *,
                 ast_element: model.ast.SourceElement) -> None:
        super().__init__(ast_element=ast_element)
        self.scope = scope
        self.port = port

    @property
    def contains_parameters(self) -> bool:
        return True

    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> ConstantValue:
        instance = getattr(par_values, 'instance', None)
        while instance is not None and instance.node_type is not self.scope:
            instance = instance.parent_instance
        if instance is None:
            raise model.ProgramError(self.ast_element,
                                     'Input length is unavailable before instance specialization')
        owner = instance.node or instance.node_type.singleton_instance
        port = owner.custom_inputs[self.port]
        count = len(model.list_members(instance.path, port))
        return ConstantValue(self.value_type, self.value_type.verify_value(count),
                             ast_element=self.ast_element)

    def evaluate(self) -> NoReturn:
        raise model.ProgramError(self.ast_element,
                                 'Input length is unavailable before instance specialization')


class DeferredValue(Value):
    """An initializer whose expected type needs instance values first."""

    ast_element: model.ast.Value

    def __init__(self, scope: model.Scope, expression: model.ast.Value, value_type: model.Type) -> None:
        super().__init__(ast_element=expression)
        self.scope = scope
        self.value_type = value_type

    @property
    def contains_parameters(self) -> bool:
        return True

    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> Value:
        value_type = resolve_type(self.value_type, par_values)
        return self.scope.get_const_value(self.ast_element, value_type, par_values)

    def evaluate(self) -> NoReturn:
        raise Exception('Attempt to evaluate an unresolved initializer')


def check_value_type(actual_type: model.Type, expected_type: model.Type,
                     ast_element: model.ast.SourceElement | None) -> None:
    if (not actual_type.contains_parameters and not expected_type.contains_parameters
            and actual_type != expected_type):
        raise model.ProgramError(ast_element, f'Expected value of type {expected_type}, got {actual_type}')


class ComputedValue(Value):
    @property
    def value(self) -> Any:
        return self.evaluate()


class ValueOptionSome(ComputedValue):
    def __init__(self, value_type: model.types.TypeOption, inner_value: Value, *,
                 ast_element: model.ast.SourceElement) -> None:
        super().__init__(ast_element=ast_element)
        self.value_type = value_type
        self.inner_value = inner_value
        check_value_type(inner_value.value_type, value_type.inner_type, inner_value.ast_element)

    @property
    def contains_parameters(self) -> bool:
        return self.value_type.contains_parameters or self.inner_value.contains_parameters

    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> ValueOptionSome:
        value_type = resolve_type(self.value_type, par_values)
        assert isinstance(value_type, model.types.TypeOption)
        inner_value = self.inner_value.resolve_self(par_values)
        assert isinstance(inner_value, Value)
        return ValueOptionSome(value_type, inner_value, ast_element=self.ast_element)

    def evaluate(self) -> Any:
        return self.value_type.verify_value(model.Some(self.inner_value.evaluate()))


class ValueList(ComputedValue):
    def __init__(self, value_type: model.types.TypeList, elements: Sequence[Value], *,
                 ast_element: model.ast.SourceElement) -> None:
        super().__init__(ast_element=ast_element)
        self.value_type = value_type
        self.elements = elements
        for element in elements:
            check_value_type(element.value_type, value_type.inner_type, element.ast_element)

    @property
    def contains_parameters(self) -> bool:
        return self.value_type.contains_parameters or any(element.contains_parameters for element in self.elements)

    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> ValueList:
        value_type = resolve_type(self.value_type, par_values)
        assert isinstance(value_type, model.types.TypeList)
        elements = []
        for element in self.elements:
            resolved = element.resolve_self(par_values)
            assert isinstance(resolved, Value)
            elements.append(resolved)
        return ValueList(value_type, elements, ast_element=self.ast_element)

    def evaluate(self) -> Any:
        return self.value_type.verify_value(tuple(element.evaluate() for element in self.elements))


class ValueFunctionCall(ComputedValue):
    ast_element: model.FunctionCall

    def __init__(self, function: model.CompiledFunction, parameters: Sequence[Value], *,
                 ast_element: model.FunctionCall) -> None:
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

    def __repr__(self) -> str:
        return f'ValueFunctionCall({self.function}, {self.parameters})'

    @property
    def contains_parameters(self) -> bool:
        return self._contains_parameters

    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> ValueFunctionCall:
        if not self.contains_parameters:
            return self
        resolved_parameters = []
        for parameter in self.parameters:
            resolved = parameter.resolve_self(par_values=par_values)
            assert isinstance(resolved, Value)
            resolved_parameters.append(resolved)
        return ValueFunctionCall(function=self.function, parameters=resolved_parameters, ast_element=self.ast_element)

    def evaluate(self) -> Any:
        if self.contains_parameters:
            raise Exception('Attempt to call evaluate() for non-concrete function call')

        return self.function.invoke(
            [parameter.evaluate() for parameter in self.parameters],
            self.ast_element.parameters, self.ast_element,
        )


class ValueStruct(ComputedValue):
    def __init__(self, value_type: model.types.TypeStruct, fields: Mapping[str, Value], *,
                 ast_element: model.ast.SourceElement) -> None:
        super().__init__(ast_element=ast_element)
        self.value_type = value_type
        self.fields = fields
        for name, value in fields.items():
            check_value_type(value.value_type, value_type.fields[name], value.ast_element)

    @property
    def contains_parameters(self) -> bool:
        return any(value.contains_parameters for value in self.fields.values())

    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> ValueStruct:
        fields = {}
        for name, value in self.fields.items():
            resolved = value.resolve_self(par_values)
            assert isinstance(resolved, Value)
            fields[name] = resolved
        return ValueStruct(self.value_type, fields, ast_element=self.ast_element)

    def evaluate(self) -> Any:
        return self.value_type.make_value({name: value.evaluate() for name, value in self.fields.items()})


class ParameterOptionValue(Value):
    """Inspect presence without reading an omitted parameter."""

    def __init__(self, parameter: model.NodeParameter, *, ast_element: model.ast.SourceElement) -> None:
        super().__init__(ast_element=ast_element)
        self.parameter = parameter
        self.value_type = model.types.TypeOption(parameter.type_resolved)

    @property
    def contains_parameters(self) -> bool:
        return True

    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> ConstantValue:
        value_type = resolve_type(self.value_type, par_values)
        if self.parameter not in par_values:
            return ConstantValue(value_type, None, ast_element=self.ast_element)
        from .parameters import resolve_parameter
        value = resolve_parameter(self.parameter, par_values)
        concrete = value if isinstance(value, model.Type) else value.evaluate()
        return ConstantValue(value_type, model.Some(concrete), ast_element=self.ast_element)

    def evaluate(self) -> NoReturn:
        raise Exception('Attempt to evaluate unresolved parameter presence')


class OptionalBinding(Value):
    """An option expression in the enclosing node's scope."""

    ast_element: model.ast.Value

    def __init__(self, scope: model.Scope, expression: model.ast.Value, parameter: model.NodeParameter) -> None:
        super().__init__(ast_element=expression)
        self.scope = scope
        self.parameter = parameter
        self.value_type = model.types.TypeOption(parameter.type_resolved)

    @property
    def contains_parameters(self) -> bool:
        return True

    def resolve_self(self, par_values: Mapping[model.NodeParameter, StaticValue]) -> Value:
        from .expressions import compile_constant
        value_type = resolve_type(self.value_type, par_values)
        value = compile_constant(self.scope, self.ast_element, value_type, par_values)
        return value.resolve_self(par_values)

    def evaluate(self) -> NoReturn:
        raise Exception('Attempt to evaluate unresolved optional binding')
