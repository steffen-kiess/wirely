from __future__ import annotations

import copy
import dataclasses
import sys
import os
from collections.abc import Iterable
from typing import Any, TypeVar

from lark.common import LexerConf
from lark.lexer import LexerState
from lark.parsers.lalr_parser import ParserState

import lark

from wirely.compiler import model
from wirely.util import getEnvironmentBool
from .attributes import Attribute, Metadata, MetadataLiteral, ParsedFile
from .ast import SourceElement, SyntaxElement, capture_source_path
from .expressions import (
    BinaryExpression, UnaryExpression, IfExpression, IfLetExpression, ForExpression, OnExpression, OnTrigger, EmitExpression,
    ExpressionBlock, PropertyAccess, LocalBinding, AssignmentExpression, ExpressionStatement, AssertionStatement,
)


T = TypeVar('T')


with open(os.path.dirname(__file__) + '/wirely.lark', 'r') as file:
    grammar = file.read()

# TODO: Move somewhere else?

ast_classes: list[Any] = [  # Grammar dispatch includes constructors and factories.
    BinaryExpression, UnaryExpression, IfExpression, IfLetExpression, ForExpression, OnExpression, OnTrigger, EmitExpression,
    ExpressionBlock, PropertyAccess, LocalBinding, AssignmentExpression, ExpressionStatement, AssertionStatement,
    model.ConnectionKindSpecifier,
    model.PortTypeSpecification,
    model.PortTypeSpecificationData,
    model.PortTypeSpecificationFixed,
    model.DeclarationResourceType,
    model.DeclarationEnumType,
    model.EnumMember,
    model.DeclarationStructType,
    model.StructField,
    model.StructLiteral,
    model.StructLiteralField,
    model.PortDirectionSpecifier,
    model.ListSpecifier,
    model.ProgramError,
    model.Identifier,
    model.UnitLiteral,
    model.IntNumber,
    model.FloatNumber,
    model.StringConstant,
    model.Reference,
    model.Declaration,
    model.DeclarationPort,
    model.DeclarationParameter,
    model.DeclarationConstant,
    model.DeclarationAssertion,
    model.DeclarationNodeType,
    model.ParameterBinding,
    model.Instantiation,
    model.DeclarationInstance,
    model.DeclarationConnection,
    model.DeclarationState,
    model.DeclarationBinding,
    model.DeclarationProcedure,
    model.DeclarationInitializer,
    model.NativeSpecifier,
    model.OptionalSpecifier,
    model.StageDeclaration,
    model.ReadsDeclaration,
    model.WritesDeclaration,
    model.ReadPort,
    model.ReferencePort,
    model.TypedPort,
    model.DeclarationLibrary,
    model.NodeTypeSpecifier,
    model.ListLiteral,
    model.OptionalExpression,
    model.FunctionCall,
    model.DeclarationNativeFunction,
    model.FunctionArgument,
    model.BracketOperator,
]

ast_classes_byname = {}
for cls in ast_classes:
    if hasattr(cls, 'name'):
        name = cls.name
    else:
        name = ''
        for c in cls.__name__:
            if c.isupper():
                name += '_'
            name += c.lower()
        if name.startswith('_'):
            name = name[1:]
    ast_classes_byname[name] = cls


