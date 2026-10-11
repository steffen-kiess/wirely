"""Shared expression syntax, checking, and restricted procedure bodies."""

from dataclasses import dataclass, replace

from . import model
from .ast import SourceElement, ProgramError, Value as AstValue, check_struct_literal
from .types import resolve_type, check_runtime_type
from .value import Value, ConstantValue, check_value_type
from ..program import (
    ARITHMETIC_OPCODES, CONVERSION_OPCODES,
    BranchTarget, Instruction, Opcode, Operand, OperandKind, ProcedureBody as ProgramBody,
)


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

    @staticmethod
    def less(left, right, *, source_position):
        return BinaryExpression(left, right, operator='lt', source_position=source_position)

    @staticmethod
    def less_equal(left, right, *, source_position):
        return BinaryExpression(left, right, operator='le', source_position=source_position)

    @staticmethod
    def greater(left, right, *, source_position):
        return BinaryExpression(left, right, operator='gt', source_position=source_position)

    @staticmethod
    def greater_equal(left, right, *, source_position):
        return BinaryExpression(left, right, operator='ge', source_position=source_position)

    @staticmethod
    def add(left, right, *, source_position):
        return BinaryExpression(left, right, operator='add', source_position=source_position)

    @staticmethod
    def subtract(left, right, *, source_position):
        return BinaryExpression(left, right, operator='sub', source_position=source_position)

    @staticmethod
    def multiply(left, right, *, source_position):
        return BinaryExpression(left, right, operator='mul', source_position=source_position)

    @staticmethod
    def divide(left, right, *, source_position):
        return BinaryExpression(left, right, operator='div', source_position=source_position)

    @staticmethod
    def remainder(left, right, *, source_position):
        return BinaryExpression(left, right, operator='rem', source_position=source_position)

    @staticmethod
    def bitwise_and(left, right, *, source_position):
        return BinaryExpression(left, right, operator='bit_and', source_position=source_position)

    @staticmethod
    def bitwise_or(left, right, *, source_position):
        return BinaryExpression(left, right, operator='bit_or', source_position=source_position)

    @staticmethod
    def bitwise_xor(left, right, *, source_position):
        return BinaryExpression(left, right, operator='bit_xor', source_position=source_position)

    @staticmethod
    def shift_left(left, right, *, source_position):
        return BinaryExpression(left, right, operator='shift_left', source_position=source_position)

    @staticmethod
    def shift_right(left, right, *, source_position):
        return BinaryExpression(left, right, operator='shift_right', source_position=source_position)


BITWISE_OPERATORS = ('bit_and', 'bit_or', 'bit_xor')
SHIFT_OPERATORS = ('shift_left', 'shift_right')
ARITHMETIC_OPERATORS = ('add', 'sub', 'mul', 'div', 'rem')


class UnaryExpression(Expression):
    def __init__(self, operand, *, operator='not', source_position):
        super().__init__(source_position=source_position)
        self.operand = operand
        self.operator = operator

    @staticmethod
    def negate(operand, *, source_position):
        # A negated literal stays a literal, so -128 can be an int8.
        if isinstance(operand, model.IntNumber):
            return operand.negated(source_position=source_position)
        return UnaryExpression(operand, operator='neg', source_position=source_position)


class PropertyAccess(Expression):
    def __init__(self, receiver, name, *, source_position):
        super().__init__(source_position=source_position)
        self.receiver = receiver
        self.name = name


def property_reference(scope, expression, local_names=()):
    """Split value suffixes while preserving qualified declaration references."""
    if not isinstance(expression, model.Reference) or expression.length < 2:
        return expression
    identifiers = expression.identifiers
    enum_reference = model.Reference.from_list(*identifiers[:-1],
                                                source_position=expression.source_position)
    try:
        value_type = scope.resolve_type(enum_reference)
    except ProgramError:
        value_type = None
    if isinstance(value_type, model.types.TypeEnum):
        return expression
    root = identifiers[0].id_str
    if (root in local_names or root in scope.static_declarations
            or root in ('true', 'false', 'None')):
        split = 1
    else:
        # An enum member is a value, but the enum's namespace path is not.
        for split in range(2, expression.length):
            enum_reference = model.Reference.from_list(
                *identifiers[:split - 1], source_position=expression.source_position)
            try:
                value_type = scope.resolve_type(enum_reference)
            except ProgramError:
                continue
            if isinstance(value_type, model.types.TypeEnum):
                break
        else:
            return expression
    receiver = model.Reference.from_list(*identifiers[:split],
                                         source_position=expression.source_position)
    for name in identifiers[split:]:
        receiver = PropertyAccess(receiver, name, source_position=expression.source_position)
    return receiver


# Member functions of integer types, mapped to their operation and overflow mode.
INTEGER_MEMBERS = {
    f'{mode.capitalize()}{name}': (operation, mode)
    for operation, name, modes in [('add', 'Add', ('wrapping', 'saturating', 'checked')),
                                   ('sub', 'Sub', ('wrapping', 'saturating', 'checked')),
                                   ('mul', 'Mul', ('wrapping', 'saturating', 'checked')),
                                   ('div', 'Div', ('checked',)),
                                   ('rem', 'Rem', ('checked',))]
    for mode in modes
}
# Conversions take one integer or enum argument; 'lossless' requires that every source value fits.
INTEGER_MEMBERS.update({
    'From': ('convert', 'lossless'), 'CheckedFrom': ('convert', 'checked'),
    'WrappingFrom': ('convert', 'wrapping'), 'SaturatingFrom': ('convert', 'saturating'),
})


def type_member(scope, reference, parameters, local_names=()):
    """Resolve Type.Member to (type, member), or None when the callee is not a type member."""
    if not isinstance(reference, model.Reference) or reference.length < 2:
        return None
    root = reference.identifiers[0].id_str
    declaration = scope.static_declarations.get(root)
    # Value names shadow type names.
    static_type = (isinstance(local_names, dict) and root in local_names
                   and local_names[root].operation == 'constant'
                   and local_names[root].value_type == model.types.ValueType)
    if ((root in local_names and not static_type)
            or (reference.length == 2 and declaration is not None and not is_type_declaration(declaration))):
        return None
    prefix = model.Reference.from_list(*reference.identifiers[:-1],
                                        source_position=reference.source_position)
    try:
        value_type = scope.resolve_type(prefix, par_values=parameters)
    except ProgramError:
        return None
    name = reference.identifiers[-1].id_str
    member = None
    if value_type.contains_parameters or isinstance(value_type, model.types.TypeInt):
        member = INTEGER_MEMBERS.get(name)
    function = scope.resolve_function(reference, required=False)
    if member is None:
        if function is None:
            raise ProgramError(reference, f'Type {value_type} has no member {name!r}')
        return None
    if function is not None:
        raise ProgramError(reference, f'{reference} is both a type member and a function')
    return value_type, member


def is_some_call(expression):
    return (isinstance(expression, model.FunctionCall) and isinstance(expression.fun, model.Reference)
            and expression.fun.length == 1 and expression.fun.identifiers[0].id_str == 'Some')


def is_type_declaration(declaration):
    declared_type = getattr(declaration, 'type_resolved', None)
    return (declared_type == model.types.ValueType
            or (isinstance(declaration.ty, model.Reference) and declaration.ty.length == 1
                and declaration.ty.identifiers[0].id_str == 'ValueType'))


class IfExpression(Expression):
    def __init__(self, condition, then, otherwise=None, *, source_position):
        super().__init__(source_position=source_position)
        self.condition = condition
        self.then = then
        self.otherwise = otherwise


