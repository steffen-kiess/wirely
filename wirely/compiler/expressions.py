"""Shared expression syntax, checking, and restricted procedure bodies."""

from dataclasses import dataclass, replace

from . import model as blocks
from .ast import SourceElement, ProgramError, Value as AstValue, check_struct_literal
from .types import resolve_type, check_runtime_type
from .value import Value, check_value_type
from ..program import Expression as ProgramExpression, ProcedureBody as ProgramBody


class Expression(AstValue):
    pass


class BinaryExpression(Expression):
    def __init__(self, left, right, *, operator, source_position):
        super().__init__(source_position=source_position)
        self.left = left
        self.right = right
        self.operator = operator

    @staticmethod
    def logical_or(left, right, *, source_position):
        return BinaryExpression(left, right, operator='or', source_position=source_position)

    @staticmethod
    def logical_and(left, right, *, source_position):
        return BinaryExpression(left, right, operator='and', source_position=source_position)

    @staticmethod
    def equal(left, right, *, source_position):
        return BinaryExpression(left, right, operator='eq', source_position=source_position)

    @staticmethod
    def not_equal(left, right, *, source_position):
        return BinaryExpression(left, right, operator='ne', source_position=source_position)


class UnaryExpression(Expression):
    def __init__(self, operand, *, source_position):
        super().__init__(source_position=source_position)
        self.operand = operand


class PropertyAccess(Expression):
    def __init__(self, receiver, name, *, source_position):
        super().__init__(source_position=source_position)
        self.receiver = receiver
        self.name = name


def property_reference(scope, expression, local_names=()):
    """Split value suffixes while preserving qualified declaration references."""
    if not isinstance(expression, blocks.Reference) or expression.length < 2:
        return expression
    identifiers = expression.identifiers
    enum_reference = blocks.Reference.from_list(*identifiers[:-1],
                                                source_position=expression.source_position)
    try:
        value_type = scope.resolve_type(enum_reference)
    except ProgramError:
        value_type = None
    if isinstance(value_type, blocks.types.TypeEnum):
        return expression
    root = identifiers[0].id_str
    if (root in local_names or root in scope.static_declarations
            or root in ('true', 'false', 'None')):
        split = 1
    else:
        # An enum member is a value, but the enum's namespace path is not.
        for split in range(2, expression.length):
            enum_reference = blocks.Reference.from_list(
                *identifiers[:split - 1], source_position=expression.source_position)
            try:
                value_type = scope.resolve_type(enum_reference)
            except ProgramError:
                continue
            if isinstance(value_type, blocks.types.TypeEnum):
                break
        else:
            return expression
    receiver = blocks.Reference.from_list(*identifiers[:split],
                                         source_position=expression.source_position)
    for name in identifiers[split:]:
        receiver = PropertyAccess(receiver, name, source_position=expression.source_position)
    return receiver


class IfExpression(Expression):
    def __init__(self, condition, then, otherwise, *, source_position):
        super().__init__(source_position=source_position)
        self.condition = condition
        self.then = then
        self.otherwise = otherwise


class ExpressionBlock(Expression):
    def __init__(self, *items, source_position):
        super().__init__(source_position=source_position)
        self.bindings = items[:-1]
        self.result = items[-1]


class LocalBinding(SourceElement):
    def __init__(self, name, annotation, initializer, *, source_position):
        super().__init__(source_position=source_position)
        self.name = name
        self.annotation = annotation
        self.initializer = initializer


class ResultAssignment(SourceElement):
    def __init__(self, name, expression, *, source_position):
        super().__init__(source_position=source_position)
        self.name = name
        self.expression = expression


class ProcedureBody(SourceElement):
    def __init__(self, *statements, source_position):
        super().__init__(source_position=source_position)
        self.statements = statements


