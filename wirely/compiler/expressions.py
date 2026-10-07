"""Shared expression syntax, checking, and restricted procedure bodies."""

from dataclasses import dataclass, replace

from . import model as blocks
from .ast import SourceElement, ProgramError, Value as AstValue, check_struct_literal
from .types import resolve_type, check_runtime_type
from .value import Value, check_value_type
from ..program import (
    ARITHMETIC_OPCODES,
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


ARITHMETIC_OPERATORS = ('add', 'sub', 'mul', 'div', 'rem')


class UnaryExpression(Expression):
    def __init__(self, operand, *, operator='not', source_position):
        super().__init__(source_position=source_position)
        self.operand = operand
        self.operator = operator

    @staticmethod
    def negate(operand, *, source_position):
        # A negated literal stays a literal, so -128 can be an int8.
        if isinstance(operand, blocks.IntNumber):
            return operand.negated(source_position=source_position)
        return UnaryExpression(operand, operator='neg', source_position=source_position)


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


def type_member(scope, reference, parameters, local_names=()):
    """Resolve Type.Member to (type, member), or None when the callee is not a type member."""
    if not isinstance(reference, blocks.Reference) or reference.length < 2:
        return None
    root = reference.identifiers[0].id_str
    declaration = scope.static_declarations.get(root)
    # Value names shadow type names.
    if root in local_names or (reference.length == 2 and declaration is not None
                               and not is_type_declaration(declaration)):
        return None
    prefix = blocks.Reference.from_list(*reference.identifiers[:-1],
                                        source_position=reference.source_position)
    try:
        value_type = scope.resolve_type(prefix, par_values=parameters)
    except ProgramError:
        return None
    name = reference.identifiers[-1].id_str
    member = None
    if value_type.contains_parameters or isinstance(value_type, blocks.types.TypeInt):
        member = INTEGER_MEMBERS.get(name)
    function = scope.resolve_function(reference, required=False)
    if member is None:
        if function is None:
            raise ProgramError(reference, f'Type {value_type} has no member {name!r}')
        return None
    if function is not None:
        raise ProgramError(reference, f'{reference} is both a type member and a function')
    return value_type, member


def is_type_declaration(declaration):
    declared_type = getattr(declaration, 'type_resolved', None)
    return (declared_type == blocks.types.ValueType
            or (isinstance(declaration.ty, blocks.Reference) and declaration.ty.length == 1
                and declaration.ty.identifiers[0].id_str == 'ValueType'))


class IfExpression(Expression):
    def __init__(self, condition, then, otherwise, *, source_position):
        super().__init__(source_position=source_position)
        self.condition = condition
        self.then = then
        self.otherwise = otherwise


class IfLetExpression(Expression):
    def __init__(self, constructor, name, scrutinee, then, otherwise, *, source_position):
        super().__init__(source_position=source_position)
        self.constructor = constructor
        self.name = name
        self.scrutinee = scrutinee
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
        elif self.operation in ('unwrap_or', 'if_let'):
            option = self.arguments[0]
            if option.value_type.contains_parameters and not isinstance(option.value_type, blocks.types.TypeOption):
                return
            if not isinstance(option.value_type, blocks.types.TypeOption):
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
            if not integer_type.contains_parameters and not isinstance(integer_type, blocks.types.TypeInt):
                raise ProgramError(self.source, f'Integer arithmetic requires an integer value, got {integer_type}')
        elif self.operation in ('is_some', 'is_none'):
            receiver = self.arguments[0]
            if (not receiver.value_type.contains_parameters
                    and not isinstance(receiver.value_type, blocks.types.TypeOption)):
                name = 'IsSome' if self.operation == 'is_some' else 'IsNone'
                raise ProgramError(self.source, f'Value of type {receiver.value_type} has no property {name!r}')

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
        if self.operation == 'field':
            return self.arguments[0].evaluate(inputs, locals)[self.payload]
        operation = self.operation
        if operation == 'input':
            return inputs[self.payload]
        if operation == 'local':
            return locals[self.payload]
        if operation == 'block':
            locals = dict(locals)
            for slot, expression in zip(self.payload, self.arguments[:-1]):
                locals[slot] = expression.evaluate(inputs, locals)
            return self.arguments[-1].evaluate(inputs, locals)
        if operation in ('arith', 'neg'):
            return self.evaluate_arithmetic(inputs, locals)
        left = self.arguments[0].evaluate(inputs, locals)
        if operation == 'not':
            return not left
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
                locals = {**locals, self.payload: left.value}
            return self.arguments[1].evaluate(inputs, locals)
        right = self.arguments[1].evaluate(inputs, locals)
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


class InstructionCompiler:
    """Lower resolved procedure expressions to forward-only instructions."""

    def __init__(self, source_types, result_types, intern_constant):
        self.inputs = {name: Operand(OperandKind.INPUT, index)
                       for index, name in enumerate(source_types)}
        self.results = {name: Operand(OperandKind.RESULT, index)
                        for index, name in enumerate(result_types)}
        self.intern_constant = intern_constant
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
        if operation in ('constant', 'input', 'local'):
            if operation == 'constant':
                source = Operand(OperandKind.CONST, self.intern_constant(
                    expression.value_type, expression.payload.evaluate(),
                ))
            elif operation == 'input':
                source = self.inputs[expression.payload]
            else:
                source = self.local(expression.payload, expression.value_type)
            if destination is None:
                return source
            self.emit(Opcode.COPY, destination, source)
            return destination
        if operation == 'block':
            for slot, value in zip(expression.payload, expression.arguments[:-1]):
                self.compile(value, self.local(slot, value.value_type))
            return self.compile(expression.arguments[-1], destination)
        if destination is None:
            destination = self.allocate(expression.value_type)
        left = self.compile(expression.arguments[0])
        if operation == 'if':
            skip_then = self.emit(Opcode.JUMP_IF_FALSE, left, BranchTarget(0))
            self.compile(expression.arguments[1], destination)
            skip_else = self.emit(Opcode.JUMP, BranchTarget(0))
            self.patch(skip_then)
            self.compile(expression.arguments[2], destination)
            self.patch(skip_else)
        elif operation in ('unwrap_or', 'if_let'):
            present = self.allocate(blocks.types.boolean)
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
        elif operation in ('not', 'is_some', 'is_none'):
            self.emit(Opcode.NOT if operation == 'not' else Opcode.IS_SOME, destination, left)
            if operation == 'is_none':
                self.emit(Opcode.NOT, destination, destination)
        elif operation in ('eq', 'ne'):
            right = self.compile(expression.arguments[1])
            self.emit(Opcode.EQUAL, destination, left, right)
            if operation == 'ne':
                self.emit(Opcode.NOT, destination, destination)
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

    def body(self, statements):
        for slot, expression in statements:
            destination = (self.results[slot] if isinstance(slot, str)
                           else self.local(slot, expression.value_type))
            self.compile(expression, destination)
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
            receiver = self.compile(expression.receiver)
            receiver_type = receiver.value_type
            if isinstance(receiver_type, blocks.types.TypeStruct) and name in receiver_type.fields:
                return TypedExpression(receiver_type.fields[name], 'field', (receiver,), name)
            if (name in ('IsSome', 'IsNone') and (isinstance(receiver_type, blocks.types.TypeOption)
                                                  or receiver_type.contains_parameters)):
                return TypedExpression(boolean, 'is_some' if name == 'IsSome' else 'is_none',
                                       (receiver,))
            if receiver_type.contains_parameters:
                raise ProgramError(expression.name, 'Field access requires a known struct type')
            raise ProgramError(expression.name, f'Value of type {receiver_type} has no property {name!r}')
        if isinstance(expression, blocks.FunctionCall):
            member = type_member(self.scope, expression.fun, self.parameters, self.names)
            if member is not None:
                return self.member_call(expression, *member)
        if isinstance(expression, blocks.FunctionCall):
            method = property_reference(self.scope, expression.fun, self.names)
            if isinstance(method, PropertyAccess):
                if method.name.id_str == 'UnwrapOr':
                    return self.unwrap_or(expression, method.receiver, expected)
                if method.name.id_str in ('IsSome', 'IsNone'):
                    raise ProgramError(expression, 'Option properties cannot be called; omit parentheses')
                receiver = self.compile(method.receiver)
                raise ProgramError(method.name,
                                   f'Value of type {receiver.value_type} has no method {method.name.id_str!r}')
        if isinstance(expression, blocks.ParameterOption):
            if self.runtime:
                raise ProgramError(expression, 'parameter_option is only available in compile-time expressions')
            parameter = expression.declaration(self.scope, self.names)
            value = blocks.value.ParameterOptionValue(parameter, ast_element=expression)
            return TypedExpression(value.value_type, 'constant', payload=value)
        if isinstance(expression, UnaryExpression) and expression.operator == 'neg':
            if self.runtime:
                raise ProgramError(expression, 'Negation is compile-time only; use T.WrappingSub(0, value), '
                                   'T.SaturatingSub(0, value), or T.CheckedSub(0, value)')
            operand = self.compile(expression.operand, expected)
            return TypedExpression(operand.value_type, 'neg', (operand,))
        if isinstance(expression, UnaryExpression):
            operand = self.compile(expression.operand, boolean)
            return TypedExpression(boolean, 'not', (operand,))
        if isinstance(expression, BinaryExpression) and expression.operator in ARITHMETIC_OPERATORS:
            if self.runtime:
                name = expression.operator.capitalize()
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
        if isinstance(expression, IfLetExpression):
            return self.if_let(expression, expected)
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

    def member_call(self, call, value_type, member):
        operation, overflow = member
        if len(call.parameters) != 2:
            raise ProgramError(call, f'{call.fun} expects exactly two arguments')
        left = self.compile(call.parameters[0], value_type)
        right = self.compile(call.parameters[1], value_type)
        result_type = blocks.types.TypeOption(value_type) if overflow == 'checked' else value_type
        return TypedExpression(result_type, 'arith', (left, right), member)

    def option_operand(self, expression, expected=None):
        option = self.compile(expression, expected)
        if isinstance(option.value_type, blocks.types.TypeOption):
            return option, option.value_type.inner_type
        raise ProgramError(expression, f'Expected an Option value, got {option.value_type}')

    def unwrap_or(self, call, receiver, expected):
        if len(call.parameters) != 1:
            raise ProgramError(call, 'UnwrapOr expects exactly one argument')
        # An untyped receiver such as None takes its type from the expected result.
        option_type = (blocks.types.TypeOption(expected)
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
            raise ProgramError(expression.name, f'Cannot shadow procedure binding or parameter {name!r}')
        slot = self.next_slot
        self.next_slot += 1
        self.names[name] = TypedExpression(inner_type, 'local', payload=slot)
        return slot

    def if_let(self, expression, expected):
        if expression.constructor.id_str != 'Some':
            raise ProgramError(expression.constructor, 'if let patterns must have the form Some(name)')
        option, inner_type = self.option_operand(expression.scrutinee)
        branch_type = expected or self.context_type(expression.otherwise)
        previous = dict(self.names)
        try:
            slot = self.bind_pattern(expression, inner_type)
            then = self.compile(expression.then, branch_type or self.context_type(expression.then))
        finally:
            self.names = previous
        otherwise = self.compile(expression.otherwise, then.value_type)
        return TypedExpression(then.value_type, 'if_let', (option, then, otherwise), slot)

    def check_runtime_syntax(self, expression):
        if isinstance(expression, blocks.ParameterOption):
            raise ProgramError(expression, 'parameter_option is only available in compile-time expressions')
        if (isinstance(expression, blocks.FunctionCall)
                and type_member(self.scope, expression.fun, self.parameters, self.names) is not None):
            for argument in expression.parameters:
                self.check_runtime_syntax(argument)
            return
        if (isinstance(expression, blocks.FunctionCall)
                and isinstance(property_reference(self.scope, expression.fun, self.names), PropertyAccess)):
            self.check_runtime_syntax(property_reference(self.scope, expression.fun, self.names).receiver)
            for argument in expression.parameters:
                self.check_runtime_syntax(argument)
            return
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
        elif isinstance(expression, IfLetExpression):
            self.check_runtime_syntax(expression.scrutinee)
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
        if isinstance(expression, BinaryExpression) and expression.operator in ARITHMETIC_OPERATORS:
            return self.context_type(expression.left) or self.context_type(expression.right)
        if isinstance(expression, UnaryExpression) and expression.operator == 'neg':
            return self.context_type(expression.operand)
        return self.known_type(expression)

    def known_type(self, expression):
        expression = property_reference(self.scope, expression, self.names)
        if isinstance(expression, PropertyAccess):
            receiver_type = self.known_type(expression.receiver)
            if isinstance(receiver_type, blocks.types.TypeStruct):
                return receiver_type.fields.get(expression.name.id_str)
            return blocks.types.boolean if expression.name.id_str in ('IsSome', 'IsNone') else None
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
        if isinstance(expression, BinaryExpression) and expression.operator in ARITHMETIC_OPERATORS:
            return (self.context_type(expression.left) or self.context_type(expression.right)
                    or self.known_type(expression.left))
        if isinstance(expression, UnaryExpression) and expression.operator == 'neg':
            return self.known_type(expression.operand)
        if isinstance(expression, (UnaryExpression, BinaryExpression)):
            return blocks.types.boolean
        if isinstance(expression, ExpressionBlock) and not expression.bindings:
            return self.known_type(expression.result)
        if isinstance(expression, IfExpression):
            # A typed branch takes precedence over a literal one.
            return (self.context_type(expression.then) or self.context_type(expression.otherwise)
                    or self.known_type(expression.then))
        if isinstance(expression, IfLetExpression):
            option_type = self.known_type(expression.scrutinee)
            if isinstance(option_type, blocks.types.TypeOption):
                previous, next_slot = dict(self.names), self.next_slot
                try:
                    self.bind_pattern(expression, option_type.inner_type)
                    then_type = self.context_type(expression.then)
                finally:
                    self.names, self.next_slot = previous, next_slot
                if then_type is not None:
                    return then_type
            return self.context_type(expression.otherwise) or self.known_type(expression.otherwise)
        if isinstance(expression, blocks.StructLiteral):
            return self.scope.resolve_type(expression.struct_type, par_values=self.parameters)
        if isinstance(expression, blocks.FunctionCall):
            method = property_reference(self.scope, expression.fun, self.names)
            if isinstance(method, PropertyAccess):
                option_type = self.known_type(method.receiver)
                return option_type.inner_type if isinstance(option_type, blocks.types.TypeOption) else None
            member = type_member(self.scope, expression.fun, self.parameters, self.names)
            if member is not None:
                value_type, (operation, overflow) = member
                return blocks.types.TypeOption(value_type) if overflow == 'checked' else value_type
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


def compile_procedure(procedure, instance, source_types, result_types, intern_constant):
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
        if isinstance(value, Value):
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
    return InstructionCompiler(source_types, result_types, intern_constant).body(statements)