class IfLetExpression(Expression):
    def __init__(self, constructor, name, scrutinee, then, otherwise=None, *, source_position):
        super().__init__(source_position=source_position)
        self.constructor = constructor
        self.name = name
        self.scrutinee = scrutinee
        self.then = then
        self.otherwise = otherwise


class ForExpression(Expression):
    def __init__(self, name, iterable, body, *, source_position):
        super().__init__(source_position=source_position)
        self.name = name
        self.iterable = iterable
        self.body = body


class ExpressionBlock(Expression):
    def __init__(self, *statements, source_position):
        super().__init__(source_position=source_position)
        self.statements = statements


class ExpressionStatement(SourceElement):
    def __init__(self, expression, *, terminated=True, source_position):
        super().__init__(source_position=source_position)
        self.expression = expression
        self.terminated = terminated

    @staticmethod
    def unterminated(expression, *, source_position):
        return ExpressionStatement(expression, terminated=False, source_position=source_position)


class LocalBinding(SourceElement):
    def __init__(self, name, annotation, initializer, *, mutable=False, source_position):
        super().__init__(source_position=source_position)
        self.name = name
        self.annotation = annotation
        self.initializer = initializer
        self.is_mutable = mutable

    @staticmethod
    def mutable(name, annotation, initializer, *, source_position):
        return LocalBinding(name, annotation, initializer, mutable=True, source_position=source_position)


class AssignmentExpression(Expression):
    def __init__(self, target, expression, *, source_position):
        super().__init__(source_position=source_position)
        self.target = target
        self.expression = expression


@dataclass(frozen=True)
class Binding:
    value: 'TypedExpression'
    mutable: bool = False


class ExpressionClassification:
    """Resolve runtime dependencies, including cycles through block locals."""

    def __init__(self, expression, names):
        self.dependencies = {}
        self.runtime = set()
        self.visit(expression, names)
        dependents = {}
        for source, dependencies in self.dependencies.items():
            for dependency in dependencies:
                dependents.setdefault(dependency, set()).add(source)
        pending = list(self.runtime)
        while pending:
            for dependent in dependents.get(pending.pop(), ()):
                if dependent not in self.runtime:
                    self.runtime.add(dependent)
                    pending.append(dependent)

    def visit(self, expression, names):
        """Names map to runtime flags or the syntax node owning a local."""
        if not isinstance(expression, SourceElement):
            return
        dependencies = self.dependencies.setdefault(expression, set())
        if isinstance(expression, model.Reference):
            binding = names.get(expression.identifiers[0].id_str, False)
            if binding is True:
                self.runtime.add(expression)
            elif binding is not False:
                dependencies.add(binding)
            return
        if isinstance(expression, ExpressionBlock):
            names = dict(names)
            for statement in expression.statements:
                if isinstance(statement, LocalBinding):
                    if statement.initializer is not None:
                        self.visit(statement.initializer, names)
                        self.dependencies[statement] = {expression, statement.initializer}
                    else:
                        self.dependencies[statement] = {expression}
                    names[statement.name.id_str] = expression
                else:
                    self.visit(statement, names)
                dependencies.add(statement)
            return
        if isinstance(expression, IfLetExpression):
            self.visit(expression.scrutinee, names)
            inner_names = {**names, expression.name.id_str: expression}
            self.visit(expression.then, inner_names)
            self.visit(expression.otherwise, names)
            dependencies.update(child for child in (
                expression.scrutinee, expression.then, expression.otherwise,
            ) if child is not None)
            return
        if isinstance(expression, ForExpression):
            self.runtime.add(expression)
            self.visit(expression.iterable, names)
            self.visit(expression.body, {**names, expression.name.id_str: True})
            dependencies.update((expression.iterable, expression.body))
            return
        # Walk syntax only; declaration names and annotations are not values.
        for name, child in vars(expression).items():
            if name in ('source_position', 'outer_attributes', 'attributes'):
                continue
            children = child if isinstance(child, (tuple, list)) else (child,)
            for item in children:
                if isinstance(item, SourceElement):
                    self.visit(item, names)
                    dependencies.add(item)