@dataclass(frozen=True, slots=True)
class TypedExpression:
    value_type: object
    operation: str
    arguments: tuple = ()
    payload: object = None
    source: object = None
    expected_type: object = None

    @property
    def contains_parameters(self):
        return (self.value_type.contains_parameters
                or (self.expected_type is not None and self.expected_type.contains_parameters)
                or (self.operation == 'constant' and self.payload.contains_parameters)
                or any(argument.contains_parameters for argument in self.arguments))

    def resolve(self, parameters):
        resolved = replace(
            self, value_type=resolve_type(self.value_type, parameters),
            expected_type=(resolve_type(self.expected_type, parameters)
                           if self.expected_type is not None else None),
            arguments=tuple(argument.resolve(parameters) for argument in self.arguments),
            payload=(self.payload.resolve_self(parameters)
                     if self.operation == 'constant' else self.payload),
        )
        resolved.check_types()
        return resolved

    def check_types(self):
        if self.expected_type is not None:
            check_value_type(self.value_type, self.expected_type, self.source)
        boolean = blocks.types.boolean
        if self.operation in ('not', 'and', 'or'):
            for argument in self.arguments:
                check_value_type(argument.value_type, boolean, argument.source)
        elif self.operation in ('eq', 'ne'):
            left, right = self.arguments
            check_value_type(right.value_type, left.value_type, right.source)
        elif self.operation == 'if':
            condition, then, otherwise = self.arguments
            check_value_type(condition.value_type, boolean, condition.source)
            check_value_type(otherwise.value_type, then.value_type, otherwise.source)
            check_value_type(then.value_type, self.value_type, then.source)
        elif self.operation in ('is_some', 'is_none'):
            receiver = self.arguments[0]
            if (not receiver.value_type.contains_parameters
                    and not isinstance(receiver.value_type, blocks.types.TypeOption)):
                raise ProgramError(self.source, 'Option presence properties require an Option value')

    def evaluate(self, inputs, locals):
        if self.operation == 'constant':
            return self.payload.evaluate()
        if self.operation == 'call':
            function, sources, call = self.payload
            return function.invoke([argument.evaluate(inputs, locals) for argument in self.arguments],
                                   sources, call)
        if self.operation == 'struct':
            return self.value_type.make_value({name: argument.evaluate(inputs, locals)
                                               for name, argument in zip(self.payload, self.arguments)})
        if self.operation == 'list':
            return tuple(argument.evaluate(inputs, locals) for argument in self.arguments)
        if self.operation == 'some':
            return blocks.Some(self.arguments[0].evaluate(inputs, locals))
        return ProgramExpression.evaluate(self, inputs, locals)


class ExpressionValue(Value):
    """A compile-time expression that can still contain parameter references."""

    def __init__(self, expression, *, ast_element):
        super().__init__(ast_element=ast_element)
        self.expression = expression
        self.value_type = expression.value_type

    @property
    def value(self):
        return self.evaluate()

    @property
    def contains_parameters(self):
        return self.expression.contains_parameters

    def resolve_self(self, par_values):
        return ExpressionValue(self.expression.resolve(par_values), ast_element=self.ast_element)

    def evaluate(self):
        return self.value_type.verify_value(self.expression.evaluate({}, {}))


def runtime_expression(expression):
    payload = expression.payload
    if expression.operation == 'constant':
        payload = blocks.value_types._to_native(expression.value_type, payload.evaluate())
    return ProgramExpression(expression.operation,
                             tuple(runtime_expression(argument) for argument in expression.arguments),
                             payload)


