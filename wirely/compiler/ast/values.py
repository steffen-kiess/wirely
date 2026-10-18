"""Literals, references, calls, and resolved port references."""

import dataclasses
import typing
import re
from collections.abc import Collection
from typing import Any


from wirely.compiler import model
from wirely.util import check_parameters

from .base import SourcePosition, SourceElement, SyntaxElement, CompilerHint, ProgramError, Identifier, Value, syntax
from .specifiers import PortDirection, PortDirectionSpecifier


@syntax
class UnitLiteral(Value):
    def __repr__(self) -> str:
        return 'UnitLiteral()'


@syntax
class IntNumber(Value):
    token: str
    _: dataclasses.KW_ONLY
    # A negated literal stays a literal, so -128 can be an int8.
    negative: bool = False
    value: int = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        text = str(self.token)
        if text.lower().startswith('0x'):
            base = 16
        elif text.lower().startswith('0o'):
            base = 8
        else:
            base = 10
        try:
            value = int(text, base)
        except ValueError:
            raise ProgramError(self, f'Invalid integer literal: {text!r}') from None
        object.__setattr__(self, 'value', -value if self.negative else value)

    @classmethod
    def positive(cls, token: str, *, source_position: SourcePosition) -> 'IntNumber':
        return cls(str(token), source_position=source_position)

    def negated(self, *, source_position: SourcePosition) -> 'IntNumber':
        return IntNumber(self.token, negative=not self.negative, source_position=source_position)


@syntax
class FloatNumber(Value):
    token: str
    value: float = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        # TODO: Does this parse the value correctly?
        object.__setattr__(self, 'value', float(self.token))


@syntax
class StringConstant(Value):
    token: str
    value: str = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'value', self._decode(self.token.replace('\r\n', '\n')))

    def _decode(self, val: str) -> str:
        if '\r' in val:
            raise ProgramError(self, 'Bare carriage return in string literal')
        # https://doc.rust-lang.org/reference/tokens.html#r-lex.token.literal.str
        # https://doc.rust-lang.org/reference/tokens.html#r-lex.token.literal.str-raw
        if val.startswith('r"'):
            if len(val) < 3 or val[-1] != '"':
                raise ProgramError(self, 'Invalid raw string literal')
            value = val[2:-1]
            if '"' in value:
                raise ProgramError(self, 'Invalid raw string literal')
            return value
        elif val.startswith('r#"'):
            if len(val) < 5 or val[-2:] != '"#':
                raise ProgramError(self, 'Invalid raw string literal')
            value = val[3:-2]
            if '"#' in value:
                raise ProgramError(self, 'Invalid raw string literal')
            return value
        else:
            if len(val) < 2 or val[0] != '"' or val[-1] != '"':
                raise ProgramError(self, 'Invalid string literal')
            return self._decode_quoted(val[1:-1])

    def _decode_quoted(self, value: str) -> str:
        escapes = {
            '0': '\0', 't': '\t', 'n': '\n', 'r': '\r',
            '"': '"', "'": "'", '\\': '\\',
        }
        hex_digits = '0123456789abcdefABCDEF'
        result = []
        index = 0
        while index < len(value):
            char = value[index]
            index += 1
            if char != '\\':
                result.append(char)
                continue
            if index == len(value):
                raise ProgramError(self, 'Incomplete string escape')
            escape = value[index]
            index += 1
            if escape in escapes:
                result.append(escapes[escape])
            elif escape == '\n':
                while index < len(value) and value[index] in ' \t\r\n':
                    index += 1
            elif escape == 'x':
                digits = value[index:index + 2]
                if len(digits) != 2 or any(digit not in hex_digits for digit in digits):
                    raise ProgramError(self, 'Hex escape requires two hexadecimal digits')
                result.append(chr(int(digits, 16)))
                index += 2
            elif escape == 'u':
                if index == len(value) or value[index] != '{':
                    raise ProgramError(self, 'Unicode escape requires braces')
                end = value.find('}', index + 1)
                if end == -1:
                    raise ProgramError(self, 'Unterminated Unicode escape')
                digits = value[index + 1:end]
                if not re.fullmatch(r'[0-9a-fA-F][0-9a-fA-F_]*', digits):
                    raise ProgramError(self, 'Invalid Unicode escape digits')
                digits = digits.replace('_', '')
                if len(digits) > 6:
                    raise ProgramError(self, 'Unicode escape allows at most six hexadecimal digits')
                code_point = int(digits, 16)
                if code_point > 0x10ffff or 0xd800 <= code_point <= 0xdfff:
                    raise ProgramError(self, 'Unicode escape is not a Unicode scalar value')
                result.append(chr(code_point))
                index = end + 1
            else:
                escape_text = '\\' + escape
                raise ProgramError(self, f'Unknown string escape: {escape_text!r}')
        return ''.join(result)