@dataclass(frozen=True, slots=True)
class TypedExpression:
    value_type: object
    operation: str
    arguments: tuple = ()
    payload: object = None
    source: object = None
    expected_type: object = None
    runtime: bool = False

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
        boolean = model.types.boolean
        if self.operation in ('and', 'or'):
            for argument in self.arguments:
                check_value_type(argument.value_type, boolean, argument.source)
        elif self.operation in (*BITWISE_OPERATORS, 'not'):
            operand_type = self.arguments[0].value_type
            for argument in self.arguments[1:]:
                check_value_type(argument.value_type, operand_type, argument.source)
            if (not operand_type.contains_parameters
                    and not isinstance(operand_type, (model.types.TypeInt, model.types.TypeBoolean))):
                raise ProgramError(self.source, f'Bitwise operation requires an integer or boolean, got {operand_type}')
        elif self.operation in SHIFT_OPERATORS:
            value, count = self.arguments
            for argument in (value, count):
                if (not argument.value_type.contains_parameters
                        and not isinstance(argument.value_type, model.types.TypeInt)):
                    raise ProgramError(argument.source, f'Shift requires an integer, got {argument.value_type}')
        elif self.operation in ('eq', 'ne', 'lt', 'le', 'gt', 'ge'):
            left, right = self.arguments
            check_value_type(right.value_type, left.value_type, right.source)
            if (self.operation not in ('eq', 'ne') and not left.value_type.contains_parameters
                    and not left.value_type.is_ordered):
                raise ProgramError(self.source, 'Ordering comparison requires an integer, Duration, '
                                   f'or TimeOfDay value, got {left.value_type}')
        elif self.operation == 'if':
            condition, then, otherwise = self.arguments
            check_value_type(condition.value_type, boolean, condition.source)
            check_value_type(otherwise.value_type, then.value_type, otherwise.source)
            check_value_type(then.value_type, self.value_type, then.source)
        elif self.operation == 'unwrap':
            option = self.arguments[0]
            if not option.value_type.contains_parameters and not isinstance(option.value_type, model.types.TypeOption):
                raise ProgramError(option.source, f'Expected an Option value, got {option.value_type}')
        elif self.operation in ('unwrap_or', 'if_let'):
            option = self.arguments[0]
            if option.value_type.contains_parameters and not isinstance(option.value_type, model.types.TypeOption):
                return
            if not isinstance(option.value_type, model.types.TypeOption):
                raise ProgramError(option.source, f'Expected an Option value, got {option.value_type}')
            if self.operation == 'unwrap_or':
                check_value_type(self.arguments[1].value_type, option.value_type.inner_type, self.arguments[1].source)
            else:
                then, otherwise = self.arguments[1:]
                check_value_type(otherwise.value_type, then.value_type, otherwise.source)
            check_value_type(self.arguments[1].value_type, self.value_type, self.arguments[1].source)
        elif self.operation in ('arith', 'neg'):
            for argument in self.arguments[1:]:
                check_value_type(argument.value_type, self.arguments[0].value_type, argument.source)
            integer_type = self.arguments[0].value_type
            # The + and - operators also combine durations.
            duration_operator = (self.operation == 'arith' and self.payload in (('add', None), ('sub', None))
                                 and isinstance(integer_type, model.types.TypeDuration))
            if (not integer_type.contains_parameters and not duration_operator
                    and not isinstance(integer_type, model.types.TypeInt)):
                raise ProgramError(self.source, f'Integer arithmetic requires an integer value, got {integer_type}')
        elif self.operation == 'convert':
            source_type = self.arguments[0].value_type
            target_type = (self.value_type.inner_type if self.payload == 'checked'
                           else self.value_type)
            if source_type.contains_parameters or target_type.contains_parameters:
                return
            if not isinstance(target_type, model.types.TypeInt):
                raise ProgramError(self.source, f'Conversion requires an integer target, got {target_type}')
            if not isinstance(source_type, (model.types.TypeInt, model.types.TypeEnum)):
                raise ProgramError(self.arguments[0].source,
                                   f'Conversion requires an integer or enum value, got {source_type}')
            source_min, source_max = model.types.integer_range(source_type)
            if self.payload == 'lossless' and not (target_type.min_value <= source_min
                                                   and source_max <= target_type.max_value):
                raise ProgramError(self.source, f'{target_type}.From cannot convert every {source_type} value; '
                                   'use CheckedFrom, WrappingFrom, or SaturatingFrom')
        elif self.operation in ('is_some', 'is_none'):
            receiver = self.arguments[0]
            if (not receiver.value_type.contains_parameters
                    and not isinstance(receiver.value_type, model.types.TypeOption)):
                name = 'IsSome' if self.operation == 'is_some' else 'IsNone'
                raise ProgramError(self.source, f'Value of type {receiver.value_type} has no property {name!r}')

    def check_shift_count(self, count):
        width = self.arguments[0].value_type.bits
        if not 0 <= count < width:
            raise ProgramError(self.arguments[1].source, f'Shift count must be between 0 and {width - 1}')

    def evaluate(self, inputs, locals):
        if self.operation == 'unit':
            return model.types.unit.default_value()
        if self.operation == 'assign':
            value = self.arguments[0].evaluate(inputs, locals)
            locals[self.payload] = value
            return model.types.unit.default_value()
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
            return model.Some(self.arguments[0].evaluate(inputs, locals))
        if self.operation == 'field':
            return self.arguments[0].evaluate(inputs, locals)[self.payload]
        if self.operation == 'convert':
            # A lossless conversion always fits, so checking cannot fail.
            target_type = self.value_type.inner_type if self.payload == 'checked' else self.value_type
            result = model.types.convert(self.arguments[0].value_type, target_type,
                                          'checked' if self.payload == 'lossless' else self.payload,
                                          self.arguments[0].evaluate(inputs, locals))
            return result.value if self.payload == 'lossless' else result
        operation = self.operation
        if operation == 'input':
            return inputs[self.payload]
        if operation == 'local':
            return locals['local', self.payload]
        if operation == 'block':
            for expression in self.arguments[:-1]:
                expression.evaluate(inputs, locals)
            return self.arguments[-1].evaluate(inputs, locals)
        if operation in ('arith', 'neg'):
            return self.evaluate_arithmetic(inputs, locals)
        left = self.arguments[0].evaluate(inputs, locals)
        if operation == 'unwrap':
            if left is None:
                raise ProgramError(self.source, 'Called Unwrap on a None value')
            return left.value
        if operation == 'not':
            return self.value_type.bitwise('not', left)
        if operation == 'is_some':
            return left is not None
        if operation == 'is_none':
            return left is None
        if operation == 'and':
            return left and self.arguments[1].evaluate(inputs, locals)
        if operation == 'or':
            return left or self.arguments[1].evaluate(inputs, locals)
        if operation == 'if':
            branch = self.arguments[1] if left else self.arguments[2]
            return branch.evaluate(inputs, locals)
        if operation == 'if_let':
            if left is None:
                return self.arguments[2].evaluate(inputs, locals)
            if self.payload is not None:
                locals['local', self.payload] = left.value
            return self.arguments[1].evaluate(inputs, locals)
        right = self.arguments[1].evaluate(inputs, locals)
        if operation in BITWISE_OPERATORS:
            return self.value_type.bitwise(operation.removeprefix('bit_'), left, right)
        if operation in SHIFT_OPERATORS:
            self.check_shift_count(right)
            return self.value_type.shift(operation.removeprefix('shift_'), left, right)
        if operation == 'unwrap_or':
            # The fallback is evaluated eagerly, as in Rust.
            return right if left is None else left.value
        if operation == 'eq':
            return left == right
        if operation == 'ne':
            return left != right
        less = self.arguments[0].value_type.less
        if operation == 'lt':
            return less(left, right)
        if operation == 'le':
            return not less(right, left)
        if operation == 'gt':
            return less(right, left)
        if operation == 'ge':
            return not less(left, right)
        raise AssertionError(f'Unknown compiler expression operation {operation!r}')

    def evaluate_arithmetic(self, inputs, locals):
        integer_type = self.arguments[0].value_type
        operands = [argument.evaluate(inputs, locals) for argument in self.arguments]
        if self.operation == 'neg':
            operation, overflow, operands = 'sub', None, [0, *operands]
        else:
            operation, overflow = self.payload
        if overflow is not None:
            return integer_type.arithmetic(operation, overflow, *operands)
        if isinstance(integer_type, model.types.TypeDuration):
            left, right = (operand.duration_ns for operand in operands)
            result = left + right if operation == 'add' else left - right
            if result < 0:
                raise ProgramError(self.source, 'Negative duration')
            if result > model.Duration.MAX_NS:
                raise ProgramError(self.source, 'Duration overflow: result exceeds the maximum duration')
            return model.Duration(duration_ns=result)
        # Operators fail compilation instead of overflowing.
        if operation in ('div', 'rem') and operands[1] == 0:
            raise ProgramError(self.source, 'Division by zero')
        result = integer_type.arithmetic(operation, 'checked', *operands)
        if result is None:
            raise ProgramError(self.source, f'Arithmetic overflow: result does not fit {integer_type}')
        return result.value


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


OPCODES_BY_MEMBER = {member: opcode for opcode, member in ARITHMETIC_OPCODES.items()}
OPCODES_BY_CONVERSION = {overflow: opcode for opcode, overflow in CONVERSION_OPCODES.items()}