class ExpressionCompiler:
    def __init__(self, scope, parameters, *, runtime=False, names=None, protected=()):
        self.scope = scope
        self.parameters = parameters
        self.runtime = runtime
        self.names = dict(names or {})
        self.protected = set(protected)
        self.next_slot = 0
        self.static_usage = set()

    def record_static_usage(self, expression):
        expression = property_reference(self.scope, expression, self.names)
        if isinstance(expression, blocks.Reference):
            if expression.length == 1:
                declaration = self.scope.static_declarations.get(expression.identifiers[0].id_str)
                if declaration is not None:
                    self.static_usage.add(declaration)
            return
        # Only walk expression syntax, never resolved graph objects or positions.
        if isinstance(expression, SourceElement):
            for name, child in vars(expression).items():
                if name in ('source_position', 'outer_attributes', 'attributes'):
                    continue
                if isinstance(child, (tuple, list)):
                    for item in child:
                        self.record_static_usage(item)
                elif isinstance(child, SourceElement):
                    self.record_static_usage(child)

    def local(self, binding):
        name = binding.name.id_str
        if name in self.protected:
            raise ProgramError(binding.name, f'Cannot shadow procedure binding or parameter {name!r}')
        if self.runtime and binding.annotation is not None:
            self.record_static_usage(binding.annotation)
        expected = (self.scope.resolve_type(binding.annotation, par_values=self.parameters)
                    if binding.annotation is not None else None)
        expression = self.compile(binding.initializer, expected)
        slot = self.next_slot
        self.next_slot += 1
        self.names[name] = TypedExpression(expected or expression.value_type, 'local', payload=slot)
        return slot, expression

    def compile(self, expression, expected=None):
        expression = property_reference(self.scope, expression, self.names)
        if self.runtime:
            self.record_static_usage(expression)
        result = replace(self._compile(expression, expected), source=expression,
                         expected_type=expected)
        result.check_types()
        if self.runtime:
            check_runtime_type(result.value_type, expression)
        return result

    def _compile(self, expression, expected):
        boolean = blocks.types.boolean
        if isinstance(expression, PropertyAccess):
            name = expression.name.id_str
            if name not in ('IsSome', 'IsNone'):
                raise ProgramError(expression.name, f'Unknown option property {name!r}')
            receiver = self.compile(expression.receiver)
            return TypedExpression(boolean, 'is_some' if name == 'IsSome' else 'is_none',
                                   (receiver,))
        if (isinstance(expression, blocks.FunctionCall)
                and isinstance(property_reference(self.scope, expression.fun, self.names), PropertyAccess)):
            raise ProgramError(expression, 'Option properties cannot be called; omit parentheses')
        if isinstance(expression, blocks.ParameterOption):
            if self.runtime:
                raise ProgramError(expression, 'parameter_option is only available in compile-time expressions')
            parameter = expression.declaration(self.scope, self.names)
            value = blocks.value.ParameterOptionValue(parameter, ast_element=expression)
            return TypedExpression(value.value_type, 'constant', payload=value)
        if isinstance(expression, UnaryExpression):
            operand = self.compile(expression.operand, boolean)
            return TypedExpression(boolean, 'not', (operand,))
        if isinstance(expression, BinaryExpression):
            if expression.operator in ('and', 'or'):
                left = self.compile(expression.left, boolean)
                right = self.compile(expression.right, boolean)
            else:
                # A typed operand supplies context for an adjacent literal.
                operand_type = self.context_type(expression.left) or self.context_type(expression.right)
                left = self.compile(expression.left, operand_type)
                right = self.compile(expression.right, left.value_type)
            return TypedExpression(boolean, expression.operator, (left, right))
        if isinstance(expression, IfExpression):
            condition = self.compile(expression.condition, boolean)
            branch_type = expected or self.context_type(expression.then) or self.context_type(expression.otherwise)
            then = self.compile(expression.then, branch_type)
            otherwise = self.compile(expression.otherwise, then.value_type)
            return TypedExpression(then.value_type, 'if', (condition, then, otherwise))
        if isinstance(expression, ExpressionBlock):
            previous = dict(self.names)
            try:
                bindings = [self.local(binding) for binding in expression.bindings]
                result = self.compile(expression.result, expected)
            finally:
                self.names = previous
            return TypedExpression(result.value_type, 'block',
                                     tuple(value for slot, value in bindings) + (result,),
                                     tuple(slot for slot, value in bindings))
        if isinstance(expression, blocks.Reference) and expression.length == 1:
            name = expression.identifiers[0].id_str
            if name in self.names:
                return self.names[name]
            if self.runtime and name in self.protected:
                raise ProgramError(expression, f'Cannot read result or unavailable parameter {name!r}')
        if self.runtime:
            self.check_runtime_syntax(expression)
        if self.runtime and isinstance(expression, blocks.Reference):
            names = [identifier.id_str for identifier in expression.identifiers]
            if (names not in (['true'], ['false'], ['None'], ['MonotonicTime', 'Invalid'])
                    and not isinstance(self.known_type(expression), blocks.types.TypeEnum)):
                raise ProgramError(expression, f'Unknown procedure value {expression}; declare inputs in depends on')
        if not self.runtime and isinstance(expression, blocks.StructLiteral):
            value_type = (self.known_type(expression)
                          if expected is None or expected.contains_parameters else expected)
            fields = check_struct_literal(value_type, expression, self.scope)
            arguments = tuple(self.compile(field.value, value_type.fields[name])
                              for name, field in fields.items())
            return TypedExpression(value_type, 'struct', arguments, tuple(fields))
        if not self.runtime and isinstance(expression, blocks.ListLiteral):
            if isinstance(expected, blocks.types.TypeList):
                inner_type = expected.inner_type
            elif expected is not None and expected.contains_parameters:
                inner_type = expected
            else:
                raise ProgramError(expression, 'List literals require a List type')
            arguments = tuple(self.compile(value, inner_type) for value in expression.values)
            return TypedExpression(expected, 'list', arguments)
        if not self.runtime and isinstance(expression, blocks.FunctionCall):
            if not isinstance(expression.fun, blocks.Reference):
                raise ProgramError(expression.fun, 'Function is not a reference')
            name = '.'.join(identifier.id_str for identifier in expression.fun.identifiers)
            if name == 'Some':
                if isinstance(expected, blocks.types.TypeOption):
                    inner_type = expected.inner_type
                elif expected is not None and expected.contains_parameters:
                    inner_type = expected
                else:
                    raise ProgramError(expression, 'Some requires an Option type')
                if len(expression.parameters) != 1:
                    raise ProgramError(expression, 'Some expects exactly one argument')
                argument = self.compile(expression.parameters[0], inner_type)
                return TypedExpression(expected, 'some', (argument,))
            function = self.scope.resolve_function(expression.fun, required=False)
            if function is None:
                raise ProgramError(expression.fun, f'Cannot find function {name!r}')
            if len(expression.parameters) != len(function.parameter_types):
                raise ProgramError(expression, f'Function {name!r} expected {len(function.parameter_types)} parameters')
            arguments = tuple(self.compile(argument, value_type)
                              for argument, value_type in zip(expression.parameters, function.parameter_types))
            return TypedExpression(function.return_type, 'call', arguments,
                                     (function, tuple(expression.parameters), expression))
        value_type = expected or self.known_type(expression)
        if value_type is None:
            raise ProgramError(expression, 'Cannot infer expression type; add a type annotation')
        if self.runtime:
            check_runtime_type(value_type, expression)
        if value_type == blocks.types.ValueType:
            actual_type = self.known_type(expression)
            if actual_type is not None:
                check_value_type(actual_type, value_type, expression)
        value = self.scope.get_const_value(expression, value_type, par_values=self.parameters)
        return TypedExpression(value_type, 'constant', payload=value)

    def check_runtime_syntax(self, expression):
        if isinstance(expression, blocks.ParameterOption):
            raise ProgramError(expression, 'parameter_option is only available in compile-time expressions')
        if isinstance(expression, (blocks.FunctionCall, blocks.BracketOperator)):
            raise ProgramError(expression, 'Runtime calls and indexing are not supported in procedures')
        if isinstance(expression, blocks.StructLiteral):
            for field in expression.fields:
                self.check_runtime_syntax(field.value)
        elif isinstance(expression, blocks.ListLiteral):
            for value in expression.values:
                self.check_runtime_syntax(value)
        elif isinstance(expression, BinaryExpression):
            self.check_runtime_syntax(expression.left)
            self.check_runtime_syntax(expression.right)
        elif isinstance(expression, UnaryExpression):
            self.check_runtime_syntax(expression.operand)
        elif isinstance(expression, PropertyAccess):
            self.check_runtime_syntax(expression.receiver)
        elif isinstance(expression, IfExpression):
            self.check_runtime_syntax(expression.condition)
            self.check_runtime_syntax(expression.then)
            self.check_runtime_syntax(expression.otherwise)
        elif isinstance(expression, ExpressionBlock):
            for binding in expression.bindings:
                self.check_runtime_syntax(binding.initializer)
            self.check_runtime_syntax(expression.result)

    def context_type(self, expression):
        # Unsuffixed literals accept context instead of imposing their default.
        if isinstance(expression, (blocks.IntNumber, blocks.StringConstant)):
            return None
        if isinstance(expression, ExpressionBlock):
            return None if expression.bindings else self.context_type(expression.result)
        return self.known_type(expression)

    def known_type(self, expression):
        expression = property_reference(self.scope, expression, self.names)
        if isinstance(expression, PropertyAccess):
            return blocks.types.boolean
        if isinstance(expression, blocks.ParameterOption):
            parameter = expression.declaration(self.scope, self.names)
            return blocks.types.TypeOption(parameter.type_resolved)
        if isinstance(expression, blocks.Reference):
            if expression.length == 1:
                name = expression.identifiers[0].id_str
                if name in self.names:
                    return self.names[name].value_type
                if name in ('true', 'false'):
                    return blocks.types.boolean
                parameter = self.scope.static_declarations.get(name)
                if parameter is not None and not self.runtime:
                    return (resolve_type(parameter.type_resolved, self.parameters)
                            if self.parameters else parameter.type_resolved)
            elif expression.length >= 2:
                reference = blocks.Reference.from_list(*expression.identifiers[:-1],
                                                       source_position=expression.source_position)
                try:
                    value_type = self.scope.resolve_type(reference, par_values=self.parameters)
                except ProgramError:
                    value_type = None
                if isinstance(value_type, blocks.types.TypeEnum):
                    return value_type
        if isinstance(expression, (blocks.Reference, blocks.BracketOperator)):
            try:
                self.scope.resolve_type(expression, par_values=self.parameters)
            except ProgramError:
                pass
            else:
                return blocks.types.ValueType
        if isinstance(expression, blocks.IntNumber):
            return blocks.types.int32
        if isinstance(expression, blocks.StringConstant):
            return blocks.types.String
        if isinstance(expression, blocks.UnitLiteral):
            return blocks.types.unit
        if isinstance(expression, (UnaryExpression, BinaryExpression)):
            return blocks.types.boolean
        if isinstance(expression, ExpressionBlock) and not expression.bindings:
            return self.known_type(expression.result)
        if isinstance(expression, IfExpression):
            return self.known_type(expression.then) or self.known_type(expression.otherwise)
        if isinstance(expression, blocks.StructLiteral):
            return self.scope.resolve_type(expression.struct_type, par_values=self.parameters)
        if (isinstance(expression, blocks.FunctionCall) and not self.runtime
                and isinstance(expression.fun, blocks.Reference)):
            function = self.scope.resolve_function(expression.fun, required=False)
            if function is not None:
                return function.return_type
        return None


