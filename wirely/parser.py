import sys
import os

import lark

from wirely import compiler_model as blocks
from wirely.util import check_parameters


with open(os.path.dirname(__file__) + '/blocks.lark', 'r') as file:
    grammar = file.read()

# TODO: Move somewhere else?

ast_classes = [
    blocks.ConnectionKindSpecifier,
    blocks.ConnectorSpecification,
    blocks.ConnectorSpecificationData,
    blocks.ConnectorSpecificationResource,
    blocks.DeclarationResourceType,
    blocks.BlockConnectorTypeSpecifier,
    blocks.ListSpecifier,
    blocks.ProgramError,
    blocks.Identifier,
    blocks.UnitLiteral,
    blocks.IntNumber,
    blocks.FloatNumber,
    blocks.StringConstant,
    blocks.Reference,
    blocks.Declaration,
    blocks.DeclarationConnector,
    blocks.DeclarationParameter,
    blocks.BlockType,
    blocks.BlockParameter,
    blocks.BlockInstantiation,
    blocks.DeclarationBlockInstantiation,
    blocks.DeclarationConnection,
    blocks.DeclarationStateVariable,
    blocks.DeclarationTemporaryVariable,
    blocks.DeclarationProcedure,
    blocks.DeclarationInitializer,
    blocks.NativeSpecifier,
    blocks.OptionalSpecifier,
    blocks.StageDeclaration,
    blocks.DependsDeclaration,
    blocks.ProducesDeclaration,
    blocks.ProcedureBinding,
    blocks.DeclarationLibrary,
    blocks.BlockTypeSpecifier,
    blocks.ListLiteral,
    blocks.FunctionCall,
    blocks.BracketOperator,
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

    def __init__(self, conf):
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

    def parse_comment(self, lex_state, parser_state):
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

    def next_token(self, lex_state, parser_state):
        while True:
            token = super().next_token(lex_state, parser_state)
            # print(repr(token.type))
            if token.type != 'COMMENT_START':
                return token

            self.parse_comment(lex_state, parser_state)


@lark.v_args(inline=True)
class Transformer(lark.Transformer):
    def __init__(self, filename, input_data):
        self.filename = filename
        self.input_data = input_data

    def copy(self, arg):
        return arg
    @lark.v_args()
    def copy_list(self, arg):
        return arg

    @lark.v_args()
    def file_input(self, decls):
        return decls

    def __default__(self, data, children, meta):
        if data.startswith('__'):
            return lark.Tree(data, children, meta)

        parts = data.split('__', 1)
        cls_name = parts[0]
        if cls_name in ast_classes_byname:
            source_position = blocks.SourcePosition(filename=self.filename, meta=meta, input_data=self.input_data)
            cls = ast_classes_byname[cls_name]
            if len(parts) == 1:
                return cls(*children, source_position=source_position)
            else:
                return getattr(cls, parts[1])(*children, source_position=source_position)

        raise Exception(f'No handler for {data}')

opts = {
    # https://lark-parser.readthedocs.io/en/stable/classes.html
    # 'strict': True,
    'start': 'file_input',
    'propagate_positions': True,
    'parser': 'lalr',
    # Don't use default ('contextual') to prevent e.g. "blockx" from being allowed instead of "block x"
    # https://stackoverflow.com/questions/61711950/how-to-prevent-lark-from-recognizing-parts-of-an-identifier-as-a-keyword
    'lexer': 'basic',
    '_plugins': {
        'BasicLexer': NestedCommentLexer,
    },
}
parser = lark.Lark(grammar, **opts)


def parse_str(input_data, *, filename):
    t = Transformer(filename, input_data=input_data)
    return t.transform(parser.parse(input_data))
