import enum
import typing
import abc
import contextlib
import re
import datetime
import os

import lark

from ..program import RunStage
from .attributes import copy_attributes

from wirely.compiler import model
from wirely.util import check_parameters
from wirely.compiler.types import parse_value, resolve_type, check_runtime_type, type_parameters


_UNSET_SOURCE_PATH = object()


def capture_source_path(filename):
    if not filename or (str(filename).startswith('<') and str(filename).endswith('>')):
        return None
    return os.path.abspath(filename)


class SourcePosition:
    @check_parameters
    def __init__(self, *, filename, meta, input_data, source_path=_UNSET_SOURCE_PATH):
        self.filename = filename
        self.meta = meta
        self.input_data = input_data
        self.source_path = (capture_source_path(filename)
                            if source_path is _UNSET_SOURCE_PATH else source_path)

    @property
    def start_pos(self):
        # TODO: Handle source elements which were created without any token where e.g. start_pos is not set?
        return self.meta.start_pos

    @property
    def start_line(self):
        return self.meta.line


class SourceElement:
    @check_parameters
    def __init__(self, *, source_position: SourcePosition):
        self.source_position = source_position
        self.outer_attributes = ()
        self.inner_attributes = ()
        self.attributes = ()

    @property
    @contextlib.contextmanager
    def context(self):
        exc = None
        try:
            yield None
        except Exception as e:
            exc = e
            if isinstance(exc, ProgramError):
                raise
        if exc is not None:
            raise InternalCompilerError(self, exc)

    def __repr__(self):
        # The source text reads naturally in diagnostics; subclasses may refine it.
        position = getattr(self, 'source_position', None)
        meta = getattr(position, 'meta', None)
        if meta is not None and not getattr(meta, 'empty', True) and position.input_data:
            return position.input_data[meta.start_pos:meta.end_pos]
        return f'{type(self).__name__}()'


class CompilerHint:
    @check_parameters
    def __init__(self, source_element: SourceElement, msg):
        self.source_element = source_element
        self.msg = msg
        self.fake_ui = lark.exceptions.UnexpectedInput()
        self.fake_ui.pos_in_stream = self.source_element.source_position.start_pos

    def __str__(self):
        return self.msg

    def get_context(self):
        return self.fake_ui.get_context(self.source_element.source_position.input_data)


class ProgramError(Exception):
    @check_parameters
    def __init__(self, source_element: typing.Optional[SourceElement], msg, inner_exception=None, *, hints: typing.List[CompilerHint] = []):
        # raise Exception()
        self.source_element = source_element
        self.msg = msg
        self.inner_exception = inner_exception
        self.hints = hints
        if self.source_element is not None:
            self.fake_ui = lark.exceptions.UnexpectedInput()
            self.fake_ui.pos_in_stream = self.source_element.source_position.start_pos

        if self.source_element is None and not isinstance(self, InternalCompilerError):
            raise Exception('self.source_element is None and not isinstance(self, InternalCompilerError)')

    def __str__(self):
        return self.msg

    def get_context(self):
        return self.fake_ui.get_context(self.source_element.source_position.input_data)


class InternalCompilerError(ProgramError):
    @check_parameters
    def __init__(self, source_element: typing.Optional[SourceElement], inner_exception, *, msg=None):
        if msg is None:
            msg = 'Internal Compiler Error: ' + str(inner_exception)
        super().__init__(source_element, msg, inner_exception)