@syntax
class Reference(Value):
    identifiers: tuple[Identifier, ...]

    @staticmethod
    @check_parameters
    def from_list(*identifiers: Identifier, source_position: SourcePosition) -> 'Reference':
        return Reference(identifiers, source_position=source_position)

    @staticmethod
    @check_parameters
    def create_default(direction_spec: PortDirectionSpecifier, *, source_position: SourcePosition) -> 'Reference':
        direction = direction_spec.direction
        # TODO: Clean up
        if direction == PortDirection.Input:
            name = 'default input'
        elif direction == PortDirection.Output:
            name = 'default output'
        else:
            raise Exception(f'Unknown value for direction: {direction}')
        identifier = Identifier(name, source_position=source_position)
        return Reference((identifier,), source_position=source_position)

    def __repr__(self) -> str:
        # TODO: Somehow show which enclosing node a parameter belongs to?
        # return '.'.join([i.id_str for i in self.identifiers]) + f' [{self.source_position.start_line}]'
        return '.'.join([i.id_str for i in self.identifiers])

    @property
    def length(self) -> int:
        return len(self.identifiers)

    @check_parameters
    def resolve(self, graph: 'model.NodeType', direction: PortDirection, purpose: str) -> 'ResolvedPortReference':
        if direction in [PortDirection.Input, PortDirection.Output]:
            if self.length == 1:
                node_name_id = self.identifiers[0]
                port_name_id = None
            elif self.length == 2:
                node_name_id = self.identifiers[0]
                port_name_id = self.identifiers[1]
            else:
                raise ProgramError(self, f'Got unexpected number of identifiers in {purpose}: {self}')
            return ResolvedPortReference(graph, direction, node_name_id, port_name_id, purpose)
        else:
            raise Exception(f'Unknown PortDirection: {direction!r}')


@syntax
class ListLiteral(Value):
    values: tuple[Value, ...]


@syntax
class FunctionCall(Value):
    fun: Value
    parameters: tuple[Value, ...]


@syntax
class OptionalExpression(Value):
    """optional(x): an optional parameter, or an event in a procedure body, as an option."""

    parameters: tuple[Value, ...]

    def argument(self) -> Value:
        if len(self.parameters) != 1:
            raise ProgramError(self, 'optional expects exactly one argument')
        return self.parameters[0]

    def declaration(self, scope: 'model.NodeType', local_names: Collection[str] = (), *,
                    events: bool = False) -> 'model.NodeParameter':
        """Return the optional parameter the argument names.

        events mentions event dependencies as alternatives in errors, for
        procedure bodies.
        """
        reference = self.argument()
        if not isinstance(reference, Reference) or reference.length != 1:
            raise ProgramError(reference, 'optional requires an unqualified optional parameter reference')
        name = reference.identifiers[0].id_str
        parameter = scope.parsed_parameters.get(name)
        if name in local_names or parameter is None or parameter.optional_specifier is None:
            alternative = ' or a single event dependency' if events else ''
            raise ProgramError(reference, f'optional requires an optional parameter without a default{alternative}')
        return parameter


@syntax
class BracketOperator(Value):
    base: Value
    parameter: Value


