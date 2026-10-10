"""Source positions, diagnostics, and base classes of the syntax tree."""

import typing
import abc
import contextlib
import os

import lark

from wirely.util import check_parameters


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


class Value(SourceElement):
    @check_parameters
    def __init__(self, *, source_position: SourcePosition):
        super().__init__(source_position=source_position)


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