class InstructionCompiler:
    """Lower resolved procedure expressions to forward-only instructions."""

    def __init__(self, source_types, output_types, intern_constant, intern_function):
        self.inputs = {name: Operand(OperandKind.INPUT, index)
                       for index, name in enumerate(source_types)}
        self.outputs = {name: Operand(OperandKind.OUTPUT, index)
                        for index, name in enumerate(output_types)}
        self.intern_constant = intern_constant
        self.intern_function = intern_function
        self.instructions = []
        self.local_types = []
        self.locals = {}

    def allocate(self, value_type):
        operand = Operand(OperandKind.LOCAL, len(self.local_types))
        self.local_types.append(value_type)
        return operand

    def local(self, slot, value_type):
        if slot not in self.locals:
            self.locals[slot] = self.allocate(value_type)
        return self.locals[slot]

    def emit(self, opcode, *operands):
        index = len(self.instructions)
        self.instructions.append(Instruction(opcode, operands))
        return index

    def patch(self, index):
        instruction = self.instructions[index]
        self.instructions[index] = Instruction(
            instruction.opcode, (*instruction.operands[:-1], BranchTarget(len(self.instructions))),
        )

    def compile(self, expression, destination=None):
        operation = expression.operation
        if operation in ('constant', 'input', 'local', 'output'):
            if operation == 'constant':
                source = Operand(OperandKind.CONST, self.intern_constant(
                    expression.value_type, expression.payload.evaluate(),
                ))
            elif operation == 'input':
                source = self.inputs[expression.payload]
            elif operation == 'output':
                source = self.outputs[expression.payload]
            else:
                source = self.local(expression.payload, expression.value_type)
            if destination is None:
                return source
            self.emit(Opcode.COPY, destination, source)
            return destination
        if operation == 'for':
            slot, members = expression.payload
            for member in members:
                self.locals[slot] = self.inputs[member]
                self.compile(expression.arguments[0])
            self.locals.pop(slot, None)
            return self.compile(TypedExpression(model.types.unit, 'unit'), destination)
        if operation == 'block':
            for value in expression.arguments[:-1]:
                if value.runtime:
                    self.compile(value)
            return self.compile(expression.arguments[-1], destination)
        if operation == 'assign':
            value = expression.arguments[0]
            kind, slot = expression.payload
            target = self.outputs[slot] if kind == 'output' else self.local(slot, value.value_type)
            # Evaluate fully before writing: the RHS can read or update the target.
            self.emit(Opcode.COPY, target, self.compile(value))
            return self.compile(TypedExpression(model.types.unit, 'unit'), destination)
        if operation == 'unit':
            destination = destination or self.allocate(model.types.unit)
            self.emit(Opcode.SET_DEFAULT, destination)
            return destination
        if destination is None:
            destination = self.allocate(expression.value_type)
        if operation == 'call':
            arguments = [self.snapshot(argument) for argument in expression.arguments]
            self.emit(Opcode.CALL, destination, self.intern_function(expression.payload[0]),
                      len(arguments), *arguments)
            return destination
        if operation == 'struct':
            # Field values are computed before being stored; bodies never read the destination.
            values = [self.snapshot(argument) for argument in expression.arguments]
            fields = tuple(expression.value_type.fields)
            for name, value in zip(expression.payload, values):
                self.emit(Opcode.SET_FIELD, destination, value, fields.index(name))
            return destination
        left = self.snapshot(expression.arguments[0])
        if operation == 'if':
            skip_then = self.emit(Opcode.JUMP_IF_FALSE, left, BranchTarget(0))
            self.compile(expression.arguments[1], destination)
            skip_else = self.emit(Opcode.JUMP, BranchTarget(0))
            self.patch(skip_then)
            self.compile(expression.arguments[2], destination)
            self.patch(skip_else)
        elif operation in ('unwrap_or', 'if_let'):
            fallback = self.snapshot(expression.arguments[1]) if operation == 'unwrap_or' else None
            present = self.allocate(model.types.boolean)
            self.emit(Opcode.IS_SOME, present, left)
            skip_some = self.emit(Opcode.JUMP_IF_FALSE, present, BranchTarget(0))
            if operation == 'unwrap_or':
                self.emit(Opcode.GET_OPTION_VALUE, destination, left)
            else:
                inner_type = expression.arguments[0].value_type.inner_type
                if expression.payload is not None:
                    self.emit(Opcode.GET_OPTION_VALUE, self.local(expression.payload, inner_type), left)
                self.compile(expression.arguments[1], destination)
            skip_none = self.emit(Opcode.JUMP, BranchTarget(0))
            self.patch(skip_some)
            if fallback is not None:
                self.emit(Opcode.COPY, destination, fallback)
            else:
                self.compile(expression.arguments[-1], destination)
            self.patch(skip_none)
        elif operation in ('and', 'or'):
            self.emit(Opcode.COPY, destination, left)
            skip_right = self.emit(Opcode.JUMP_IF_FALSE if operation == 'and' else Opcode.JUMP_IF_TRUE,
                                   left, BranchTarget(0))
            self.compile(expression.arguments[1], destination)
            self.patch(skip_right)
        elif operation == 'field':
            index = tuple(expression.arguments[0].value_type.fields).index(expression.payload)
            self.emit(Opcode.GET_FIELD, destination, left, index)
        elif operation == 'some':
            self.emit(Opcode.MAKE_SOME, destination, left)
        elif operation == 'convert':
            # A lossless conversion never changes the value, so wrapping yields the same result.
            overflow = 'wrapping' if expression.payload == 'lossless' else expression.payload
            self.emit(OPCODES_BY_CONVERSION[overflow], destination, left)
        elif operation in ('not', 'is_some', 'is_none'):
            self.emit(Opcode.NOT if operation == 'not' else Opcode.IS_SOME, destination, left)
            if operation == 'is_none':
                self.emit(Opcode.NOT, destination, destination)
        elif operation in ('eq', 'ne'):
            right = self.compile(expression.arguments[1])
            self.emit(Opcode.EQUAL, destination, left, right)
            if operation == 'ne':
                self.emit(Opcode.NOT, destination, destination)
        elif operation in BITWISE_OPERATORS:
            right = self.compile(expression.arguments[1])
            opcode = {'bit_and': Opcode.AND, 'bit_or': Opcode.OR, 'bit_xor': Opcode.XOR}[operation]
            self.emit(opcode, destination, left, right)
        elif operation in SHIFT_OPERATORS:
            count = expression.arguments[1].evaluate({}, {})
            expression.check_shift_count(count)
            right = Operand(OperandKind.CONST, self.intern_constant(model.types.uint32, count))
            self.emit(Opcode.SHIFT_LEFT if operation == 'shift_left' else Opcode.SHIFT_RIGHT,
                      destination, left, right)
        elif operation == 'arith':
            right = self.compile(expression.arguments[1])
            self.emit(OPCODES_BY_MEMBER[expression.payload], destination, left, right)
        elif operation in ('lt', 'le', 'gt', 'ge'):
            right = self.compile(expression.arguments[1])
            # a <= b is !(b < a), and a >= b is !(a < b).
            if operation in ('lt', 'ge'):
                self.emit(Opcode.LESS, destination, left, right)
            else:
                self.emit(Opcode.LESS, destination, right, left)
            if operation in ('le', 'ge'):
                self.emit(Opcode.NOT, destination, destination)
        else:
            raise AssertionError(f'Unknown procedure operation {operation!r}')
        return destination

    def snapshot(self, expression):
        source = self.compile(expression)
        if source.kind in (OperandKind.LOCAL, OperandKind.OUTPUT):
            destination = self.allocate(expression.value_type)
            self.emit(Opcode.COPY, destination, source)
            return destination
        return source

    def body(self, expression):
        if expression.runtime:
            self.compile(expression)
        self.emit(Opcode.RETURN)
        return ProgramBody(tuple(self.instructions), tuple(self.local_types))