# TODO: Remove and merge into Reference?
class ResolvedPortReference(SourceElement):
    @check_parameters
    # TODO: port_name should not be typing.Optional
    def __init__(self, graph: 'model.NodeType', direction: PortDirection, node_name: Identifier,
                 port_name: typing.Optional[Identifier], purpose: str) -> None:
        if direction == PortDirection.Input:
            port_direction = 'input'
            port_dict_lambda = lambda node: node.input_ports_byname
        elif direction == PortDirection.Output:
            port_direction = 'output'
            port_dict_lambda = lambda node: node.output_ports_byname
        else:
            raise Exception(f'Unknown PortDirection: {direction!r}')

        self.node_name = node_name
        self.port_name = port_name

        self.node_name_str = self.node_name.id_str
        if self.port_name is None:
            # TODO: Clean up
            if direction == PortDirection.Input:
                # self.port_name_str = 'default input'
                self.port_name_str = ''
            elif direction == PortDirection.Output:
                # self.port_name_str = 'default output'
                self.port_name_str = ''
            else:
                raise Exception(f'Unknown value for direction: {direction}')
            # TODO: Remove port_name_elem (Hack)
            self.port_name_elem = node_name
        else:
            self.port_name_str = self.port_name.id_str
            self.port_name_elem = self.port_name

        # print(direction, repr(self.node_name_str), repr(self.port_name_str))
        static = graph.static_declarations.get(self.node_name_str)
        if static is not None:
            self.port: Any = static_source(static, self.node_name, self.port_name, direction)  # Any connection source or target.
            return

        local = getattr(graph, 'local_resources', {}).get(self.node_name_str)
        if local is not None:
            if self.port_name is not None:
                raise ProgramError(self.port_name, f'Binding {self.node_name_str!r} has no port {self.port_name_str!r}')
            self.port = local
            return

        # TODO: Should this be here?
        if direction == PortDirection.Input:
            if self.node_name_str in graph.output_ports and self.port_name_str == '':
                self.port = graph.output_ports[self.node_name_str]
                # TODO: This is a hack
                return
            variable = graph.value_nodes.get(self.node_name_str)
            if variable is not None:
                is_state = isinstance(variable, model.ast.DeclarationState)
                if self.port_name is not None:
                    kind = 'State' if is_state else 'Binding'
                    raise ProgramError(self.port_name, f'{kind} {self.node_name_str!r} has no input port {self.port_name_str!r}')
                variables = graph.state_nodes if is_state else graph.temporary_nodes
                self.port = variables[self.node_name_str]
                return
        if direction == PortDirection.Output:
            variable = graph.value_nodes.get(self.node_name_str)
            if isinstance(variable, model.ast.DeclarationState):
                if self.port_name_str not in ('old', 'new'):
                    raise ProgramError(self.port_name_elem, 'State connection sources must explicitly reference .old or .new')
                self.port = model.StateRead(
                    graph.state_nodes[self.node_name_str],
                    self.port_name_str, self.port_name_elem,
                )
                return
            if self.node_name_str in graph.input_ports and self.port_name_str == '':
                self.port = graph.input_ports[self.node_name_str]
                # TODO: This is a hack
                return
            if self.node_name_str in graph.temporary_nodes:
                if self.port_name_str != '':
                    raise ProgramError(self.port_name_elem, f'Binding {self.node_name_str!r} has no output port {self.port_name_str!r}')
                self.port = graph.temporary_nodes[self.node_name_str]
                # TODO: This is a hack
                return

        procedure = graph.procedures.get(self.node_name_str)
        if procedure is not None:
            self.port = procedure.resolve_port(self.node_name, self.port_name, direction == PortDirection.Input)
            return

        if self.node_name_str not in graph.instance_nodes:
            raise ProgramError(self.node_name, f'Unable to find {purpose} node {self.node_name_str!r}')
        self.node = graph.instance_nodes[self.node_name_str]
        port_dict = port_dict_lambda(self.node)
        if self.port_name_str in port_dict:
            self.port = port_dict[self.port_name_str]
        else:
            if self.port_name_str == '':
                raise ProgramError(self.node_name, f'{purpose.capitalize()} node {self.node_name_str!r} does not have a default {port_direction}')
            else:
                raise ProgramError(self.port_name_elem, f'Unable to find {purpose} {port_direction} port {self.node_name_str}.{self.port_name_str}')


def static_source(parameter: 'model.NodeParameter', name: Identifier, port_name: Identifier | None,
                  direction: PortDirection) -> 'model.StaticSource':
    """Return the connection source for a parameter or constant."""
    kind = 'Constant' if parameter.is_constant else 'Parameter'
    if direction == PortDirection.Input:
        raise ProgramError(name, f'Cannot connect to {kind.lower()} {name.id_str!r}')
    if port_name is not None:
        raise ProgramError(port_name, f'{kind} {name.id_str!r} has no output port {port_name.id_str!r}')
    parameter.resolve_type()
    if parameter.type_resolved == model.types.ValueType:
        raise ProgramError(name, f'Type-valued {kind.lower()} {name.id_str!r} cannot be a connection source')
    return model.StaticSource(parameter, name)


@syntax
class StructLiteralField(SyntaxElement):
    name: Identifier
    value: Value


@syntax
class StructLiteral(Value):
    struct_type: Reference
    fields: tuple[StructLiteralField, ...]


def check_struct_literal(value_type: 'model.types.Type', literal: 'StructLiteral',
                         scope: 'model.Scope | None') -> dict[str, 'StructLiteralField']:
    if not isinstance(value_type, model.types.TypeStruct):
        raise ProgramError(literal, f'Expected {value_type}, got struct literal')
    if scope is None or scope.resolve_type(literal.struct_type) is not value_type:
        raise ProgramError(literal.struct_type, f'Expected struct type {value_type}, got {literal.struct_type}')
    fields: dict[str, StructLiteralField] = {}
    for field in literal.fields:
        name = field.name.id_str
        if name in fields:
            raise ProgramError(field.name, f'Duplicate struct field {name}', hints=[
                CompilerHint(fields[name].name, 'Previous field was here'),
            ])
        if name not in value_type.fields:
            raise ProgramError(field.name, f'Unknown field {name!r} of {value_type}')
        fields[name] = field
    missing = value_type.fields.keys() - fields.keys()
    if missing:
        raise ProgramError(literal, f'Missing fields of {value_type}: {", ".join(sorted(missing))}')
    return fields