# TODO: Make this more generic: Allow different modes/states/start conditions for the lexer ("default", "comment") and do this in the postlexer?
# Similar for flex: https://westes.github.io/flex/manual/Start-Conditions.html#Start-Conditions
class NestedCommentLexer(lark.lexer.BasicLexer):
    terminal_names = {'COMMENT_START', 'COMMENT_NEST', 'COMMENT_CONTENT', 'COMMENT_END'}
    shared_terminal_names = {'COMMENT_START'}

    def __init__(self, conf: LexerConf) -> None:
        # Lark keeps conf, and saves it in its cache, so change a copy.
        conf = copy.copy(conf)
        # Get comment terminals
        comment_terminals = list([terminal for terminal in conf.terminals if terminal.name in self.terminal_names])
        # Check whether any terminals are missing
        missing_comment_terminals = set(self.terminal_names) - set([terminal.name for terminal in comment_terminals])
        if len(missing_comment_terminals):
            raise Exception(f'Could not find comment terminals: {missing_comment_terminals}')

        # Remove terminals which are only valid in comment mode from configuration
        conf.terminals = list([terminal for terminal in conf.terminals if terminal.name not in self.terminal_names or terminal.name in self.shared_terminal_names])
        # Don't ignore any comment terminals
        conf.ignore = list([name for name in conf.ignore if name not in self.terminal_names])

        super().__init__(conf)
        # Create a scanner which is used while in comment mode
        self.comment_scanner = lark.lexer.Scanner(comment_terminals, self.g_regex_flags, self.re, self.use_bytes)

    def parse_comment(self, lex_state: LexerState, parser_state: ParserState | None) -> None:
        level = 1
        while lex_state.line_ctr.char_pos < len(lex_state.text):
            res = self.comment_scanner.match(lex_state.text, lex_state.line_ctr.char_pos)
            if not res:
                raise lark.exceptions.UnexpectedCharacters(lex_state.text, lex_state.line_ctr.char_pos, lex_state.line_ctr.line, lex_state.line_ctr.column, allowed=self.comment_scanner.allowed_types)
            value, type_ = res
            # TODO: COMMENT_CONTENT must be in self.newline_types
            # lex_state.line_ctr.feed(value, type_ in self.newline_types)
            lex_state.line_ctr.feed(value, True)
            # print(f'Got comment terminal: {type_}')
            if type_ == 'COMMENT_START' or type_ == 'COMMENT_NEST':
                level += 1
            elif type_ == 'COMMENT_CONTENT':
                pass
            elif type_ == 'COMMENT_END':
                level -= 1
                if level == 0:
                    return
            else:
                raise Exception(f'Got unknown comment terminal: {type_!r}')

        raise lark.exceptions.UnexpectedEOF(self.terminal_names)

    def next_token(self, lex_state: LexerState, parser_state: ParserState | None) -> lark.Token:  # type: ignore[override]
        while True:
            token = super().next_token(lex_state, parser_state)
            # print(repr(token.type))
            if token.type != 'COMMENT_START':
                return token

            self.parse_comment(lex_state, parser_state)