class ExpressionCompiler:
    def __init__(self, scope, parameters, *, runtime=False, names=None, protected=()):
        self.scope = scope
        self.parameters = parameters
        self.runtime = runtime
        self.names = dict(names or {})
        self.protected = set(protected)
        self.next_slot = 0
        self.static_usage = set()
        self.bindings = {}
        self.initialization = {}
        self.loop_outer_keys = None
        self.classification = None

    def is_runtime(self, expression):
        if self.classification is not None and expression in self.classification.dependencies:
            return expression in self.classification.runtime
        # Property references are split into fresh syntax nodes during compilation.
        if isinstance(expression, PropertyAccess):
            return self.is_runtime(expression.receiver)
        if isinstance(expression, model.Reference):
            value = self.names.get(expression.identifiers[0].id_str)
            return value is not None and value.runtime
        return False

    def parameter_option_declaration(self, expression):
        local_names = {name for name, value in self.names.items() if value.operation != 'constant'}
        return expression.declaration(self.scope, local_names)

    def record_static_usage(self, expression):
        expression = property_reference(self.scope, expression, self.names)
        if isinstance(expression, model.Reference):
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

    def bind(self, name, value, *, mutable=False, initialized=False):
        self.names[name] = value
        key = value.operation, value.payload
        self.bindings[key] = Binding(value, mutable)
        self.initialization[key] = initialized, initialized

    def local(self, binding):
        name = binding.name.id_str
        if name in self.protected:
            raise ProgramError(binding.name, f'Cannot shadow procedure port or parameter {name!r}')
        if self.runtime and binding.annotation is not None:
            self.record_static_usage(binding.annotation)
        expected = (self.scope.resolve_type(binding.annotation, par_values=self.parameters)
                    if binding.annotation is not None else None)
        if binding.initializer is None and expected is None:
            raise ProgramError(binding, 'Uninitialized locals require a type annotation')
        expression = (self.compile(binding.initializer, expected)
                      if binding.initializer is not None else None)
        value_type = expected or expression.value_type
        if self.is_runtime(binding):
            check_runtime_type(value_type, binding)
        slot = self.next_slot
        self.next_slot += 1
        self.bind(name, TypedExpression(value_type, 'local', payload=slot, runtime=self.is_runtime(binding)),
                  mutable=binding.is_mutable, initialized=expression is not None)
        return (TypedExpression(model.types.unit, 'assign', (expression,), ('local', slot),
                                runtime=self.is_runtime(binding))
                if expression is not None else TypedExpression(model.types.unit, 'unit'))

    def merge_initialization(self, before, *branches):
        """Merge (possibly initialized, definitely initialized) for outer bindings."""
        self.initialization = {
            key: (any(branch[key][0] for branch in branches),
                  all(branch[key][1] for branch in branches))
            for key in before
        }

    def assignment(self, expression):
        target = expression.target
        if not isinstance(target, model.Reference) or target.length != 1:
            raise ProgramError(target, 'Assignment requires a whole local variable or output')
        name = target.identifiers[0].id_str
        value = self.names.get(name)
        key = (value.operation, value.payload) if value is not None else None
        binding = self.bindings.get(key)
        if binding is None:
            raise ProgramError(target, f'Cannot assign to {name!r}; expected a local variable or output')
        if not binding.mutable and self.loop_outer_keys is not None and key in self.loop_outer_keys:
            raise ProgramError(target, f'Cannot assign outer immutable binding {name!r} inside a loop')
        rhs = self.compile(expression.expression, binding.value.value_type)
        if not binding.mutable and self.initialization[key][0]:
            raise ProgramError(target, f'Immutable binding {name!r} may be assigned more than once')
        self.initialization[key] = True, True
        return TypedExpression(model.types.unit, 'assign', (rhs,), key)

    def for_expression(self, expression):
        if not self.runtime:
            raise ProgramError(expression, 'For loops are only allowed in procedure bodies')
        if self.loop_outer_keys is not None:
            raise ProgramError(expression, 'Nested for loops are not supported')
        iterable = expression.iterable
        value = (self.names.get(iterable.identifiers[0].id_str)
                 if isinstance(iterable, model.Reference) and iterable.length == 1 else None)
        if value is None or value.operation != 'multi_input':
            raise ProgramError(iterable, 'For loops require a multi value input name')
        name = expression.name.id_str
        if name in self.protected:
            raise ProgramError(expression.name, f'Cannot shadow procedure port or parameter {name!r}')
        previous_names = dict(self.names)
        before = dict(self.initialization)
        slot = self.next_slot
        self.next_slot += 1
        self.loop_outer_keys = set(before)
        self.bind(name, TypedExpression(value.value_type, 'local', payload=slot, runtime=True), initialized=True)
        try:
            body = self.compile(expression.body, model.types.unit)
            # A loop may execute zero times, regardless of its specialized arity.
            self.merge_initialization(before, before, self.initialization)
        finally:
            self.names = previous_names
            self.loop_outer_keys = None
            self.initialization = {key: state for key, state in self.initialization.items() if key in before}
        return TypedExpression(model.types.unit, 'for', (body,), (slot, value.payload))

    def block(self, expression, expected):
        previous_names = dict(self.names)
        outer_keys = set(self.initialization)
        statements = []
        result = TypedExpression(model.types.unit, 'unit')
        try:
            for index, statement in enumerate(expression.statements):
                if isinstance(statement, LocalBinding):
                    statements.append(self.local(statement))
                    continue
                is_tail = not statement.terminated and index == len(expression.statements) - 1
                value = self.compile(statement.expression, expected if is_tail else None)
                if is_tail:
                    result = value
                else:
                    if not statement.terminated:
                        check_value_type(value.value_type, model.types.unit, statement)
                    statements.append(value)
        finally:
            self.names = previous_names
            self.initialization = {key: state for key, state in self.initialization.items()
                                   if key in outer_keys}
        return TypedExpression(result.value_type, 'block', (*statements, result))

    def compile(self, expression, expected=None):
        if self.runtime and self.classification is None:
            self.classification = ExpressionClassification(
                expression, {name: value.runtime for name, value in self.names.items()},
            )
        runtime = self.is_runtime(expression)
        expression = property_reference(self.scope, expression, self.names)
        if self.runtime:
            self.record_static_usage(expression)
        result = replace(self._compile(expression, expected), source=expression,
                         expected_type=expected, runtime=runtime)
        result.check_types()
        if runtime:
            check_runtime_type(result.value_type, expression)
        return result

    def _compile(self, expression, expected):
        boolean = model.types.boolean
        if isinstance(expression, ForExpression):
            return self.for_expression(expression)
        if isinstance(expression, AssignmentExpression):
            return self.assignment(expression)
        if isinstance(expression, PropertyAccess):
            name = expression.name.id_str
            receiver = self.compile(expression.receiver)
            receiver_type = receiver.value_type
            if isinstance(receiver_type, model.types.TypeStruct) and name in receiver_type.fields:
                return TypedExpression(receiver_type.fields[name], 'field', (receiver,), name)
            if (name in ('IsSome', 'IsNone') and (isinstance(receiver_type, model.types.TypeOption)
                                                  or receiver_type.contains_parameters)):
                return TypedExpression(boolean, 'is_some' if name == 'IsSome' else 'is_none',
                                       (receiver,))
            if receiver_type.contains_parameters:
                raise ProgramError(expression.name, 'Field access requires a known struct type')
            raise ProgramError(expression.name, f'Value of type {receiver_type} has no property {name!r}')
        if isinstance(expression, model.FunctionCall):
            member = type_member(self.scope, expression.fun, self.parameters, self.names)
            if member is not None:
                return self.member_call(expression, *member)
        if isinstance(expression, model.FunctionCall):
            method = property_reference(self.scope, expression.fun, self.names)
            if isinstance(method, PropertyAccess):
                if method.name.id_str == 'UnwrapOr':
                    return self.unwrap_or(expression, method.receiver, expected)
                if method.name.id_str == 'Unwrap':
                    return self.unwrap(expression, method.receiver, expected)
                if method.name.id_str in ('IsSome', 'IsNone'):
                    raise ProgramError(expression, 'Option properties cannot be called; omit parentheses')
                receiver = self.compile(method.receiver)
                raise ProgramError(method.name,
                                   f'Value of type {receiver.value_type} has no method {method.name.id_str!r}')
        if isinstance(expression, model.ParameterOption):
            if self.is_runtime(expression):
                raise ProgramError(expression, 'parameter_option is only available in compile-time expressions')
            parameter = self.parameter_option_declaration(expression)
            value = model.value.ParameterOptionValue(parameter, ast_element=expression)
            if self.runtime:
                value = value.resolve_self(self.parameters)
            return TypedExpression(value.value_type, 'constant', payload=value)
        if isinstance(expression, UnaryExpression) and expression.operator == 'neg':
            if self.is_runtime(expression):
                raise ProgramError(expression, 'Negation is compile-time only; use T.WrappingSub(0, value), '
                                   'T.SaturatingSub(0, value), or T.CheckedSub(0, value)')
            operand = self.compile(expression.operand, expected)
            return TypedExpression(operand.value_type, 'neg', (operand,))
        if isinstance(expression, UnaryExpression):
            operand = self.compile(expression.operand, expected or self.context_type(expression.operand))
            return TypedExpression(operand.value_type, 'not', (operand,))
        if isinstance(expression, BinaryExpression) and expression.operator in SHIFT_OPERATORS:
            left = self.compile(expression.left, expected or self.context_type(expression.left))
            if self.is_runtime(expression.right):
                raise ProgramError(expression.right, 'Shift count must be a compile-time value')
            right = self.compile(expression.right)
            return TypedExpression(left.value_type, expression.operator, (left, right))
        if isinstance(expression, BinaryExpression) and expression.operator in BITWISE_OPERATORS:
            operand_type = expected or self.context_type(expression.left) or self.context_type(expression.right)
            left = self.compile(expression.left, operand_type)
            right = self.compile(expression.right, left.value_type)
            return TypedExpression(left.value_type, expression.operator, (left, right))
        if isinstance(expression, BinaryExpression) and expression.operator in ARITHMETIC_OPERATORS:
            if self.is_runtime(expression):
                name = expression.operator.capitalize()
                operand_type = self.context_type(expression.left) or self.context_type(expression.right)
                if isinstance(operand_type, model.types.TypeDuration) and name in ('Add', 'Sub'):
                    alternatives = f'Duration.Saturating{name} or Duration.Checked{name}'
                else:
                    alternatives = (f'T.CheckedDiv' if name == 'Div' else 'T.CheckedRem' if name == 'Rem'
                                    else f'T.Wrapping{name}, T.Saturating{name}, or T.Checked{name}')
                raise ProgramError(expression, f'Arithmetic operators are compile-time only; use {alternatives}')
            operand_type = expected or self.context_type(expression.left) or self.context_type(expression.right)
            left = self.compile(expression.left, operand_type)
            right = self.compile(expression.right, left.value_type)
            return TypedExpression(left.value_type, 'arith', (left, right), (expression.operator, None))
        if isinstance(expression, BinaryExpression):
            if expression.operator in ('and', 'or'):
                left = self.compile(expression.left, boolean)
                before = dict(self.initialization)
                right = self.compile(expression.right, boolean)
                self.merge_initialization(before, before, self.initialization)
            else:
                # A typed operand supplies context for an adjacent literal.
                operand_type = self.context_type(expression.left) or self.context_type(expression.right)
                left = self.compile(expression.left, operand_type)
                right = self.compile(expression.right, left.value_type)
            return TypedExpression(boolean, expression.operator, (left, right))
        if isinstance(expression, IfExpression):
            condition = self.compile(expression.condition, boolean)
            before = dict(self.initialization)
            branch_type = (model.types.unit if expression.otherwise is None else
                           expected or self.context_type(expression.then) or self.context_type(expression.otherwise))
            then = self.compile(expression.then, branch_type)
            then_state = dict(self.initialization)
            self.initialization = dict(before)
            otherwise = (self.compile(expression.otherwise, then.value_type)
                         if expression.otherwise is not None else TypedExpression(model.types.unit, 'unit'))
            self.merge_initialization(before, then_state, self.initialization)
            return TypedExpression(then.value_type, 'if', (condition, then, otherwise))
        if isinstance(expression, IfLetExpression):
            return self.if_let(expression, expected)
        if isinstance(expression, ExpressionBlock):
            return self.block(expression, expected)
        if isinstance(expression, model.Reference) and expression.length == 1:
            name = expression.identifiers[0].id_str
            if name in self.names:
                value = self.names[name]
                if value.operation == 'multi_input':
                    raise ProgramError(expression, 'Multi inputs can only be used as for-loop sources')
                key = value.operation, value.payload
                if key in self.initialization and not self.initialization[key][1]:
                    raise ProgramError(expression, f'Binding {name!r} is not definitely initialized')
                return value
            if self.runtime and name in self.protected:
                raise ProgramError(expression, f'Cannot read output or unavailable parameter {name!r}')
        if self.is_runtime(expression):
            self.check_runtime_syntax(expression)
        if self.runtime and isinstance(expression, model.Reference):
            names = [identifier.id_str for identifier in expression.identifiers]
            if (names not in (['true'], ['false'], ['None'])
                    and self.known_type(expression) != model.types.ValueType
                    and not isinstance(self.known_type(expression), model.types.TypeEnum)):
                raise ProgramError(expression, f'Unknown procedure value {expression}; declare inputs in depends on')
        if isinstance(expression, model.StructLiteral):
            value_type = (self.known_type(expression)
                          if expected is None or expected.contains_parameters else expected)
            fields = check_struct_literal(value_type, expression, self.scope)
            if self.is_runtime(expression):
                check_runtime_type(value_type, expression)
            arguments = tuple(self.compile(field.value, value_type.fields[name])
                              for name, field in fields.items())
            return TypedExpression(value_type, 'struct', arguments, tuple(fields))
        if isinstance(expression, model.ListLiteral):
            if isinstance(expected, model.types.TypeList):
                inner_type = expected.inner_type
            elif expected is not None and expected.contains_parameters:
                inner_type = expected
            else:
                raise ProgramError(expression, 'List literals require a List type')
            arguments = tuple(self.compile(value, inner_type) for value in expression.values)
            return TypedExpression(expected, 'list', arguments)
        if is_some_call(expression):
            if len(expression.parameters) != 1:
                raise ProgramError(expression, 'Some expects exactly one argument')
            if isinstance(expected, model.types.TypeOption):
                inner_type = expected.inner_type
            elif expected is not None and expected.contains_parameters:
                inner_type = expected
            else:
                # Without an expected Option type, the argument determines it, as in Rust.
                argument = self.compile(expression.parameters[0])
                return TypedExpression(model.types.TypeOption(argument.value_type), 'some', (argument,))
            argument = self.compile(expression.parameters[0], inner_type)
            return TypedExpression(expected, 'some', (argument,))
        if isinstance(expression, model.FunctionCall):
            if not isinstance(expression.fun, model.Reference):
                raise ProgramError(expression.fun, 'Function is not a reference')
            name = '.'.join(identifier.id_str for identifier in expression.fun.identifiers)
            function = self.scope.resolve_function(expression.fun, required=False)
            if function is None:
                raise ProgramError(expression.fun, f'Cannot find function {name!r}')
            if self.is_runtime(expression) and function.is_constant:
                raise ProgramError(expression.fun,
                                   f'{name} is a compile-time function and requires constant arguments')
            if len(expression.parameters) != len(function.parameter_types):
                raise ProgramError(expression, f'Function {name!r} expected {len(function.parameter_types)} parameters')
            arguments = tuple(self.compile(argument, value_type)
                              for argument, value_type in zip(expression.parameters, function.parameter_types))
            return TypedExpression(function.return_type, 'call', arguments,
                                     (function, tuple(expression.parameters), expression))
        value_type = expected or self.known_type(expression)
        if value_type is None:
            raise ProgramError(expression, 'Cannot infer expression type; add a type annotation')
        if self.is_runtime(expression):
            check_runtime_type(value_type, expression)
        if value_type == model.types.ValueType:
            actual_type = self.known_type(expression)
            if actual_type is not None:
                check_value_type(actual_type, value_type, expression)
        value = self.scope.get_const_value(expression, value_type, par_values=self.parameters)
        return TypedExpression(value_type, 'constant', payload=value)

    def member_call(self, call, value_type, member):
        operation, overflow = member
        result_type = model.types.TypeOption(value_type) if overflow == 'checked' else value_type
        if operation == 'convert':
            if len(call.parameters) != 1:
                raise ProgramError(call, f'{call.fun} expects exactly one argument')
            # As in Rust, a literal takes the target type for From (i8::from(5)) and
            # otherwise the default literal type (i8::try_from(300) converts an i32).
            argument = call.parameters[0]
            literal_type = value_type if overflow == 'lossless' else None
            source = self.compile(argument, None if self.context_type(argument) else literal_type)
            return TypedExpression(result_type, 'convert', (source,), overflow)
        if len(call.parameters) != 2:
            raise ProgramError(call, f'{call.fun} expects exactly two arguments')
        left = self.compile(call.parameters[0], value_type)
        right = self.compile(call.parameters[1], value_type)
        return TypedExpression(result_type, 'arith', (left, right), member)

    def option_operand(self, expression, expected=None):
        option = self.compile(expression, expected)
        if isinstance(option.value_type, model.types.TypeOption):
            return option, option.value_type.inner_type
        raise ProgramError(expression, f'Expected an Option value, got {option.value_type}')

    def unwrap(self, call, receiver, expected):
        # Programs must not fail at runtime, so Unwrap is compile-time only.
        if self.is_runtime(call):
            raise ProgramError(call, 'Unwrap is compile-time only; use UnwrapOr or if let')
        if call.parameters:
            raise ProgramError(call, 'Unwrap expects no arguments')
        option_type = (model.types.TypeOption(expected)
                       if expected is not None and self.context_type(receiver) is None else None)
        option, inner_type = self.option_operand(receiver, option_type)
        return TypedExpression(inner_type, 'unwrap', (option,))

    def unwrap_or(self, call, receiver, expected):
        if len(call.parameters) != 1:
            raise ProgramError(call, 'UnwrapOr expects exactly one argument')
        # An untyped receiver such as None takes its type from the expected result.
        option_type = (model.types.TypeOption(expected)
                       if expected is not None and self.context_type(receiver) is None else None)
        option, inner_type = self.option_operand(receiver, option_type)
        fallback = self.compile(call.parameters[0], inner_type)
        return TypedExpression(inner_type, 'unwrap_or', (option, fallback))

    def bind_pattern(self, expression, inner_type):
        """Bind the if-let name in self.names and return its slot, or None for _."""
        name = expression.name.id_str
        if name == '_':
            return None
        if name in self.protected:
            raise ProgramError(expression.name, f'Cannot shadow procedure port or parameter {name!r}')
        slot = self.next_slot
        self.next_slot += 1
        self.bind(name, TypedExpression(inner_type, 'local', payload=slot, runtime=self.is_runtime(expression)),
                  initialized=True)
        return slot

    def if_let(self, expression, expected):
        if expression.constructor.id_str != 'Some':
            raise ProgramError(expression.constructor, 'if let patterns must have the form Some(name)')
        option, inner_type = self.option_operand(expression.scrutinee)
        before = dict(self.initialization)
        branch_type = (model.types.unit if expression.otherwise is None else
                       expected or self.context_type(expression.otherwise))
        previous = dict(self.names)
        try:
            slot = self.bind_pattern(expression, inner_type)
            then = self.compile(expression.then, branch_type or self.context_type(expression.then))
            then_state = dict(self.initialization)
        finally:
            self.names = previous
        self.initialization = dict(before)
        otherwise = (self.compile(expression.otherwise, then.value_type)
                     if expression.otherwise is not None else TypedExpression(model.types.unit, 'unit'))
        self.merge_initialization(before, then_state, self.initialization)
        return TypedExpression(then.value_type, 'if_let', (option, then, otherwise), slot)

    def check_runtime_syntax(self, expression):
        if not self.is_runtime(expression):
            return
        if isinstance(expression, model.ParameterOption):
            raise ProgramError(expression, 'parameter_option is only available in compile-time expressions')
        if is_some_call(expression):
            for argument in expression.parameters:
                self.check_runtime_syntax(argument)
            return
        if (isinstance(expression, model.FunctionCall)
                and type_member(self.scope, expression.fun, self.parameters, self.names) is not None):
            for argument in expression.parameters:
                self.check_runtime_syntax(argument)
            return
        if (isinstance(expression, model.FunctionCall)
                and isinstance(property_reference(self.scope, expression.fun, self.names), PropertyAccess)):
            self.check_runtime_syntax(property_reference(self.scope, expression.fun, self.names).receiver)
            for argument in expression.parameters:
                self.check_runtime_syntax(argument)
            return
        if (isinstance(expression, model.FunctionCall) and isinstance(expression.fun, model.Reference)
                and self.scope.resolve_function(expression.fun, required=False) is not None):
            # Calls with runtime arguments are checked when the call is compiled.
            for argument in expression.parameters:
                self.check_runtime_syntax(argument)
            return
        if isinstance(expression, (model.FunctionCall, model.BracketOperator)):
            raise ProgramError(expression, 'Runtime calls and indexing are not supported in procedures')
        if isinstance(expression, model.StructLiteral):
            for field in expression.fields:
                self.check_runtime_syntax(field.value)
        elif isinstance(expression, model.ListLiteral):
            for value in expression.values:
                self.check_runtime_syntax(value)
        elif isinstance(expression, BinaryExpression):
            self.check_runtime_syntax(expression.left)
            if expression.operator not in SHIFT_OPERATORS:
                self.check_runtime_syntax(expression.right)
        elif isinstance(expression, UnaryExpression):
            self.check_runtime_syntax(expression.operand)
        elif isinstance(expression, PropertyAccess):
            self.check_runtime_syntax(expression.receiver)
        elif isinstance(expression, IfExpression):
            self.check_runtime_syntax(expression.condition)
            self.check_runtime_syntax(expression.then)
            self.check_runtime_syntax(expression.otherwise)
        elif isinstance(expression, IfLetExpression):
            self.check_runtime_syntax(expression.scrutinee)
            self.check_runtime_syntax(expression.then)
            self.check_runtime_syntax(expression.otherwise)
        elif isinstance(expression, ExpressionBlock):
            for statement in expression.statements:
                self.check_runtime_syntax(statement.initializer if isinstance(statement, LocalBinding)
                                          else statement.expression)
        elif isinstance(expression, AssignmentExpression):
            self.check_runtime_syntax(expression.expression)

    def context_type(self, expression):
        # Unsuffixed literals accept context instead of imposing their default.
        if isinstance(expression, (model.IntNumber, model.StringConstant)):
            return None
        if isinstance(expression, ExpressionBlock):
            if not expression.statements:
                return model.types.unit
            tail = expression.statements[-1]
            if isinstance(tail, LocalBinding) or tail.terminated:
                return model.types.unit
            return self.context_type(tail.expression) if len(expression.statements) == 1 else None
        if isinstance(expression, BinaryExpression) and expression.operator in SHIFT_OPERATORS:
            return self.context_type(expression.left)
        if isinstance(expression, BinaryExpression) and expression.operator in (*ARITHMETIC_OPERATORS, *BITWISE_OPERATORS):
            return self.context_type(expression.left) or self.context_type(expression.right)
        if isinstance(expression, UnaryExpression):
            return self.context_type(expression.operand)
        if is_some_call(expression):
            inner_type = (self.context_type(expression.parameters[0])
                          if len(expression.parameters) == 1 else None)
            return model.types.TypeOption(inner_type) if inner_type is not None else None
        return self.known_type(expression)

    def known_type(self, expression):
        expression = property_reference(self.scope, expression, self.names)
        if isinstance(expression, PropertyAccess):
            receiver_type = self.known_type(expression.receiver)
            if isinstance(receiver_type, model.types.TypeStruct):
                return receiver_type.fields.get(expression.name.id_str)
            return model.types.boolean if expression.name.id_str in ('IsSome', 'IsNone') else None
        if isinstance(expression, model.ParameterOption):
            parameter = self.parameter_option_declaration(expression)
            value_type = model.types.TypeOption(parameter.type_resolved)
            return resolve_type(value_type, self.parameters) if self.runtime else value_type
        if isinstance(expression, model.Reference):
            if expression.length == 1:
                name = expression.identifiers[0].id_str
                if name in self.names:
                    return self.names[name].value_type
                if name in ('true', 'false'):
                    return model.types.boolean
                parameter = self.scope.static_declarations.get(name)
                if parameter is not None and not self.runtime:
                    return (resolve_type(parameter.type_resolved, self.parameters)
                            if self.parameters else parameter.type_resolved)
            elif expression.length >= 2:
                reference = model.Reference.from_list(*expression.identifiers[:-1],
                                                       source_position=expression.source_position)
                try:
                    value_type = self.scope.resolve_type(reference, par_values=self.parameters)
                except ProgramError:
                    value_type = None
                if isinstance(value_type, model.types.TypeEnum):
                    return value_type
        if isinstance(expression, (model.Reference, model.BracketOperator)):
            try:
                self.scope.resolve_type(expression, par_values=self.parameters)
            except ProgramError:
                pass
            else:
                return model.types.ValueType
        if isinstance(expression, model.IntNumber):
            return model.types.int32
        if isinstance(expression, model.StringConstant):
            return model.types.String
        if isinstance(expression, model.UnitLiteral):
            return model.types.unit
        if isinstance(expression, BinaryExpression) and expression.operator in SHIFT_OPERATORS:
            return self.known_type(expression.left)
        if isinstance(expression, BinaryExpression) and expression.operator in (*ARITHMETIC_OPERATORS, *BITWISE_OPERATORS):
            return (self.context_type(expression.left) or self.context_type(expression.right)
                    or self.known_type(expression.left))
        if isinstance(expression, UnaryExpression):
            return self.known_type(expression.operand)
        if isinstance(expression, (UnaryExpression, BinaryExpression)):
            return model.types.boolean
        if isinstance(expression, ForExpression):
            return model.types.unit
        if isinstance(expression, AssignmentExpression):
            return model.types.unit
        if isinstance(expression, ExpressionBlock):
            if not expression.statements:
                return model.types.unit
            tail = expression.statements[-1]
            if isinstance(tail, LocalBinding) or tail.terminated:
                return model.types.unit
            if len(expression.statements) == 1:
                return self.known_type(tail.expression)
        if isinstance(expression, IfExpression):
            if expression.otherwise is None:
                return model.types.unit
            # A typed branch takes precedence over a literal one.
            return (self.context_type(expression.then) or self.context_type(expression.otherwise)
                    or self.known_type(expression.then))
        if isinstance(expression, IfLetExpression):
            if expression.otherwise is None:
                return model.types.unit
            option_type = self.known_type(expression.scrutinee)
            if isinstance(option_type, model.types.TypeOption):
                previous, next_slot = dict(self.names), self.next_slot
                previous_bindings, previous_init = dict(self.bindings), dict(self.initialization)
                try:
                    self.bind_pattern(expression, option_type.inner_type)
                    then_type = self.context_type(expression.then)
                finally:
                    self.names, self.next_slot = previous, next_slot
                    self.bindings, self.initialization = previous_bindings, previous_init
                if then_type is not None:
                    return then_type
            return self.context_type(expression.otherwise) or self.known_type(expression.otherwise)
        if isinstance(expression, model.StructLiteral):
            return self.scope.resolve_type(expression.struct_type, par_values=self.parameters)
        if is_some_call(expression):
            inner_type = self.known_type(expression.parameters[0]) if len(expression.parameters) == 1 else None
            return model.types.TypeOption(inner_type) if inner_type is not None else None
        if isinstance(expression, model.FunctionCall):
            # Type members take precedence over methods, as in _compile().
            member = type_member(self.scope, expression.fun, self.parameters, self.names)
            if member is not None:
                value_type, (operation, overflow) = member
                return model.types.TypeOption(value_type) if overflow == 'checked' else value_type
            method = property_reference(self.scope, expression.fun, self.names)
            if isinstance(method, PropertyAccess):
                option_type = self.known_type(method.receiver)
                return option_type.inner_type if isinstance(option_type, model.types.TypeOption) else None
        if isinstance(expression, model.FunctionCall) and isinstance(expression.fun, model.Reference):
            function = self.scope.resolve_function(expression.fun, required=False)
            if function is not None:
                return function.return_type
        return None