def compile_constant(scope, expression, expected, parameters):
    compiler = ExpressionCompiler(scope, parameters)
    typed_expression = compiler.compile(expression, expected)
    return ExpressionValue(typed_expression, ast_element=expression)


def compile_procedure(procedure, instance, source_types, result_types):
    declaration = procedure.declaration_procedure
    parameters = instance.static_values
    names = {name: TypedExpression(value_type, 'input', payload=name)
             for name, value_type in source_types.items()}
    protected = set(result_types) | set(instance.block_type.static_declarations)
    overlap = ((set(source_types) & protected)
               | (set(result_types) & set(instance.block_type.static_declarations)))
    if overlap:
        raise ProgramError(declaration, f'Procedure names must be distinct: {sorted(overlap)!r}')
    for parameter, value in {**instance.parameter_values, **instance.constant_values}.items():
        name = parameter.name.id_str
        if isinstance(value, Value) and value.value_type.is_runtime_type:
            names[name] = TypedExpression(value.value_type, 'constant', payload=value)
    compiler = ExpressionCompiler(instance.block_type, parameters, runtime=True,
                                  names=names, protected=protected | set(source_types))
    statements = []
    written = set()
    for statement in declaration.body.statements:
        if isinstance(statement, LocalBinding):
            statements.append(compiler.local(statement))
            continue
        name = statement.name.id_str
        if name not in result_types:
            raise ProgramError(statement.name, f'Cannot assign to {name!r}; expected a declared result')
        if name in written:
            raise ProgramError(statement.name, f'Result {name!r} is assigned more than once')
        statements.append((name, compiler.compile(statement.expression, result_types[name])))
        written.add(name)
    missing = set(result_types) - written
    if missing:
        raise ProgramError(declaration.body, f'Procedure did not assign results: {sorted(missing)!r}')
    procedure.static_usage = frozenset(compiler.static_usage)
    return ProgramBody(tuple((destination, runtime_expression(expression))
                             for destination, expression in statements))