@lark.v_args(inline=True)
class Transformer(lark.Transformer):
    def __init__(self, filename: str | os.PathLike, input_data: str) -> None:
        self.filename = filename
        self.input_data = input_data
        self.source_path = capture_source_path(filename)

    def copy(self, arg: T) -> T:
        return arg
    @lark.v_args()
    def copy_list(self, arg: list[T]) -> list[T]:
        return arg

    @lark.v_args()
    def file_input(self, decls: list[Any]) -> ParsedFile:
        inner = [item for item in decls if isinstance(item, Attribute)]
        return ParsedFile([item for item in decls if not isinstance(item, Attribute)],
                          inner_attributes=inner)

    def attributed(self, *items: Any) -> Any:
        item = items[-1]
        attributes = tuple(items[:-1])

        def attach(element: Any) -> Any:
            # Dotted namespace attributes belong to the innermost block.
            if isinstance(element, model.DeclarationLibrary) and element.dotted:
                child = attach(element.declarations[0])
                return dataclasses.replace(element, declarations=(child,))
            return dataclasses.replace(element, outer_attributes=attributes)

        return attach(item)

    def metadata_path(self, *identifiers: model.Identifier) -> tuple[str, ...]:
        return tuple(identifier.id_str for identifier in identifiers)

    @lark.v_args()
    def metadata_arguments(self, arguments: list[Metadata | MetadataLiteral]) -> list[Metadata | MetadataLiteral]:
        return [MetadataLiteral(argument.name == 'true', argument.source_position)
                if isinstance(argument, Metadata) and argument.kind == 'flag'
                and argument.name in ('true', 'false') else argument
                for argument in arguments]

    def __default__(self, data: str, children: list[Any], meta: lark.tree.Meta) -> Any:
        if data.startswith('__'):
            return lark.Tree(data, children, meta)

        source_position = model.SourcePosition(
            filename=self.filename, meta=meta, input_data=self.input_data,
            source_path=self.source_path,
        )
        if data in ('outer_attribute', 'inner_attribute'):
            return Attribute(children[0], 'outer' if data == 'outer_attribute' else 'inner', source_position)
        if data in ('metadata_flag', 'metadata_value', 'metadata_list'):
            kind = {'metadata_flag': 'flag', 'metadata_value': 'value', 'metadata_list': 'list'}[data]
            value = None if kind == 'flag' else children[1]
            if kind == 'list':
                value = tuple(children[1])
            return Metadata(children[0], kind, value, source_position)
        if data == 'metadata_named_literal':
            if children[0] not in (('true',), ('false',)):
                raise model.ProgramError(SourceElement(source_position=source_position),
                                          'Attribute values must be literals')
            return MetadataLiteral(children[0] == ('true',), source_position)
        if data.startswith('metadata_'):
            value = children[0].value
            if data in ('metadata_negative_int', 'metadata_negative_float'):
                value = -value
            return MetadataLiteral(value, source_position)

        parts = data.split('__', 1)
        cls_name = parts[0]
        if cls_name in ast_classes_byname:
            cls = ast_classes_byname[cls_name]
            if len(parts) == 1:
                if issubclass(cls, SyntaxElement):
                    # Syntax classes hold child sequences as tuples.
                    inner = next((child.inner_attributes for child in children if isinstance(child, ParsedFile)), ())
                    children = [tuple(child) if isinstance(child, (list, ParsedFile)) else child for child in children]
                    return cls(*children, source_position=source_position, inner_attributes=inner)
                return cls(*children, source_position=source_position)
            return getattr(cls, parts[1])(*children, source_position=source_position)

        raise Exception(f'No handler for {data}')

opts = {
    # https://lark-parser.readthedocs.io/en/stable/classes.html
    # 'strict': True,
    'start': 'file_input',
    'propagate_positions': True,
    'parser': 'lalr',
    # Don't use default ('contextual') to prevent e.g. "nodex" from being allowed instead of "node x"
    # https://stackoverflow.com/questions/61711950/how-to-prevent-lark-from-recognizing-parts-of-an-identifier-as-a-keyword
    'lexer': 'basic',
    '_plugins': {
        'BasicLexer': NestedCommentLexer,
    },
}


def _cache_path() -> str | None:
    """Return the parser cache file if WIRELY_COMPILER_CACHE_LARK is set and its directory is usable."""
    if not getEnvironmentBool('WIRELY_COMPILER_CACHE_LARK', False):
        return None
    base = os.environ.get('XDG_CACHE_HOME', '')
    if not os.path.isabs(base):
        base = os.path.join(os.path.expanduser('~'), '.cache')
    directory = os.path.join(base, 'wirely')
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    except OSError:
        return None
    # Lark checks that a cached file matches the grammar and options; separate
    # files keep different Lark and Python versions from replacing each other.
    return os.path.join(directory, f'parser-lark{lark.__version__}-py{sys.version_info[0]}{sys.version_info[1]}.cache')


def create_parser(cache: str | None = None) -> lark.Lark:
    """Build the parser, loading its tables from the cache file if given and current, or saving them there."""
    return lark.Lark(grammar, cache=cache or False, **opts)


parser = create_parser(_cache_path())


def parse_str(input_data: str, *, filename: str | os.PathLike) -> ParsedFile:
    t = Transformer(filename, input_data=input_data)
    return t.transform(parser.parse(input_data))