def compile_constant(scope, expression, expected, parameters):
    compiler = ExpressionCompiler(scope, parameters)
    typed_expression = compiler.compile(expression, expected)
    return ExpressionValue(typed_expression, ast_element=expression)


def fold_constants(expression):
    """Evaluate constant islands without evaluating runtime control flow."""
    if not expression.runtime:
        value = expression.value_type.verify_value(expression.evaluate({}, {}))
        return TypedExpression(expression.value_type, 'constant', source=expression.source,
                               payload=ConstantValue(expression.value_type, value, ast_element=expression.source))
    folded = replace(expression, arguments=tuple(fold_constants(argument) for argument in expression.arguments))
    if folded.operation in SHIFT_OPERATORS:
        folded.check_shift_count(folded.arguments[1].evaluate({}, {}))
    return folded


def compile_procedure(procedure, instance, source_types, output_types, intern_constant, intern_function,
                      multi_sources=None):
    declaration = procedure.declaration_procedure
    parameters = instance.static_values
    multi_sources = multi_sources or {}
    names = {name: TypedExpression(value_type, 'input', payload=name, runtime=True)
             for name, value_type in source_types.items() if isinstance(name, str)}
    names.update({name: TypedExpression(value_type, 'multi_input', payload=members, runtime=True)
                  for name, (value_type, members) in multi_sources.items()})
    input_names = set(names)
    protected = set(output_types) | set(instance.node_type.static_declarations)
    overlap = ((input_names & protected)
               | (set(output_types) & set(instance.node_type.static_declarations)))
    if overlap:
        raise ProgramError(declaration, f'Procedure names must be distinct: {sorted(overlap)!r}')
    for parameter, value in {**instance.parameter_values, **instance.constant_values}.items():
        name = parameter.name.id_str
        if isinstance(value, model.types.Type):
            value = ConstantValue(model.types.ValueType, value, ast_element=parameter)
        if isinstance(value, Value):
            names[name] = TypedExpression(value.value_type, 'constant', payload=value)
    compiler = ExpressionCompiler(instance.node_type, parameters, runtime=True,
                                  names=names, protected=protected | input_names)
    for name, value_type in output_types.items():
        compiler.bind(name, TypedExpression(value_type, 'output', payload=name, runtime=True))
    body = compiler.compile(declaration.body, model.types.unit)
    missing = [name for name in output_types if not compiler.initialization['output', name][1]]
    if missing:
        raise ProgramError(declaration.body, f'Procedure did not assign outputs on every path: {sorted(missing)!r}')
    procedure.static_usage = frozenset(compiler.static_usage)
    body = fold_constants(body)
    return InstructionCompiler(source_types, output_types, intern_constant, intern_function).body(body)
