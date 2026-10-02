import enum
import typing
import abc
import contextlib
import re
import datetime

import lark

import wirely as blocks
from wirely.util import check_parameters


class SourcePosition:
    @check_parameters
    def __init__(self, *, filename, meta, input_data):
        self.filename = filename
        self.meta = meta
        self.input_data = input_data

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


class RefType(enum.Enum):
    BlockInput = enum.auto()
    BlockOutput = enum.auto()


# TODO: Merge with RefType?
class BlockConnectorType(enum.Enum):
    BlockInput = enum.auto()
    BlockOutput = enum.auto()


class RunStage(enum.Enum):
    INPUT = enum.auto()
    DEFAULT = enum.auto()
    OUTPUT = enum.auto()


class BlockConnectorTypeSpecifier(SourceElement):
    @check_parameters
    def __init__(self, conn_type: BlockConnectorType, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.conn_type = conn_type

    @staticmethod
    @check_parameters
    def create_input(*, source_position: SourcePosition):
        return BlockConnectorTypeSpecifier(BlockConnectorType.BlockInput, source_position=source_position)

    @staticmethod
    @check_parameters
    def create_output(*, source_position: SourcePosition):
        return BlockConnectorTypeSpecifier(BlockConnectorType.BlockOutput, source_position=source_position)


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


class ConnectorSpecification(SourceElement):
    @check_parameters
    def __init__(self, list_spec: ListSpecifier, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.list_spec = list_spec


class ConnectorSpecificationData(ConnectorSpecification):
    @check_parameters
    def __init__(self, list_spec: ListSpecifier, kind_spec: ConnectionKindSpecifier, ty: Value, *, source_position: SourcePosition):
        super().__init__(list_spec=list_spec, source_position=source_position)
        self.kind_spec = kind_spec
        self.ty = ty


class ConnectorSpecificationResource(ConnectorSpecification):
    @check_parameters
    def __init__(self, list_spec: ListSpecifier, ty: Value, *, source_position: SourcePosition):
        super().__init__(list_spec=list_spec, source_position=source_position)
        self.ty = ty


class BlockTypeSpecifier(SourceElement):
    @check_parameters
    def __init__(self, specifier_str, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)

        self.specifier_str = specifier_str

    @staticmethod
    def create_singleton(*, source_position: SourcePosition):
        return BlockTypeSpecifier(specifier_str='singleton', source_position=source_position)


class NativeSpecifier(BlockTypeSpecifier):
    @check_parameters
    def __init__(self, *, source_position: SourcePosition):
        super().__init__(specifier_str='native', source_position=source_position)


class OptionalSpecifier(BlockTypeSpecifier):
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

    @classmethod
    def negative(cls, token, *, source_position):
        value = cls(token, source_position=source_position)
        value.value = -value.value
        return value


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
    def create_implicit(conn_type_spec: BlockConnectorTypeSpecifier, *, source_position: SourcePosition):
        conn_type = conn_type_spec.conn_type
        # TODO: Clean up
        if conn_type == BlockConnectorType.BlockInput:
            name = 'implicit input'
        elif conn_type == BlockConnectorType.BlockOutput:
            name = 'implicit output'
        else:
            raise Exception(f'Unknown value for conn_type: {conn_type}')
        identifier = Identifier(name, source_position=source_position)
        return Reference([identifier], source_position=source_position)

    def __repr__(self):
        # TODO: Somehow show which enclosing block a parameter belongs to?
        # return '.'.join([i.id_str for i in self.identifiers]) + f' [{self.source_position.start_line}]'
        return '.'.join([i.id_str for i in self.identifiers])

    @property
    def length(self):
        return len(self.identifiers)

    @check_parameters
    def resolve(self, graph, ref_type: RefType, purpose: str):
        if ref_type in [RefType.BlockInput, RefType.BlockOutput]:
            if self.length == 1:
                block_name_id = self.identifiers[0]
                member_name_id = None
            elif self.length == 2:
                block_name_id = self.identifiers[0]
                member_name_id = self.identifiers[1]
            else:
                raise ProgramError(self, f'Got unexpected number of identifiers in {purpose}: {self}')
            return ResolvedReferenceBlockMember(graph, ref_type, block_name_id, member_name_id, purpose)
        else:
            raise Exception(f'Unknown RefType: {ref_type!r}')


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


class BracketOperator(Value):
    @check_parameters
    def __init__(self, base: Value, parameter: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.base = base
        self.parameter = parameter


# TODO: Remove and merge into Reference?
class ResolvedReferenceBlockMember(SourceElement):
    @check_parameters
    # TODO: member_name should not be typing.Optional
    def __init__(self, graph, ref_type: RefType, block_name: Identifier, member_name: typing.Optional[Identifier], purpose: str):
        if ref_type == RefType.BlockInput:
            member_type = 'input'
            member_dict_lambda = lambda block: block.block_inputs_byname
        elif ref_type == RefType.BlockOutput:
            member_type = 'output'
            member_dict_lambda = lambda block: block.block_outputs_byname
        else:
            raise Exception(f'Unknown RefType: {ref_type!r}')

        self.block_name = block_name
        self.member_name = member_name

        self.block_name_str = self.block_name.id_str
        if self.member_name is None:
            # TODO: Clean up
            if ref_type == RefType.BlockInput:
                # self.member_name_str = 'implicit input'
                self.member_name_str = ''
            elif ref_type == RefType.BlockOutput:
                # self.member_name_str = 'implicit output'
                self.member_name_str = ''
            else:
                raise Exception(f'Unknown value for ref_type: {ref_type}')
            # TODO: Remove member_name_elem (Hack)
            self.member_name_elem = block_name
        else:
            self.member_name_str = self.member_name.id_str
            self.member_name_elem = self.member_name

        # print(ref_type, repr(self.block_name_str), repr(self.member_name_str))
        # TODO: Should this be here?
        if ref_type == RefType.BlockInput:
            if self.block_name_str in graph.block_outputs and self.member_name_str == '':
                self.member = graph.block_outputs[self.block_name_str]
                # TODO: This is a hack
                return
        if ref_type == RefType.BlockOutput:
            if self.block_name_str in graph.block_inputs and self.member_name_str == '':
                self.member = graph.block_inputs[self.block_name_str]
                # TODO: This is a hack
                return
            if self.block_name_str in graph.block_state_variables and self.member_name_str == '':
                self.member = graph.block_state_variables[self.block_name_str]
                # TODO: This is a hack
                return
            if self.block_name_str in graph.block_temporary_variables and self.member_name_str == '':
                self.member = graph.block_state_variables[self.block_name_str]
                # TODO: This is a hack
                return

        if self.block_name_str not in graph.blocks:
            raise ProgramError(self.block_name, f'Unable to find {purpose} block {self.block_name_str!r}')
        self.block = graph.blocks[self.block_name_str]
        member_dict = member_dict_lambda(self.block)
        if self.member_name_str in member_dict:
            self.member = member_dict[self.member_name_str]
        else:
            if self.member_name_str == '':
                raise ProgramError(self.block_name, f'{purpose} block {self.block_name_str} does not have an implicit {member_type}')
            else:
                raise ProgramError(self.member_name_elem, f'Unable to find {purpose} block {member_type} {self.block_name_str}.{self.member_name_str}')


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

        # TODO: Remove one of block_type_interfaces / block_type_implementations
        self.block_type_interfaces = {}
        self.block_type_implementations = {}
        self.resource_types = {}

    # TODO: Move to Declaration?
    @property
    @abc.abstractmethod
    def fullname(self):
        ...

    # TODO: Handle parameters for parent types properly? Does this even make sense?
    def resolve_type(self, ty, par_values={}):
        with ty.context:
            # TODO: Clean up
            if isinstance(ty, BracketOperator):
                if not isinstance(ty.base, Reference):
                    raise blocks.ProgramError(ty, f'Expected Reference as generic type base, got {ty}')
                if ty.base.length == 1 and ty.base.identifiers[0].id_str == 'Option':
                    gen_type = blocks.types.TypeOption
                elif ty.base.length == 1 and ty.base.identifiers[0].id_str == 'List':
                    gen_type = blocks.types.TypeList
                else:
                    raise blocks.ProgramError(ty, f'Cannot find generic type {ty.base}')
                # if len(ty.parameters) != 1:
                #     raise blocks.ProgramError(ty, f'Expected 1 type parameter, got {len(ty.parameters)}: {ty}')
                par_type = self.resolve_type(ty.parameter, par_values=par_values)
                return gen_type(par_type)
            elif not isinstance(ty, Reference):
                raise blocks.ProgramError(ty, f'Expected Reference or BracketOperator as type name, got {ty}')

            # TODO: Move somewhere else, clean up
            if ty.length == 1:
                name = ty.identifiers[0].id_str
                # TODO: Avoid hasattr
                if hasattr(self, 'parsed_parameters') and name in self.parsed_parameters:
                    par = self.parsed_parameters[name]
                    # TODO
                    # if par.ty == blocks.types.ValueType:
                    if par.ty.length == 1 and par.ty.identifiers[0].id_str == 'ValueType':
                        # TODO: Handle par_values=None?
                        # TODO: More checks?
                        if par not in par_values:
                            # TODO: Should TypeParameter creation happen here?
                            return blocks.TypeParameter(par)
                        return par_values[par]
                if name in blocks.types.type_by_name:
                    return blocks.types.type_by_name[name]
            raise blocks.ProgramError(ty, f'Cannot find type {ty}')

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
        raise blocks.ProgramError(name, f'Cannot find resource type {name}')

    # TODO: Should this be named get_const_value()? (Also returns e.g. ValueFunctionCall)
    def get_const_value(self, value, value_type, par_values={}):
        with value.context:
            if value_type == blocks.types.ValueType:
                return blocks.ConstantValue(value_type, self.resolve_type(value, par_values=par_values),
                                            ast_element=value)
            if isinstance(value, blocks.ListLiteral) and isinstance(value_type, blocks.types.TypeList):
                elements = [self.get_const_value(element, value_type.inner_type, par_values=par_values)
                            for element in value.values]
                return blocks.value.ValueList(value_type, elements, ast_element=value)
            if isinstance(value, blocks.FunctionCall):
                if not isinstance(value.fun, blocks.Reference) or len(value.fun.identifiers) != 1:
                    raise blocks.ProgramError(value.fun, f'Function is not a single identifier')
                function_name = value.fun.identifiers[0].id_str
                if function_name == 'Some':
                    if len(value.parameters) != 1:
                        raise blocks.ProgramError(value, 'Some expects exactly one argument')
                    if not isinstance(value_type, blocks.types.TypeOption):
                        raise blocks.ProgramError(value, 'Some requires an Option type')
                    inner = self.get_const_value(value.parameters[0], value_type.inner_type, par_values=par_values)
                    return blocks.value.ValueOptionSome(value_type, inner, ast_element=value)
                if function_name not in blocks.value.functions:
                    raise blocks.ProgramError(value.fun, f'Cannot find function {function_name!r}')
                function = blocks.value.functions[function_name]
                blocks.value.check_value_type(function.return_type, value_type, value)
                if len(value.parameters) != len(function.parameter_types):
                    raise blocks.ProgramError(value, f'Function {function_name!r} expected {len(function.parameter_types)} parameters, but got {len(value.parameters)}')
                parameters = [self.get_const_value(parameter, function.parameter_types[i], par_values=par_values) for i, parameter in enumerate(value.parameters)]
                return blocks.value.ValueFunctionCall(function=function, parameters=parameters, ast_element=value)

            # TODO: Clean up
            found = True
            while found:
                found = False
                if isinstance(value, blocks.Reference) and value.length == 1:
                    name = value.identifiers[0].id_str
                    if name in self.parsed_parameters:
                        par = self.parsed_parameters[name]
                        if not(isinstance(par.ty, blocks.Reference) and par.ty.length == 1
                               and par.ty.identifiers[0].id_str == 'ValueType'):
                            parameter_type = self.resolve_type(par.ty, par_values=par_values)
                            blocks.value.check_value_type(parameter_type, value_type, value)
                            # value = par_values[name]
                            # found = True
                            # print(f'{par.name} in {par_values}')
                            # TODO: Clean up / Check whether this really is a parameter
                            if par not in par_values:
                                return blocks.ValueParameter(value_type, par, ast_element=value)
                            return blocks.ConstantValue(value_type, value_type.verify_value(par_values[par].evaluate()), ast_element=value)
            return blocks.ConstantValue(value_type, value_type.parse_value(value), ast_element=value)
            # raise blocks.ProgramError(value, f'Cannot turn {value!r} into constant value of type {value_type}')

    def resolve_block_type(self, name, par_values={}):
        # print(self, 'resolve_block_type', name)

        if name.length >= 2:
            library = self.libraries.get(name.identifiers[0].id_str)
            for identifier in name.identifiers[1:-1]:
                if library is None:
                    break
                library = library.libraries.get(identifier.id_str)
            if library is not None:
                block_name = name.identifiers[-1].id_str
                if block_name in library.block_type_implementations:
                    return library.block_type_implementations[block_name]

        if name.length == 1:
            block_type_name_id = name.identifiers[0]

            if block_type_name_id.id_str in self.block_type_implementations:
                return self.block_type_implementations[block_type_name_id.id_str]

        if self.parent_graph is None:
            raise blocks.ProgramError(name, f'Cannot find block type {name}')
        return self.parent_graph.resolve_block_type(name, par_values=par_values)


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
        if name in graph.resource_types:
            previous = graph.resource_types[name].declaration
            raise ProgramError(self, f'Duplicate resource type {name}', hints=[
                CompilerHint(previous, 'Previous resource type was here'),
            ])
        if name in graph.block_type_interfaces:
            raise ProgramError(self, f'Resource type {name} conflicts with node type', hints=[
                CompilerHint(graph.block_type_interfaces[name], 'Previous node type was here'),
            ])
        if isinstance(graph, DeclarationLibrary):
            fullname = graph.fullname + '.' + name
        else:
            fullname = name
        graph.resource_types[name] = blocks.types.ResourceType(fullname, self)

    def create_io(self, new_graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass


class DeclarationConnector(Declaration):
    @check_parameters
    def __init__(self, name: typing.Optional[Identifier], conn_type_spec: BlockConnectorTypeSpecifier, conn_spec: ConnectorSpecification, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.conn_type_spec = conn_type_spec
        self.conn_spec = conn_spec

        # TODO: Remove?
        self.list_spec = self.conn_spec.list_spec
        if isinstance(self.conn_spec, ConnectorSpecificationData):
            self.ty = self.conn_spec.ty
            self.kind_spec = self.conn_spec.kind_spec

            self.kind = self.kind_spec.kind

        self.conn_type = self.conn_type_spec.conn_type

        if self.list_spec.is_list and self.conn_type != BlockConnectorType.BlockInput:
            raise ProgramError(self, f'Got list specifier for non-input')

        if self.name is None:
            if self.conn_type == BlockConnectorType.BlockInput:
                self.name_str = 'implicit input'
            elif self.conn_type == BlockConnectorType.BlockOutput:
                self.name_str = 'implicit output'
            else:
                raise Exception(f'Unknown value for conn_type: {self.conn_type}')
        else:
            self.name_str = self.name.id_str

    @staticmethod
    @check_parameters
    def create(conn_type_spec: BlockConnectorTypeSpecifier, name: Identifier, conn_spec : ConnectorSpecification, *, source_position: SourcePosition):
        return DeclarationConnector(name, conn_spec=conn_spec, conn_type_spec=conn_type_spec, source_position=source_position)

    @staticmethod
    @check_parameters
    def create_implicit(conn_type_spec: BlockConnectorTypeSpecifier, conn_spec : ConnectorSpecification, *, source_position: SourcePosition):
        return DeclarationConnector(None, conn_spec=conn_spec, conn_type_spec=conn_type_spec, source_position=source_position)

    @check_parameters
    def compile_pass1_btd(self, interface):
        self.interface = interface

        if self.interface.is_singleton and self.conn_type_spec.conn_type == BlockConnectorType.BlockInput:
            raise blocks.ProgramError(self, f'Singleton block cannot have inputs')

    def create_io(self, new_graph):
        # TODO: Use self.kind ConnectionKind
        # TODO: Pass par_values to resolve_type?
        # TODO: Should the entires conn_spec instance be passed?
        if isinstance(self.conn_spec, ConnectorSpecificationData):
            ty = self.interface.resolve_type(self.ty)
            resource_type = None
        elif isinstance(self.conn_spec, ConnectorSpecificationResource):
            ty = None
            resource_type = self.interface.resolve_resource_type(self.conn_spec.ty)
        else:
            raise InternalCompilerError(self, Exception('Unknown connector specification'))
        if self.conn_type == BlockConnectorType.BlockInput:
            name = self.name_str
            if name == '':
                name = 'implicit input'
            blocks.BlockInput(new_graph, ty, name=name, input_name=self.name_str, is_list=self.list_spec.is_list, conn_spec=self.conn_spec, resource_type=resource_type)
        elif self.conn_type == BlockConnectorType.BlockOutput:
            name = self.name_str
            if name == '':
                name = 'implicit output'
            blocks.BlockOutput(new_graph, ty, name=name, output_name=self.name_str, conn_spec=self.conn_spec, resource_type=resource_type)
        else:
            raise Exception(f'Unknown conn_type: {self.conn_type!r}')

    # TODO: Clean up compilation passes

    def compile_pass1(self, graph):
        pass

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
        if hasattr(self, 'type_resolved'):
            raise Exception("hasattr(self, 'type_resolved')")

        # TODO: Is this correct?
        self.type_resolved = self.interface.resolve_type(self.ty)

    def __repr__(self):
        # return f'Parameter <{self.name}> [{self.name.source_position.start_line}] : <{self.ty}>'
        if not hasattr(self, 'interface'):
            # return f'Parameter <(unknown block).{self.name}> : <{self.ty}>'
            return f'(unknown block).{self.name}'
        else:
            return f'{self.interface.name}.{self.name}'

    # TODO: Clean up compilation passes

    def compile_pass1(self, graph):
        pass

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass


class BlockParameter(SourceElement):
    @check_parameters
    def __init__(self, name: Identifier, value: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.value = value

    def __repr__(self):
        return f'Parameter <{self.name}> = <{self.value}>'
        # return f'Parameter <{self.name}> [{self.name.source_position.start_line}] = <{self.value}>'


class BlockInstantiation(SourceElement):
    @check_parameters
    def __init__(self, block_class_name: Reference, parameters: typing.List[BlockParameter]=[], *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.block_class_name = block_class_name
        self.parameters = parameters


class DeclarationBlockInstantiation(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, instantiation: BlockInstantiation, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.instantiation = instantiation

    def compile_pass1(self, graph):
        pass

    def compile_pass1_5(self, graph):
        name = self.name
        inst = self.instantiation

        self.block_name = name.id_str
        block_arg = inst.parameters

        self.block_type = graph.resolve_block_type(inst.block_class_name)
        # print(self.block_name, self.block_type, block_arg)

        # TODO: Is this needed?
        # self.block_type.compile_pass1_5_if_needed()

        self.par_values = {}
        for parameter in block_arg:
            key = parameter.name
            value = parameter.value

            if key.id_str not in self.block_type.parameters_by_name:
                raise ProgramError(key, f'Unknown parameter for {self.block_type.name.id_str}: {key}')
            par = self.block_type.parameters_by_name[key.id_str]
            par_type = par.type_resolved.resolve_self(self.par_values)
            # TODO: Is passing par_values=self.par_values here the correct thing to do?
            if par_type == blocks.types.ValueType:
                ty = graph.resolve_type(parameter.value, par_values=self.par_values)
                self.par_values[par] = ty
            else:
                val = graph.get_const_value(value, par_type, par_values=self.par_values)
                self.par_values[par] = val

    def compile_pass2(self, graph):
        block = self.block_type.create(graph=graph, par_values=self.par_values, name=self.block_name, instantiation=self)

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

        # print(source_block_name, source_output_name, target_block_name, target_input_name)
        res_sources = [source.resolve(graph, RefType.BlockOutput, 'source') for source in sources]

        res_target = target.resolve(graph, RefType.BlockInput, 'target')

        # TODO: Disallow multiple connect statements to a list input?
        with self.context:
            for source in res_sources:
                blocks.connect(source.member, res_target.member, self)

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, new_graph):
        pass


class DeclarationStateVariable(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, var_type: Value, initial_value: Value, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.var_type = var_type
        self.initial_value = initial_value

        self.name_str = self.name.id_str

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.variables:
            hints = [
                CompilerHint(graph.variables[name], 'Previous variable was here'),
            ]
            raise ProgramError(self, f'Duplicate variable name {name} in {graph}', hints=hints)
        graph.variables[name] = self

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        self.interface = interface

    def create_io(self, new_graph):
        ty = self.interface.resolve_type(self.var_type)
        # TODO: Resolve initial_value
        blocks.BlockStateVariable(new_graph, ty, name=self.name_str, initial_value=self.initial_value)


class DeclarationTemporaryVariable(Declaration):
    @check_parameters
    def __init__(self, name: Identifier, var_type: Reference, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.var_type = var_type

        self.name_str = self.name.id_str

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.variables:
            hints = [
                CompilerHint(graph.variables[name], 'Previous variable was here'),
            ]
            raise ProgramError(self, f'Duplicate variable name {name} in {graph}', hints=hints)
        graph.variables[name] = self

    def compile_pass1_5(self, graph):
        pass

    def compile_pass2(self, graph):
        pass

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        self.interface = interface

    def create_io(self, new_graph):
        ty = self.interface.resolve_type(self.var_type)
        blocks.BlockTemporaryVariable(new_graph, ty, name=self.name_str)


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


class ProcedureBinding(SourceElement):
    def __init__(self, reference: Reference, alias: typing.Optional[StringConstant] = None, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.reference = reference
        self.alias = alias

    @property
    def key(self):
        if self.alias is not None:
            return self.alias.value
        names = [identifier.id_str for identifier in self.reference.identifiers]
        if len(names) != 1 or names[0] in ('implicit input', 'implicit output'):
            raise ProgramError(self, 'Implicit connectors and complex references require an explicit alias')
        return names[0]


def check_procedure_binding_keys(bindings):
    keys = {}
    for binding in bindings:
        key = binding.key
        if key in keys:
            raise ProgramError(binding, f'Duplicate procedure binding key {key!r}', hints=[
                CompilerHint(keys[key], 'Previous binding was here'),
            ])
        keys[key] = binding


class DependsDeclaration(SourceElement):
    @check_parameters
    def __init__(self, *dependencies: ProcedureBinding, source_position: SourcePosition):
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
        check_procedure_binding_keys(self.dependencies)
        for binding in self.dependencies:
            dep = binding.reference
            if dep.length == 2:
                name, member = [identifier.id_str for identifier in dep.identifiers]
                variable = self.graph.variables.get(name)
                if isinstance(variable, DeclarationStateVariable) and member == 'old':
                    resolved.append(variable)
                    continue
                child = self.graph.blocks.get(name)
                if child is not None:
                    output = child.block_outputs_byname.get(member)
                    if output is not None:
                        resolved.append(output)
                        continue
                raise ProgramError(dep, f'Cannot resolve procedure dependency {dep}')
            if dep.length != 1:
                raise ProgramError(dep, 'Unsupported procedure dependency reference')
            identifier = dep.identifiers[0]
            name = identifier.id_str
            # TODO: Should not be done using blocks
            if name in self.graph.blocks:
                res = self.graph.blocks[name]
            elif name in self.graph.variables:
                res = self.graph.variables[name]
                if isinstance(res, DeclarationStateVariable):
                    raise ProgramError(dep, 'State dependencies must explicitly reference the old value with .old')
            elif name in self.graph.block_inputs:
                res = self.graph.block_inputs[name]
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
    def __init__(self, *outputs: ProcedureBinding, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.outputs = outputs

        self.resolved_outputs_value = None
        self.resolved_outputs_value_in_process = False

    def compile_pass1(self, graph, proc):
        self.graph = graph
        self.proc = proc

    def get_resolved_outputs(self):
        if self.resolved_outputs_value is not None:
            return self.resolved_outputs_value
        if self.resolved_outputs_value_in_process:
            raise Exception('self.resolved_outputs_value_in_process')
        self.resolved_outputs_value_in_process = True

        resolved = []
        check_procedure_binding_keys(self.outputs)
        for binding in self.outputs:
            out = binding.reference
            if out.length != 1:
                raise ProgramError(out, f'Output does not have exactly one component')
            identifier = out.identifiers[0]
            name = identifier.id_str
            # TODO: Should not be done using blocks
            if name in self.graph.variables:
                res = self.graph.variables[name]
            elif name in self.graph.block_outputs:
                res = self.graph.block_outputs[name]
            else:
                raise ProgramError(out, f'Could not find output')
            resolved.append(res)

        self.resolved_outputs_value = resolved
        return self.resolved_outputs_value

    def compile_pass2(self, graph, proc):
        # Make sure get_resolved_outputs() is executed at some point
        self.get_resolved_outputs()


def dependency_is_resource(dependency):
    if isinstance(dependency, blocks.Block):
        dependency = dependency.block_outputs_byname.get('')
    return isinstance(getattr(dependency, 'conn_spec', None), ConnectorSpecificationResource)


class DeclarationInitializer(Declaration):
    def __init__(self, depends: DependsDeclaration, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.depends = depends

    def compile_pass1(self, graph):
        if not isinstance(graph, blocks.BlockType) or not graph.is_native:
            raise ProgramError(self, 'Native initializer requires a native node type')
        if graph.initializer is not None:
            raise ProgramError(self, 'Duplicate native initializer', hints=[
                CompilerHint(graph.initializer, 'Previous initializer was here'),
            ])
        graph.initializer = self
        self.depends.compile_pass1(graph, self)

    def compile_pass2(self, graph):
        self.depends.compile_pass2(graph, self)
        for binding, dep in zip(self.depends.dependencies, self.depends.get_resolved_dependencies()):
            if not dependency_is_resource(dep):
                raise ProgramError(binding, 'Initializer dependencies must be resources')

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, new_graph):
        pass

    def compile_pass1_5(self, graph):
        pass


class DeclarationProcedure(Declaration):
    @check_parameters
    def __init__(self, native_spec: typing.Optional[NativeSpecifier], name: Identifier, alias: typing.Optional[StringConstant], stage: StageDeclaration, depends: DependsDeclaration, produces: ProducesDeclaration, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.is_native = native_spec is not None
        self.name = name
        self.alias = alias
        self.stage = stage
        self.depends = depends
        self.produces = produces

        # TODO: Allow this? In which cases? Does this need checks that the outputs are not used in earlier stages?
        # Currently this is used to update a state variable after writing to a CSV file. The new value for the state variable might depend on the result (success or not) of writing to the file. Probably at least updates to state variables should be allowed in the output stage.
        # Disallowing this would make it possible to save the state variables before the output stage (and therefore before any results of the current step become externally visible). Or state variables could be saved twice, once before and once after the output stage.
        # if self.stage.stage == RunStage.OUTPUT and len(self.produces.outputs) > 0:
        #     hints = [
        #         CompilerHint(self.depends, 'Output declared here'),
        #     ]
        #     raise ProgramError(self.stage, f'Procedure name {self.name} is declared as output stage but produces an output', hints=hints)

    def compile_pass1(self, graph):
        name = self.name.id_str
        if name in graph.procedures:
            hints = [
                CompilerHint(graph.procedures[name], 'Previous procedure was here'),
            ]
            raise ProgramError(self, f'Duplicate procedure name {name} in {graph}', hints=hints)
        for previous in graph.procedures.values():
            if previous.python_name == self.python_name:
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
        self.depends.compile_pass2(graph, self)
        self.produces.compile_pass2(graph, self)
        if self.stage.stage == RunStage.INPUT:
            for dep in self.depends.get_resolved_dependencies():
                if not dependency_is_resource(dep):
                    raise ProgramError(self.stage, f'Procedure name {self.name} is declared as input stage but depends on something else', hints=[
                        CompilerHint(self.depends, 'Dependencies declared here'),
                    ])

    # TODO: Clean up compilation passes

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, new_graph):
        pass


# TODO: Should this to some extend be merged with BlockType? Should there be a common base class?
class DeclarationLibrary(Scope):
    @check_parameters
    def __init__(self, name: Identifier, declarations: typing.List[Declaration], *, source_position: SourcePosition):
        super().__init__(source_position=source_position)
        self.name = name
        self.declarations = declarations

        # TODO?
        self.libraries = {}

    @staticmethod
    def make(*args, source_position: SourcePosition):
        names = args[:-1]
        declarations = args[-1]
        for name in reversed(names):
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
        # TODO: What should be allowed here?
        if not isinstance(graph, DeclarationLibrary) and graph.name is not None:
            raise ProgramError(self, 'Attempt to put namespace into invalid context')

        self.compiled_scope = graph.libraries.setdefault(self.name.id_str, self)

        for decl in self.declarations:
            with decl.context:
                # TODO: What is allowed here?
                if not isinstance(decl, (blocks.BlockType, DeclarationLibrary, DeclarationResourceType)):
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