class Identifier(SourceElement):
    @check_parameters
    def __init__(self, s: str, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        if s.startswith('r#'):
            s = s[2:]
        self.id_str = s

    def __repr__(self):
        # return f'{self.id_str} [{self.source_position.start_line}]'
        return self.id_str


class PortDirection(enum.Enum):
    Input = enum.auto()
    Output = enum.auto()


class PortDirectionSpecifier(SourceElement):
    @check_parameters
    def __init__(self, direction: PortDirection, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.direction = direction

    @staticmethod
    @check_parameters
    def create_input(*, source_position: SourcePosition):
        return PortDirectionSpecifier(PortDirection.Input, source_position=source_position)

    @staticmethod
    @check_parameters
    def create_output(*, source_position: SourcePosition):
        return PortDirectionSpecifier(PortDirection.Output, source_position=source_position)


class ListSpecifier(SourceElement):
    @check_parameters
    def __init__(self, is_list: bool, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.is_list = is_list

    @staticmethod
    @check_parameters
    def create_list(*, source_position: SourcePosition):
        return ListSpecifier(True, source_position=source_position)

    @staticmethod
    @check_parameters
    def create_nolist(*, source_position: SourcePosition):
        return ListSpecifier(False, source_position=source_position)


# TODO: Rename Value to Level or State or keep Value?
# TODO: Rename Signal to Impulse or Trigger or Event?
class ConnectionKind(enum.Enum):
    Value = enum.auto()
    Event = enum.auto()


class ConnectionKindSpecifier(SourceElement):
    @check_parameters
    def __init__(self, kind: ConnectionKind, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.kind = kind

    @staticmethod
    @check_parameters
    def create_value(*, source_position: SourcePosition):
        return ConnectionKindSpecifier(ConnectionKind.Value, source_position=source_position)

    @staticmethod
    @check_parameters
    def create_event(*, source_position: SourcePosition):
        return ConnectionKindSpecifier(ConnectionKind.Event, source_position=source_position)


class Value(SourceElement):
    @check_parameters
    def __init__(self, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)


class PortTypeSpecification(SourceElement):
    @check_parameters
    def __init__(self, list_spec: ListSpecifier, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.list_spec = list_spec


class PortTypeSpecificationData(PortTypeSpecification):
    @check_parameters
    def __init__(self, list_spec: ListSpecifier, kind_spec: ConnectionKindSpecifier, ty: Value, *, source_position: SourcePosition):
        super().__init__(list_spec=list_spec, source_position=source_position)
        self.kind_spec = kind_spec
        self.ty = ty


class PortTypeSpecificationResource(PortTypeSpecification):
    @check_parameters
    def __init__(self, list_spec: ListSpecifier, ty: Value, *, source_position: SourcePosition):
        super().__init__(list_spec=list_spec, source_position=source_position)
        self.ty = ty


class NodeTypeSpecifier(SourceElement):
    @check_parameters
    def __init__(self, specifier_str, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)

        self.specifier_str = specifier_str

    @staticmethod
    def create_singleton(*, source_position: SourcePosition):
        return NodeTypeSpecifier(specifier_str='singleton', source_position=source_position)


class NativeSpecifier(NodeTypeSpecifier):
    @check_parameters
    def __init__(self, *, source_position: SourcePosition):
        super().__init__(specifier_str='native', source_position=source_position)


class OptionalSpecifier(NodeTypeSpecifier):
    @check_parameters
    def __init__(self, *, source_position: SourcePosition):
        super().__init__(specifier_str='optional', source_position=source_position)


class UnitLiteral(Value):
    def __repr__(self):
        return 'UnitLiteral()'


class IntNumber(Value):
    @check_parameters
    def __init__(self, token: lark.Token, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.token = token
        text = str(token)
        if text.lower().startswith('0x'):
            base = 16
        elif text.lower().startswith('0o'):
            base = 8
        else:
            base = 10
        try:
            self.value = int(text, base)
        except ValueError:
            raise ProgramError(self, f'Invalid integer literal: {text!r}') from None

    @classmethod
    def positive(cls, token, *, source_position):
        return cls(token, source_position=source_position)

    def negated(self, *, source_position):
        result = IntNumber(self.token, source_position=source_position)
        result.value = -self.value
        return result


class FloatNumber(Value):
    @check_parameters
    def __init__(self, token: lark.Token, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.token = token
        # TODO: Does this parse the value correctly?
        self.value = float(token)


class StringConstant(Value):
    @check_parameters
    def __init__(self, token: lark.Token, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.token = token

        val = token.value.replace('\r\n', '\n')
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
            self.value = value
        elif val.startswith('r#"'):
            if len(val) < 5 or val[-2:] != '"#':
                raise ProgramError(self, 'Invalid raw string literal')
            value = val[3:-2]
            if '"#' in value:
                raise ProgramError(self, 'Invalid raw string literal')
            self.value = value
        else:
            if len(val) < 2 or val[0] != '"' or val[-1] != '"':
                raise ProgramError(self, 'Invalid string literal')
            self.value = self._decode_quoted(val[1:-1])

    def _decode_quoted(self, value):
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


class Reference(Value):
    @check_parameters
    def __init__(self, identifiers: typing.List[Identifier], *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.identifiers = identifiers

    @staticmethod
    @check_parameters
    def from_list(*identifiers, source_position: SourcePosition):
        return Reference(list(identifiers), source_position=source_position)

    @staticmethod
    @check_parameters
    def create_default(direction_spec: PortDirectionSpecifier, *, source_position: SourcePosition):
        direction = direction_spec.direction
        # TODO: Clean up
        if direction == PortDirection.Input:
            name = 'default input'
        elif direction == PortDirection.Output:
            name = 'default output'
        else:
            raise Exception(f'Unknown value for direction: {direction}')
        identifier = Identifier(name, source_position=source_position)
        return Reference([identifier], source_position=source_position)

    def __repr__(self):
        # TODO: Somehow show which enclosing node a parameter belongs to?
        # return '.'.join([i.id_str for i in self.identifiers]) + f' [{self.source_position.start_line}]'
        return '.'.join([i.id_str for i in self.identifiers])

    @property
    def length(self):
        return len(self.identifiers)

    @check_parameters
    def resolve(self, graph, direction: PortDirection, purpose: str):
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


class ListLiteral(Value):
    @check_parameters
    def __init__(self, values: list[Value], *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.values = values


class FunctionCall(Value):
    @check_parameters
    def __init__(self, fun: Value, parameters: list[Value], *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.fun = fun
        self.parameters = parameters

    @staticmethod
    def create(fun, parameters, *, source_position):
        if (isinstance(fun, Reference) and fun.length == 1
                and fun.identifiers[0].id_str == 'parameter_option'):
            return ParameterOption(parameters, source_position=source_position)
        return FunctionCall(fun, parameters, source_position=source_position)


class ParameterOption(Value):
    def __init__(self, parameters, *, source_position):
        super().__init__(source_position=source_position)
        self.parameters = parameters

    def declaration(self, scope, local_names=()):
        if len(self.parameters) != 1:
            raise ProgramError(self, 'parameter_option expects exactly one argument')
        reference = self.parameters[0]
        if not isinstance(reference, Reference) or reference.length != 1:
            raise ProgramError(reference, 'parameter_option requires an unqualified optional parameter reference')
        name = reference.identifiers[0].id_str
        parameter = scope.parsed_parameters.get(name)
        if name in local_names or parameter is None or parameter.optional_specifier is None:
            raise ProgramError(reference, 'parameter_option requires an optional parameter without a default')
        return parameter


class BracketOperator(Value):
    @check_parameters
    def __init__(self, base: Value, parameter: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.base = base
        self.parameter = parameter


# TODO: Remove and merge into Reference?
class ResolvedPortReference(SourceElement):
    @check_parameters
    # TODO: port_name should not be typing.Optional
    def __init__(self, graph, direction: PortDirection, node_name: Identifier, port_name: typing.Optional[Identifier], purpose: str):
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
        # TODO: Should this be here?
        if direction == PortDirection.Input:
            if self.node_name_str in graph.output_ports and self.port_name_str == '':
                self.port = graph.output_ports[self.node_name_str]
                # TODO: This is a hack
                return
            variable = graph.value_nodes.get(self.node_name_str)
            if variable is not None:
                is_state = isinstance(variable, DeclarationState)
                if self.port_name is not None:
                    kind = 'State' if is_state else 'Temporary'
                    raise ProgramError(self.port_name, f'{kind} {self.node_name_str!r} has no input port {self.port_name_str!r}')
                variables = graph.state_nodes if is_state else graph.temporary_nodes
                self.port = variables[self.node_name_str]
                return
        if direction == PortDirection.Output:
            variable = graph.value_nodes.get(self.node_name_str)
            if isinstance(variable, DeclarationState):
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
                    raise ProgramError(self.port_name_elem, f'Temporary {self.node_name_str!r} has no output port {self.port_name_str!r}')
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


class Declaration(SourceElement, abc.ABC):
    @check_parameters
    def __init__(self, source_position: SourcePosition):
        super().__init__(source_position=source_position)

    # TODO: Move implementation here?
    @abc.abstractmethod
    def compile_pass1_btd(self, interface):
        ...

    @abc.abstractmethod
    def create_io(self, new_graph):
        ...

    @abc.abstractmethod
    def compile_pass1(self, graph):
        ...

    @abc.abstractmethod
    def compile_pass1_5(self, graph):
        ...

    @abc.abstractmethod
    def compile_pass2(self, graph):
        ...


# This class contains methods for resolving references in a scope
class Scope(Declaration):
    @check_parameters
    def __init__(self, source_position: SourcePosition):
        super().__init__(source_position=source_position)

        # TODO: Remove one of node_type_interfaces / node_type_implementations
        self.node_type_interfaces = {}
        self.node_type_implementations = {}
        self.resource_types = {}
        self.enum_types = {}
        self.struct_types = {}
        self.connections = []
        self.native_functions = {}

    # TODO: Move to Declaration?
    @property
    @abc.abstractmethod
    def fullname(self):
        ...

    @property
    def static_declarations(self):
        return {**getattr(self, 'parsed_parameters', {}),
                **getattr(self, 'parsed_constants', {})}

    # TODO: Handle parameters for parent types properly? Does this even make sense?
    def resolve_type(self, ty, par_values={}):
        with ty.context:
            # TODO: Clean up
            if isinstance(ty, BracketOperator):
                if not isinstance(ty.base, Reference):
                    raise model.ProgramError(ty, f'Expected Reference as generic type base, got {ty}')
                if ty.base.length == 1 and ty.base.identifiers[0].id_str == 'Option':
                    gen_type = model.types.TypeOption
                elif ty.base.length == 1 and ty.base.identifiers[0].id_str == 'List':
                    gen_type = model.types.TypeList
                else:
                    raise model.ProgramError(ty, f'Cannot find generic type {ty.base}')
                # if len(ty.parameters) != 1:
                #     raise model.ProgramError(ty, f'Expected 1 type parameter, got {len(ty.parameters)}: {ty}')
                par_type = self.resolve_type(ty.parameter, par_values=par_values)
                return gen_type(par_type)
            elif not isinstance(ty, Reference):
                raise model.ProgramError(ty, f'Expected Reference or BracketOperator as type name, got {ty}')

            # TODO: Move somewhere else, clean up
            if ty.length == 1:
                name = ty.identifiers[0].id_str
                # TODO: Avoid hasattr
                if name in self.static_declarations:
                    par = self.static_declarations[name]
                    # A parameter whose type refers to itself is not a type.
                    if not getattr(par, 'resolving_type', False):
                        par.resolve_type()
                    declared_type = getattr(par, 'type_resolved', None)
                    is_type = (isinstance(par.ty, Reference) and par.ty.length == 1
                               and par.ty.identifiers[0].id_str == 'ValueType')
                    if (is_type or declared_type == model.types.ValueType
                            or (declared_type is not None and declared_type.contains_parameters)):
                        if par not in par_values:
                            return model.TypeParameter(par, ast_element=ty)
                        result = par_values[par]
                        if isinstance(result, model.Value):
                            return model.TypeParameter(par, ast_element=ty)
                        if not isinstance(result, model.Type):
                            raise ProgramError(ty, f'Expected ValueType for {name!r}')
                        return result
                if name in model.types.type_by_name:
                    return model.types.type_by_name[name]
            scope = self
            while scope is not None:
                candidate = scope
                for identifier in ty.identifiers[:-1]:
                    candidate = candidate.libraries.get(identifier.id_str)
                    if candidate is None:
                        break
                if candidate is not None:
                    struct = candidate.struct_types.get(ty.identifiers[-1].id_str)
                    if struct is not None:
                        return struct.resolve_fields()
                    enum_type = candidate.enum_types.get(ty.identifiers[-1].id_str)
                    if enum_type is not None:
                        return enum_type
                scope = scope.parent_graph
            raise model.ProgramError(ty, f'Cannot find type {ty}')

    def resolve_function(self, reference, *, required=True):
        scope = self
        names = tuple(identifier.id_str for identifier in reference.identifiers)
        while scope is not None:
            candidate = scope
            for name in names[:-1]:
                candidate = candidate.libraries.get(name)
                if candidate is None:
                    break
            if candidate is not None and names[-1] in candidate.native_functions:
                return candidate.native_functions[names[-1]].resolve_signature()
            scope = scope.parent_graph
        if required:
            raise ProgramError(reference, f'Cannot find function {".".join(names)!r}')
        return None

    def resolve_resource_type(self, name):
        scope = self
        while scope is not None:
            candidate = scope
            for identifier in name.identifiers[:-1]:
                candidate = candidate.libraries.get(identifier.id_str)
                if candidate is None:
                    break
            if candidate is not None:
                resource_type = candidate.resource_types.get(name.identifiers[-1].id_str)
                if resource_type is not None:
                    return resource_type
            scope = scope.parent_graph
        raise model.ProgramError(name, f'Cannot find resource type {name}')

    # TODO: Should this be named get_const_value()? (Also returns e.g. ValueFunctionCall)
    def get_const_value(self, value, value_type, par_values={}):
        with value.context:
            from .expressions import (
                Expression, PropertyAccess, compile_constant, property_reference, type_member,
            )
            value = property_reference(self, value)
            if (isinstance(value, FunctionCall) and not value_type.contains_parameters
                    and type_member(self, value.fun, par_values) is not None):
                return compile_constant(self, value, value_type, par_values)
            if (isinstance(value, FunctionCall) and not value_type.contains_parameters
                    and isinstance(property_reference(self, value.fun), PropertyAccess)):
                return compile_constant(self, value, value_type, par_values)
            if isinstance(value, ParameterOption):
                parameter = value.declaration(self)
                result = model.value.ParameterOptionValue(parameter, ast_element=value)
                model.value.check_value_type(result.value_type, value_type, value)
                return result
            if value_type.contains_parameters:
                if (isinstance(value, Reference) and value.length == 1
                        and value.identifiers[0].id_str not in self.static_declarations
                        and value.identifiers[0].id_str not in ('true', 'false', 'None')):
                    try:
                        self.resolve_type(value, par_values)
                    except ProgramError:
                        raise ProgramError(value, f'Unknown static value {value}') from None
                return model.value.DeferredValue(self, value, value_type)
            if isinstance(value, Expression):
                return compile_constant(self, value, value_type, par_values)
            if value_type == model.types.ValueType and not isinstance(value, model.FunctionCall):
                return model.ConstantValue(value_type, self.resolve_type(value, par_values=par_values),
                                            ast_element=value)
            if isinstance(value, StructLiteral):
                fields = check_struct_literal(value_type, value, self)
                return model.value.ValueStruct(
                    value_type,
                    {name: self.get_const_value(field.value, value_type.fields[name], par_values=par_values)
                     for name, field in fields.items()}, ast_element=value,
                )
            if isinstance(value, model.ListLiteral) and isinstance(value_type, model.types.TypeList):
                elements = [self.get_const_value(element, value_type.inner_type, par_values=par_values)
                            for element in value.values]
                return model.value.ValueList(value_type, elements, ast_element=value)
            if isinstance(value, model.FunctionCall):
                if not isinstance(value.fun, model.Reference):
                    raise model.ProgramError(value.fun, 'Function is not a reference')
                function_name = '.'.join(identifier.id_str for identifier in value.fun.identifiers)
                if function_name == 'Some':
                    if len(value.parameters) != 1:
                        raise model.ProgramError(value, 'Some expects exactly one argument')
                    if not isinstance(value_type, model.types.TypeOption):
                        raise model.ProgramError(value, 'Some requires an Option type')
                    inner = self.get_const_value(value.parameters[0], value_type.inner_type, par_values=par_values)
                    return model.value.ValueOptionSome(value_type, inner, ast_element=value)
                function = self.resolve_function(value.fun)
                model.value.check_value_type(function.return_type, value_type, value)
                if len(value.parameters) != len(function.parameter_types):
                    raise model.ProgramError(value, f'Function {function_name!r} expected {len(function.parameter_types)} parameters, but got {len(value.parameters)}')
                parameters = [self.get_const_value(parameter, function.parameter_types[i], par_values=par_values) for i, parameter in enumerate(value.parameters)]
                return model.value.ValueFunctionCall(function=function, parameters=parameters, ast_element=value)

            # TODO: Clean up
            found = True
            while found:
                found = False
                if isinstance(value, model.Reference) and value.length == 1:
                    name = value.identifiers[0].id_str
                    if name in self.static_declarations:
                        par = self.static_declarations[name]
                        if not(isinstance(par.ty, model.Reference) and par.ty.length == 1
                               and par.ty.identifiers[0].id_str == 'ValueType'):
                            parameter_type = self.resolve_type(par.ty, par_values=par_values)
                            model.value.check_value_type(parameter_type, value_type, value)
                            # value = par_values[name]
                            # found = True
                            # print(f'{par.name} in {par_values}')
                            # TODO: Clean up / Check whether this really is a parameter
                            if par not in par_values:
                                return model.ValueParameter(value_type, par, ast_element=value)
                            return model.ConstantValue(value_type, value_type.verify_value(par_values[par].evaluate()), ast_element=value)
            return model.ConstantValue(value_type, parse_value(value_type, value, scope=self), ast_element=value)
            # raise model.ProgramError(value, f'Cannot turn {value!r} into constant value of type {value_type}')

    def resolve_node_type(self, name, par_values={}):
        # print(self, 'resolve_node_type', name)

        if name.length >= 2:
            library = self.libraries.get(name.identifiers[0].id_str)
            for identifier in name.identifiers[1:-1]:
                if library is None:
                    break
                library = library.libraries.get(identifier.id_str)
            if library is not None:
                node_name = name.identifiers[-1].id_str
                if node_name in library.node_type_implementations:
                    return library.node_type_implementations[node_name]

        if name.length == 1:
            node_type_name_id = name.identifiers[0]

            if node_type_name_id.id_str in self.node_type_implementations:
                return self.node_type_implementations[node_type_name_id.id_str]

        if self.parent_graph is None:
            raise model.ProgramError(name, f'Cannot find node type {name}')
        return self.parent_graph.resolve_node_type(name, par_values=par_values)


class EnumMember(SourceElement):
    @check_parameters
    def __init__(self, name: Identifier, value: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.value = value


class StructField(SourceElement):
    @check_parameters
    def __init__(self, name: Identifier, field_type: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.field_type = field_type


class StructLiteralField(SourceElement):
    @check_parameters
    def __init__(self, name: Identifier, value: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.value = value


class StructLiteral(Value):
    @check_parameters
    def __init__(self, struct_type: Reference, fields: typing.List[StructLiteralField],
                 *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.struct_type = struct_type
        self.fields = fields


def check_struct_literal(value_type, literal, scope):
    if not isinstance(value_type, model.types.TypeStruct):
        raise ProgramError(literal, f'Expected {value_type}, got struct literal')
    if scope is None or scope.resolve_type(literal.struct_type) is not value_type:
        raise ProgramError(literal.struct_type, f'Expected struct type {value_type}, got {literal.struct_type}')
    fields = {}
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


class DeclarationStructType(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, fields: list,
                 *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.fields = fields
        self.inner_attributes = getattr(fields, 'inner_attributes', ())
        self.value_type = None
        self.resolving = False

    def compile_pass1_btd(self, interface):
        if interface.name is not None:
            raise ProgramError(self, 'Struct type declaration not allowed inside node type')

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in model.types.type_by_name or name in ('Option', 'List'):
            raise ProgramError(self.name, f'Struct type {name} conflicts with built-in type')
        for kind, declarations in (
                ('struct type', graph.struct_types), ('enum type', graph.enum_types),
                ('node type', graph.node_type_interfaces), ('resource type', graph.resource_types),
                ('namespace', graph.libraries)):
            if name in declarations:
                previous = declarations[name]
                if kind in ('enum type', 'resource type'):
                    previous = previous.declaration
                raise ProgramError(self.name, f'Struct type {name} conflicts with {kind}', hints=[
                    CompilerHint(previous, 'Previous declaration was here'),
                ])
        self.scope = graph
        graph.struct_types[name] = self

    def resolve_fields(self):
        if self.value_type is not None:
            return self.value_type
        if self.resolving:
            raise ProgramError(self.name, f'Recursive struct type {self.name.id_str}')
        self.resolving = True
        try:
            fields = {}
            previous = {}
            for field in self.fields:
                name = field.name.id_str
                if name in fields:
                    raise ProgramError(field.name, f'Duplicate struct field {name}', hints=[
                        CompilerHint(previous[name].name, 'Previous field was here'),
                    ])
                fields[name] = self.scope.resolve_type(field.field_type)
                previous[name] = field
            fullname = self.scope.fullname + '.' + self.name.id_str if isinstance(self.scope, DeclarationLibrary) else self.name.id_str
            self.value_type = model.types.TypeStruct(fullname, fields)
            return self.value_type
        finally:
            self.resolving = False

    def create_io(self, new_graph):
        pass

    def compile_pass1_5(self, graph):
        self.resolve_fields()

    def compile_pass2(self, graph):
        pass


class DeclarationEnumType(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, underlying_type: Value,
                 members: list, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.underlying_type = underlying_type
        self.members = members
        self.inner_attributes = getattr(members, 'inner_attributes', ())

    def compile_pass1_btd(self, interface):
        if interface.name is not None:
            raise ProgramError(self, 'Enum type declaration not allowed inside node type')

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in model.types.type_by_name or name in ('Option', 'List'):
            raise ProgramError(self.name, f'Enum type {name} conflicts with built-in type')
        for kind, declarations in (
                ('enum type', graph.enum_types),
                ('struct type', graph.struct_types),
                ('node type', graph.node_type_interfaces),
                ('resource type', graph.resource_types),
                ('namespace', graph.libraries)):
            if name in declarations:
                previous = declarations[name]
                if kind in ('enum type', 'resource type'):
                    previous = previous.declaration
                raise ProgramError(self.name, f'Enum type {name} conflicts with {kind}', hints=[
                    CompilerHint(previous, 'Previous declaration was here'),
                ])
        underlying = self.underlying_type
        if not isinstance(underlying, Reference) or underlying.length != 1:
            raise ProgramError(underlying, 'Enum underlying type must be a supported integer type')
        underlying = model.types.type_by_name.get(underlying.identifiers[0].id_str)
        if not isinstance(underlying, model.types.TypeInt):
            raise ProgramError(self.underlying_type, 'Enum underlying type must be a supported integer type')
        if not self.members:
            raise ProgramError(self.name, 'Enum must declare at least one member')
        names = {}
        values = {}
        numbers = {}
        for member in self.members:
            member_name = member.name.id_str
            if member_name in names:
                raise ProgramError(member.name, f'Duplicate enum member {member_name}', hints=[
                    CompilerHint(names[member_name], 'Previous member was here'),
                ])
            if not isinstance(member.value, IntNumber):
                raise ProgramError(member.value, 'Enum member values must be integer literals')
            number = parse_value(underlying, member.value)
            if number in values:
                raise ProgramError(member.value, f'Duplicate enum value {number}', hints=[
                    CompilerHint(values[number], 'Previous member was here'),
                ])
            names[member_name] = member
            values[number] = member
            numbers[member_name] = number
        fullname = graph.fullname + '.' + name if isinstance(graph, DeclarationLibrary) else name
        try:
            python_type = enum.Enum(name, numbers)
        except (TypeError, ValueError) as error:
            raise ProgramError(self, str(error)) from None
        for member_name, member in names.items():
            if member_name not in python_type.__members__:
                raise ProgramError(member.name, f'Unsupported enum member name {member_name}')
        self.value_type = model.types.TypeEnum(fullname, python_type, underlying_type=underlying)
        self.value_type.declaration = self
        graph.enum_types[name] = self.value_type

    def create_io(self, new_graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass


class DeclarationResourceType(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name

    def compile_pass1_btd(self, interface):
        if interface.name is not None:
            raise ProgramError(self, 'Resource type declaration not allowed inside node type')

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.struct_types:
            raise model.ProgramError(self.name, f'Resource type {name} conflicts with struct type', hints=[
                model.CompilerHint(graph.struct_types[name], 'Previous declaration was here'),
            ])
        if name in graph.enum_types:
            raise ProgramError(self.name, f'Resource type {name} conflicts with enum type', hints=[
                CompilerHint(graph.enum_types[name].declaration, 'Previous declaration was here'),
            ])
        if name in graph.resource_types:
            previous = graph.resource_types[name].declaration
            raise ProgramError(self, f'Duplicate resource type {name}', hints=[
                CompilerHint(previous, 'Previous resource type was here'),
            ])
        if name in graph.node_type_interfaces:
            raise ProgramError(self, f'Resource type {name} conflicts with node type', hints=[
                CompilerHint(graph.node_type_interfaces[name], 'Previous node type was here'),
            ])
        if isinstance(graph, DeclarationLibrary):
            fullname = graph.fullname + '.' + name
        else:
            fullname = name
        graph.resource_types[name] = model.ResourceType(fullname, self)

    def create_io(self, new_graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass


class DeclarationPort(Declaration):
    @check_parameters
    def __init__(self, name: typing.Optional[Identifier], direction_spec: PortDirectionSpecifier, port_spec: PortTypeSpecification, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.direction_spec = direction_spec
        self.port_spec = port_spec

        # TODO: Remove?
        self.list_spec = self.port_spec.list_spec
        if isinstance(self.port_spec, PortTypeSpecificationData):
            self.ty = self.port_spec.ty
            self.kind_spec = self.port_spec.kind_spec

            self.kind = self.kind_spec.kind

        self.direction = self.direction_spec.direction

        if self.list_spec.is_list and self.direction != PortDirection.Input:
            raise ProgramError(self, f'Got multi specifier for non-input')

        if self.name is None:
            if self.direction == PortDirection.Input:
                self.name_str = 'default input'
            elif self.direction == PortDirection.Output:
                self.name_str = 'default output'
            else:
                raise Exception(f'Unknown value for direction: {self.direction}')
        else:
            self.name_str = self.name.id_str

    @staticmethod
    @check_parameters
    def create(direction_spec: PortDirectionSpecifier, name: Identifier, port_spec : PortTypeSpecification, *, source_position: SourcePosition):
        return DeclarationPort(name, port_spec=port_spec, direction_spec=direction_spec, source_position=source_position)

    @staticmethod
    @check_parameters
    def create_default(direction_spec: PortDirectionSpecifier, port_spec : PortTypeSpecification, *, source_position: SourcePosition):
        return DeclarationPort(None, port_spec=port_spec, direction_spec=direction_spec, source_position=source_position)

    @check_parameters
    def compile_pass1_btd(self, interface):
        self.interface = interface

        if self.interface.is_singleton and self.direction_spec.direction == PortDirection.Input:
            raise model.ProgramError(self, 'Singleton node type cannot have inputs')

    def create_io(self, new_graph):
        # TODO: Use self.kind ConnectionKind
        # TODO: Pass par_values to resolve_type?
        # TODO: Should the entires port_spec instance be passed?
        if isinstance(self.port_spec, PortTypeSpecificationData):
            ty = self.interface.resolve_type(self.ty)
            check_runtime_type(ty, self)
            resource_type = None
        elif isinstance(self.port_spec, PortTypeSpecificationResource):
            ty = None
            resource_type = self.interface.resolve_resource_type(self.port_spec.ty)
        else:
            raise InternalCompilerError(self, Exception('Unknown port type specification'))
        if self.direction == PortDirection.Input:
            name = self.name_str
            if name == '':
                name = 'default input'
            if name in new_graph.input_ports:
                raise ProgramError(self, f'Duplicate input {name!r}', hints=[
                    CompilerHint(new_graph.input_ports[name].port_spec, 'Previous input was here'),
                ])
            port = model.TypeInputPort(new_graph, ty, name=name, input_name=self.name_str, is_list=self.list_spec.is_list, port_spec=self.port_spec, resource_type=resource_type)
        elif self.direction == PortDirection.Output:
            name = self.name_str
            if name == '':
                name = 'default output'
            if name in new_graph.output_ports:
                raise ProgramError(self, f'Duplicate output {name!r}', hints=[
                    CompilerHint(new_graph.output_ports[name].port_spec, 'Previous output was here'),
                ])
            port = model.TypeOutputPort(new_graph, ty, name=name, output_name=self.name_str, port_spec=self.port_spec, resource_type=resource_type)
        else:
            raise Exception(f'Unknown direction: {self.direction!r}')

        copy_attributes(port, self)

    # TODO: Clean up compilation passes

    def compile_pass1(self, graph):
        if self.name is None:
            return
        previous = graph.name_declarations.get(self.name.id_str)
        # create_io reports duplicate ports of the same kind.
        if not (isinstance(previous, DeclarationPort) and previous.direction == self.direction):
            register_name(graph, self.name, self)

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass


class DeclarationParameter(Declaration):
    @check_parameters
    def __init__(self, optional_specifier: typing.Optional[OptionalSpecifier], name: Identifier, ty, *, default_value=None, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.ty = ty
        self.optional_specifier = optional_specifier
        self.default_value = default_value

        if self.optional_specifier is not None and self.default_value is not None:
            raise Exception('self.optional_specifier is not None and self.default_value is not None')

        self.is_optional = self.optional_specifier is not None or self.default_value is not None

    @staticmethod
    def with_default_value(name: Identifier, ty, default_value, *, source_position: SourcePosition):
        return DeclarationParameter(optional_specifier=None, name=name, ty=ty, default_value=default_value, source_position=source_position)

    @check_parameters
    def compile_pass1_btd(self, interface):
        self.interface = interface

    def create_io(self, new_graph):
        pass

    def resolve_type(self):
        # Types may refer to parameters declared later, which resolve on demand.
        if hasattr(self, 'type_resolved'):
            return
        self.resolving_type = True
        try:
            # TODO: Is this correct?
            self.type_resolved = self.interface.resolve_type(self.ty)
        finally:
            self.resolving_type = False

    def __repr__(self):
        # return f'Parameter <{self.name}> [{self.name.source_position.start_line}] : <{self.ty}>'
        if not hasattr(self, 'interface'):
            # return f'Parameter <(unknown node).{self.name}> : <{self.ty}>'
            return f'(unknown node).{self.name}'
        else:
            return f'{self.interface.name}.{self.name}'

    # TODO: Clean up compilation passes

    def compile_pass1(self, graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        if self.default_value is not None:
            from .expressions import compile_constant
            compile_constant(graph, self.default_value, self.type_resolved, {})


class DeclarationConstant(DeclarationParameter):
    def __init__(self, name, ty, initializer, *, source_position):
        super().__init__(None, name, ty, default_value=initializer,
                         source_position=source_position)

    def compile_pass2(self, graph):
        # Check concrete types and expression syntax without running initializers.
        if self.type_resolved == model.types.ValueType:
            graph.get_const_value(self.default_value, self.type_resolved)
        else:
            from .expressions import compile_constant
            compile_constant(graph, self.default_value, self.type_resolved, {})


class DeclarationAssertion(Declaration):
    def __init__(self, condition, message, *, source_position):
        super().__init__(source_position=source_position)
        self.condition = condition
        self.message = message

    def compile_pass1_btd(self, interface):
        interface.assertions.append(self)

    def create_io(self, new_graph):
        pass

    def compile_pass1(self, graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        from .expressions import compile_constant
        compile_constant(graph, self.condition, model.types.boolean, {})

    def evaluate(self, instance):
        from .expressions import compile_constant
        with self.context:
            try:
                condition = compile_constant(instance.node_type, self.condition,
                                             model.types.boolean, instance.static_values)
                try:
                    result = condition.resolve_self(instance.static_values).evaluate()
                except (ValueError, ArithmeticError) as error:
                    raise ProgramError(self.condition, str(error)) from error
                if not result:
                    message = 'Compile-time assertion failed'
                    if self.message is not None:
                        message += ': ' + self.message.value
                    raise ProgramError(self, message)
            except ProgramError as error:
                owner = instance.node or instance.node_type.singleton_instance
                if not owner.is_root:
                    name = model.InstanceNode.path_name(instance.path, qualified=True)
                    error.hints = [*error.hints, CompilerHint(
                        owner.instantiation, f'While instantiating {name!r}',
                    )]
                raise


class ParameterBinding(SourceElement):
    @check_parameters
    def __init__(self, name: Identifier, value: Value, *, source_position: SourcePosition, is_optional=False):
        super().__init__(source_position=source_position)
        self.name = name
        self.value = value
        self.is_optional = is_optional

    @staticmethod
    def optional(name, value, *, source_position):
        return ParameterBinding(name, value, source_position=source_position, is_optional=True)

    def __repr__(self):
        return f'Parameter <{self.name}> = <{self.value}>'
        # return f'Parameter <{self.name}> [{self.name.source_position.start_line}] = <{self.value}>'


class Instantiation(SourceElement):
    @check_parameters
    def __init__(self, type_name: Reference, parameters: list,
                 *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.type_name = type_name
        self.parameters = parameters
        self.inner_attributes = getattr(parameters, 'inner_attributes', ())


class DeclarationInstance(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, instantiation: Instantiation, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.instantiation = instantiation
        self.inner_attributes = instantiation.inner_attributes

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.node_declarations:
            raise ProgramError(self.name, f'Duplicate node name {name!r}', hints=[
                CompilerHint(graph.node_declarations[name], 'Previous node was here'),
            ])
        register_name(graph, self.name, self)
        graph.node_declarations[name] = self

    def compile_pass1_5(self, graph):
        name = self.name
        inst = self.instantiation

        self.node_name = name.id_str
        parameter_bindings = inst.parameters

        self.node_type = graph.resolve_node_type(inst.type_name)
        # print(self.node_name, self.node_type, parameter_bindings)

        # The node type may be declared after this node.
        self.node_type.compile_pass1_5_if_needed()

        self.par_values = {}
        seen = set()
        for parameter in parameter_bindings:
            key = parameter.name
            value = parameter.value

            if key.id_str in self.node_type.parsed_constants:
                raise ProgramError(key, f'Cannot override constant {key.id_str!r}')
            if key.id_str not in self.node_type.parameters_by_name:
                raise ProgramError(key, f'Unknown parameter for {self.node_type.name.id_str}: {key}')
            par = self.node_type.parameters_by_name[key.id_str]
            if par in seen:
                raise ProgramError(parameter, f'Duplicate parameter binding {key.id_str!r}')
            seen.add(par)
            if parameter.is_optional:
                if not par.is_optional:
                    raise ProgramError(parameter, 'Optional binding requires an optional or defaulted parameter')
                from .expressions import compile_constant
                compile_constant(graph, value, model.types.TypeOption(par.type_resolved), {})
                self.par_values[par] = model.value.OptionalBinding(graph, value, par)

        pending = {self.node_type.parameters_by_name[parameter.name.id_str]: parameter
                   for parameter in parameter_bindings if not parameter.is_optional}

        def bind(par):
            parameter = pending.pop(par, None)
            if parameter is None:
                return
            # Bind the parameters this parameter's type refers to first.
            for dependency in type_parameters(par.type_resolved):
                bind(dependency)
            value = parameter.value
            par_type = resolve_type(par.type_resolved, self.par_values)
            # TODO: Is passing par_values=self.par_values here the correct thing to do?
            if par_type == model.types.ValueType:
                val = graph.get_const_value(value, par_type, par_values=self.par_values)
                self.par_values[par] = (val.value if isinstance(val, model.ConstantValue) else val)
            else:
                val = graph.get_const_value(value, par_type, par_values=self.par_values)
                self.par_values[par] = val

        for par in list(pending):
            bind(par)

    def compile_pass2(self, graph):
        node = self.node_type.create(graph=graph, par_values=self.par_values, name=self.node_name, instantiation=self)

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, new_graph):
        pass


class DeclarationConnection(Declaration):
    @check_parameters
    def __init__(self, sources: typing.List[Reference], target: Reference, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.sources = sources
        self.target = target

    def compile_pass1(self, graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        sources = self.sources
        target = self.target

        # print(source_node_name, source_output_name, target_node_name, target_input_name)
        res_sources = [source.resolve(graph, PortDirection.Output, 'source') for source in sources]

        res_target = target.resolve(graph, PortDirection.Input, 'target')

        # TODO: Disallow multiple connect statements to a list input?
        with self.context:
            for source, attributes in zip(res_sources, self.edge_attributes):
                model.connect(source.port, res_target.port, self, attributes=attributes)

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, new_graph):
        pass


class DeclarationState(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, var_type: Value, initial_value: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.var_type = var_type
        self.initial_value = initial_value

        self.name_str = self.name.id_str

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.value_nodes:
            hints = [
                CompilerHint(graph.value_nodes[name], 'Previous declaration was here'),
            ]
            raise ProgramError(self, f'Duplicate name {name!r}', hints=hints)
        register_name(graph, self.name, self)
        graph.value_nodes[name] = self

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        self.interface = interface

    def create_io(self, new_graph):
        ty = self.interface.resolve_type(self.var_type)
        check_runtime_type(ty, self)
        # TODO: Resolve initial_value
        variable = model.StateNode(new_graph, ty, name=self.name_str, initial_value=self.initial_value)
        copy_attributes(variable, self)


def validate_nodes(graph):
    """Require a source for each procedure input port and variable."""
    for procedure in graph.procedures.values():
        procedure.validate_ports()
    for name, variable in graph.value_nodes.items():
        is_state = isinstance(variable, DeclarationState)
        node = (graph.state_nodes if is_state else graph.temporary_nodes)[name]
        if node.connected_source is None:
            raise ProgramError(variable, f'{"State" if is_state else "Temporary"} {name!r} has no source')


def register_name(graph, name, declaration):
    """Give nodes and named ports one namespace."""
    previous = graph.name_declarations.get(name.id_str)
    if previous is not None:
        raise ProgramError(name, f'Duplicate name {name.id_str!r}', hints=[
            CompilerHint(previous, 'Previous declaration was here'),
        ])
    graph.name_declarations[name.id_str] = declaration


class DeclarationTemporary(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, var_type: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.var_type = var_type

        self.name_str = self.name.id_str

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.value_nodes:
            hints = [
                CompilerHint(graph.value_nodes[name], 'Previous declaration was here'),
            ]
            raise ProgramError(self, f'Duplicate name {name!r}', hints=hints)
        register_name(graph, self.name, self)
        graph.value_nodes[name] = self

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        self.interface = interface

    def create_io(self, new_graph):
        ty = self.interface.resolve_type(self.var_type)
        check_runtime_type(ty, self)
        variable = model.TemporaryNode(new_graph, ty, name=self.name_str)
        copy_attributes(variable, self)


class StageDeclaration(SourceElement):
    @check_parameters
    def __init__(self, stage: RunStage, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.stage = stage

    @staticmethod
    def create_input(*, source_position: SourcePosition):
        return StageDeclaration(stage=RunStage.INPUT, source_position=source_position)

    @staticmethod
    def create_default(*, source_position: SourcePosition):
        return StageDeclaration(stage=RunStage.DEFAULT, source_position=source_position)

    @staticmethod
    def create_output(*, source_position: SourcePosition):
        return StageDeclaration(stage=RunStage.OUTPUT, source_position=source_position)


class ReferencePort(SourceElement):
    def __init__(self, reference: Reference, alias: typing.Optional[typing.Union[StringConstant, Identifier]] = None, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.reference = reference
        self.alias = alias

    @property
    def key(self):
        if self.alias is not None:
            return (self.alias.id_str if isinstance(self.alias, Identifier)
                    else self.alias.value)
        names = [identifier.id_str for identifier in self.reference.identifiers]
        if len(names) != 1 or names[0] in ('default input', 'default output'):
            raise ProgramError(self, 'Default ports and complex references require an explicit alias')
        return names[0]


class TypedPort(SourceElement):
    """A typed procedure port, which connections supply or consume."""

    def __init__(self, name: Identifier, port_spec: PortTypeSpecification, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.port_spec = port_spec
        self.reference = None
        self.alias = None

    @property
    def key(self):
        return self.name.id_str


def check_port_keys(ports):
    keys = {}
    for port in ports:
        key = port.key
        if key in keys:
            raise ProgramError(port, f'Duplicate port name {key!r}', hints=[
                CompilerHint(keys[key], 'Previous port was here'),
            ])
        keys[key] = port


class DependsDeclaration(SourceElement):
    @check_parameters
    def __init__(self, *dependencies: SourceElement, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.dependencies = dependencies

        self.resolved_dependencies_value = None
        self.resolved_dependencies_value_in_process = False

    def compile_pass1(self, graph, proc):
        self.graph = graph
        self.proc = proc

    # Trigger resolution of dependencies and return them
    def get_resolved_dependencies(self):
        if self.resolved_dependencies_value is not None:
            return self.resolved_dependencies_value
        if self.resolved_dependencies_value_in_process:
            raise Exception('self.resolved_dependencies_value_in_process')
        self.resolved_dependencies_value_in_process = True

        resolved = []
        check_port_keys(self.dependencies)
        for port in self.dependencies:
            dep = port.reference
            if dep.length == 2:
                name, member = [identifier.id_str for identifier in dep.identifiers]
                variable = self.graph.value_nodes.get(name)
                if isinstance(variable, DeclarationState) and member in ('old', 'new'):
                    resolved.append(model.StateRead(variable, member, dep))
                    continue
                child = self.graph.instance_nodes.get(name)
                if child is not None:
                    output = child.output_ports_byname.get(member)
                    if output is not None:
                        resolved.append(output)
                        continue
                raise ProgramError(dep, f'Cannot resolve procedure dependency {dep}')
            if dep.length != 1:
                raise ProgramError(dep, 'Unsupported procedure dependency reference')
            identifier = dep.identifiers[0]
            name = identifier.id_str
            # TODO: Should not be done using nodes
            if name in self.graph.instance_nodes:
                res = self.graph.instance_nodes[name]
            elif name in self.graph.value_nodes:
                res = self.graph.value_nodes[name]
                if isinstance(res, DeclarationState):
                    raise ProgramError(dep, 'State dependencies must explicitly reference .old or .new')
            elif name in self.graph.input_ports:
                res = self.graph.input_ports[name]
            else:
                raise ProgramError(dep, f'Could not find dependency')
            resolved.append(res)

        self.resolved_dependencies_value = resolved
        return self.resolved_dependencies_value

    def compile_pass2(self, graph, proc):
        # Make sure get_resolved_dependencies() is executed at some point
        self.get_resolved_dependencies()


class ProducesDeclaration(SourceElement):
    @check_parameters
    def __init__(self, *outputs: SourceElement, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.outputs = outputs

    def compile_pass1(self, graph, proc):
        self.graph = graph
        self.proc = proc


def dependency_is_resource(dependency):
    if isinstance(dependency, model.InstanceNode):
        dependency = dependency.output_ports_byname.get('')
    return isinstance(getattr(dependency, 'port_spec', None), PortTypeSpecificationResource)


class StaticBinding(SourceElement):
    def __init__(self, expression, alias=None, *, source_position):
        super().__init__(source_position=source_position)
        self.expression = expression
        self.alias = alias

    @property
    def key(self):
        if self.alias is not None:
            return self.alias.id_str if isinstance(self.alias, Identifier) else self.alias.value
        if isinstance(self.expression, Reference) and self.expression.length == 1:
            return self.expression.identifiers[0].id_str
        raise ProgramError(self, 'Static binding requires an alias')


class UsesDeclaration(SourceElement):
    def __init__(self, *bindings, source_position):
        super().__init__(source_position=source_position)
        self.bindings = bindings

    def check(self, scope):
        from .expressions import compile_constant

        keys = set()
        for binding in self.bindings:
            with binding.context:
                key = binding.key
                if key in keys:
                    raise ProgramError(binding, f'Duplicate static binding key {key!r}')
                keys.add(key)
                expression = binding.expression
                if binding.alias is None:
                    if key not in scope.static_declarations:
                        raise ProgramError(expression, f'Unknown static declaration {key!r}')
                compile_constant(scope, expression, None, {})


class DeclarationInitializer(Declaration):
    def __init__(self, depends: DependsDeclaration, uses=None, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.depends = depends
        self.uses = uses

    def compile_pass1(self, graph):
        if not isinstance(graph, model.NodeType) or not graph.is_native:
            raise ProgramError(self, 'Native initializer requires a native node type')
        if graph.initializer is not None:
            raise ProgramError(self, 'Duplicate native initializer', hints=[
                CompilerHint(graph.initializer, 'Previous initializer was here'),
            ])
        graph.initializer = self
        self.depends.compile_pass1(graph, self)

    def compile_pass2(self, graph):
        for port in self.depends.dependencies:
            if isinstance(port, TypedPort):
                raise ProgramError(port, 'Initializer dependencies must reference resources')
        self.depends.compile_pass2(graph, self)
        if self.uses is not None:
            self.uses.check(graph)
        for port, dep in zip(self.depends.dependencies, self.depends.get_resolved_dependencies()):
            if not dependency_is_resource(dep):
                raise ProgramError(port, 'Initializer dependencies must be resources')

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, new_graph):
        pass

    def compile_pass1_5(self, graph):
        pass


class DeclarationProcedure(Declaration):
    @check_parameters
    def __init__(self, native_spec: typing.Optional[NativeSpecifier], name: Identifier, alias: typing.Optional[StringConstant], stage: StageDeclaration, depends: DependsDeclaration, produces: ProducesDeclaration, uses=None, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.is_native = native_spec is not None
        self.name = name
        self.alias = alias
        self.stage = stage
        self.depends = depends
        self.produces = produces
        self.uses = uses
        self.body = None

        # TODO: Allow this? In which cases? Does this need checks that the outputs are not used in earlier stages?
        # Currently this is used to update a state node after writing to a CSV file. The new value for the state node might depend on the result (success or not) of writing to the file. Probably at least updates to state variables should be allowed in the output stage.
        # Disallowing this would make it possible to save the state before the output stage (and therefore before any results of the current step become externally visible). Or state variables could be saved twice, once before and once after the output stage.
        # if self.stage.stage == RunStage.OUTPUT and len(self.produces.outputs) > 0:
        #     hints = [
        #         CompilerHint(self.depends, 'Output declared here'),
        #     ]
        #     raise ProgramError(self.stage, f'Procedure name {self.name} is declared as output stage but produces an output', hints=hints)

    @staticmethod
    def create_body(name, stage, depends, produces, body, *, source_position):
        procedure = DeclarationProcedure(None, name, None, stage, depends, produces,
                                         source_position=source_position)
        procedure.body = body
        for port in (*depends.dependencies, *produces.outputs):
            if isinstance(port.alias, StringConstant):
                raise ProgramError(port.alias, 'Procedure aliases must be identifiers')
        return procedure

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.procedures:
            hints = [
                CompilerHint(graph.procedures[name], 'Previous procedure was here'),
            ]
            raise ProgramError(self, f'Duplicate procedure name {name} in {graph}', hints=hints)
        register_name(graph, self.name, self)
        for previous in graph.procedures.values():
            if self.is_native and previous.is_native and previous.python_name == self.python_name:
                raise ProgramError(self.alias or self.name, f'Duplicate Python procedure name {self.python_name!r}', hints=[
                    CompilerHint(previous.alias or previous.name, 'Previous Python procedure name was here'),
                ])
        graph.procedures[name] = self

        self.depends.compile_pass1(graph, self)
        self.produces.compile_pass1(graph, self)

    @property
    def python_name(self):
        return self.alias.value if self.alias is not None else self.name.id_str

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        if graph.parent_graph is not None:
            self.compile_interface(graph)

    def compile_interface(self, graph):
        # Reference ports connect to the ports they reference.
        for declaration in self.depends.dependencies:
            if isinstance(declaration, ReferencePort):
                port = self.input_ports[declaration.key]
                with declaration.context:
                    model.connect(port.counterpart, port, declaration)
        for declaration in self.produces.outputs:
            if isinstance(declaration, ReferencePort):
                port = self.output_ports[declaration.key]
                if isinstance(port.port_spec, PortTypeSpecificationResource):
                    raise ProgramError(declaration, 'Procedures cannot produce resources')
                with declaration.context:
                    model.connect(port, port.counterpart, declaration)
        if self.uses is not None:
            self.uses.check(graph)
        if self.stage.stage == RunStage.INPUT:
            for port in self.input_ports.values():
                if not isinstance(port.port_spec, PortTypeSpecificationResource):
                    raise ProgramError(self.stage, f'Procedure name {self.name} is declared as input stage but depends on something else', hints=[
                        CompilerHint(self.depends, 'Dependencies declared here'),
                    ])

    def resolve_port(self, name, port_name, is_input):
        """Resolve a reference to a port, given as procedure and port name."""
        procedure = name.id_str
        if port_name is None:
            raise ProgramError(name, f'Procedure references require a port, as in {procedure}.port')
        ports, others = ((self.input_ports, self.output_ports) if is_input
                         else (self.output_ports, self.input_ports))
        port = ports.get(port_name.id_str)
        if port is None:
            if port_name.id_str in others:
                direction = 'output' if is_input else 'input'
                raise ProgramError(port_name, f'Port {port_name.id_str!r} of procedure {procedure!r} is an {direction}')
            raise ProgramError(port_name, f'Unable to find port {port_name.id_str!r} of procedure {procedure!r}')
        return port

    def validate_ports(self):
        for port in self.input_ports.values():
            if not port.is_list and port.connected_source is None:
                raise ProgramError(port.declaration, f'Input port {port.name!r} of procedure {self.name.id_str!r} has no source')

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, new_graph):
        check_port_keys(self.depends.dependencies)
        check_port_keys(self.produces.outputs)
        self.input_ports = {
            declaration.key: self.create_port(new_graph, declaration, is_input=True)
            for declaration in self.depends.dependencies
        }
        self.output_ports = {
            declaration.key: self.create_port(new_graph, declaration, is_input=False)
            for declaration in self.produces.outputs
        }

    def create_port(self, graph, declaration, *, is_input):
        port_type = model.ProcedureInputPort if is_input else model.ProcedureOutputPort
        if isinstance(declaration, ReferencePort):
            direction = PortDirection.Output if is_input else PortDirection.Input
            purpose = 'dependency' if is_input else 'destination'

            def counterpart():
                with declaration.context:
                    return declaration.reference.resolve(graph, direction, purpose).port
            return port_type(graph, self, declaration, counterpart=counterpart)

        spec = declaration.port_spec
        if spec.list_spec.is_list and not is_input:
            raise ProgramError(spec, 'Got multi specifier for non-input')
        if isinstance(spec, PortTypeSpecificationData):
            var_type = graph.resolve_type(spec.ty)
            check_runtime_type(var_type, spec)
            resource_type = None
        else:
            if not is_input:
                raise ProgramError(declaration, 'Procedures cannot produce resources')
            var_type = None
            resource_type = graph.resolve_resource_type(spec.ty)
        specification = model.PortSpecification(var_type, spec, resource_type, spec.list_spec.is_list)
        return port_type(graph, self, declaration, specification=specification)


# TODO: Should this to some extend be merged with NodeType? Should there be a common base class?
class DeclarationLibrary(Scope):
    @check_parameters
    def __init__(self, name: Identifier, declarations: list, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.declarations = declarations
        self.inner_attributes = getattr(declarations, 'inner_attributes', ())

        # TODO?
        self.libraries = {}

    @staticmethod
    def make(*args, source_position: SourcePosition):
        names = args[:-1]
        declarations = args[-1]
        for name in reversed(names):
            if name.id_str == 'parameter_option':
                raise ProgramError(name, 'parameter_option is reserved for the intrinsic')
            declarations = [DeclarationLibrary(
                name, declarations, source_position=source_position)]
        return declarations[0]

    @property
    def fullname(self):
        if isinstance(self.parent_graph, DeclarationLibrary):
            return self.parent_graph.fullname + '.' + self.name.id_str
        return self.name.id_str

    @property
    def fullname_tuple(self):
        if isinstance(self.parent_graph, DeclarationLibrary):
            return self.parent_graph.fullname_tuple + (self.name.id_str,)
        return (self.name.id_str,)

    def compile_pass1(self, graph):
        if hasattr(self, 'parent_graph'):
            raise Exception("hasattr(self, 'parent_graph')")
        self.parent_graph = graph
        self.services = graph.services
        # TODO: What should be allowed here?
        if not isinstance(graph, DeclarationLibrary) and graph.name is not None:
            raise ProgramError(self, 'Attempt to put namespace into invalid context')

        if self.name.id_str in graph.struct_types:
            raise model.ProgramError(self.name, f'Namespace {self.name.id_str} conflicts with struct type', hints=[
                model.CompilerHint(graph.struct_types[self.name.id_str], 'Previous declaration was here'),
            ])
        if self.name.id_str in graph.enum_types:
            raise ProgramError(self.name, f'Namespace {self.name.id_str} conflicts with enum type', hints=[
                CompilerHint(graph.enum_types[self.name.id_str].declaration, 'Previous declaration was here'),
            ])
        self.compiled_scope = graph.libraries.setdefault(self.name.id_str, self)

        for decl in self.declarations:
            with decl.context:
                # TODO: What is allowed here?
                if not isinstance(decl, (model.NodeType, DeclarationLibrary, DeclarationResourceType, DeclarationEnumType, DeclarationStructType, DeclarationNativeFunction)):
                    hints = [
                        CompilerHint(self, 'In this namespace'),
                    ]
                    raise ProgramError(decl, 'Declaration not allowed in namespace', hints=hints)

                decl.compile_pass1(self.compiled_scope)
                

    def compile_pass1_5(self, graph):
        for decl in self.declarations:
            with decl.context:
                decl.compile_pass1_5(self.compiled_scope)

    def compile_pass2(self, graph):
        for decl in self.declarations:
            with decl.context:
                decl.compile_pass2(self.compiled_scope)

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, new_graph):
        pass

    def __repr__(self):
        return self.fullname


class FunctionArgument(SourceElement):
    def __init__(self, name, ty, *, source_position):
        super().__init__(source_position=source_position)
        self.name = name
        self.ty = ty


class DeclarationNativeFunction(Declaration):
    def __init__(self, name, arguments, return_type, *, is_constant=False, source_position):
        super().__init__(source_position=source_position)
        self.name = name
        self.arguments = arguments
        self.return_type_ast = return_type
        # Constant functions are compile-time only; others are also callable in procedure bodies.
        self.is_constant = is_constant

    @staticmethod
    def create_constant(name, arguments, return_type, *, source_position):
        return DeclarationNativeFunction(name, arguments, return_type, is_constant=True,
                                         source_position=source_position)

    def compile_pass1(self, graph):
        from .native_functions import validate_entry
        if not isinstance(graph, DeclarationLibrary) and graph.name is not None:
            raise ProgramError(self, 'Native functions are only allowed at root or in namespaces')
        self.scope = graph
        self.function_name = getattr(graph, 'fullname_tuple', ()) + (self.name.id_str,)
        if self.function_name == ('Some',):
            raise ProgramError(self.name, 'Some is reserved for the Option constructor')
        names = [argument.name.id_str for argument in self.arguments]
        if len(set(names)) != len(names):
            raise ProgramError(self, 'Duplicate function argument name')
        self.argument_names = tuple(names)
        if self.is_constant:
            validate_entry(self.function_name, self)
        graph.native_functions[self.name.id_str] = self

    def resolve_signature(self):
        if not hasattr(self, 'parameter_types'):
            self.parameter_types = [self.scope.resolve_type(argument.ty) for argument in self.arguments]
            self.return_type = self.scope.resolve_type(self.return_type_ast)
            if any(ty.contains_parameters for ty in self.parameter_types + [self.return_type]):
                raise ProgramError(self, 'Native functions require fixed value types')
            if not self.is_constant:
                for ty in self.parameter_types + [self.return_type]:
                    if not ty.is_runtime_type:
                        name = '.'.join(self.function_name)
                        raise ProgramError(self, f'Function {name!r} uses parameter-only type {ty}; '
                                           'declare it native constant function')
        return self

    def invoke(self, values, sources, call):
        if self.is_constant:
            from .native_functions import invoke
            return invoke(self, values, sources, call)
        from .native_functions import invoke_runtime_function
        return invoke_runtime_function(self, values, call)

    def compile_pass1_5(self, graph):
        self.resolve_signature()

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, graph):
        pass

    def compile_pass2(self, graph):
        pass
