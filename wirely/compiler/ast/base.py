"""Source positions, diagnostics, and base classes of the syntax tree."""

import typing
import contextlib
import dataclasses
import os
from collections.abc import Iterator
from typing import Any, TypeVar

import lark

from wirely.util import check_parameters


_UNSET_SOURCE_PATH = object()


def capture_source_path(filename: str | os.PathLike | None) -> str | None:
    if not filename or (str(filename).startswith('<') and str(filename).endswith('>')):
        return None
    return os.path.abspath(filename)


class SourcePosition:
    @check_parameters
    def __init__(self, *, filename: str | os.PathLike | None, meta: lark.tree.Meta | None, input_data: str,
                 source_path: object = _UNSET_SOURCE_PATH) -> None:
        self.filename = filename
        self.meta = meta
        self.input_data = input_data
        self.source_path: str | None
        if source_path is _UNSET_SOURCE_PATH:
            self.source_path = capture_source_path(filename)
        else:
            assert source_path is None or isinstance(source_path, str)
            self.source_path = source_path

    @property
    def start_pos(self) -> int:
        # TODO: Handle source elements which were created without any token where e.g. start_pos is not set?
        assert self.meta is not None
        return self.meta.start_pos

    @property
    def start_line(self) -> int:
        assert self.meta is not None
        return self.meta.line


class SourceElement:
    # Set by the coordinator when declaration or port resolution fails.
    failed: bool

    @check_parameters
    def __init__(self, *, source_position: SourcePosition) -> None:
        self.source_position = source_position
        self.outer_attributes: tuple = ()
        self.inner_attributes: tuple = ()
        self.attributes: tuple = ()

    @property
    @contextlib.contextmanager
    def context(self) -> Iterator[None]:
        exc = None
        try:
            yield None
        except Exception as e:
            exc = e
            if isinstance(exc, ProgramError):
                raise
        if exc is not None:
            raise InternalCompilerError(self, exc)

    def __repr__(self) -> str:
        # The source text reads naturally in diagnostics; subclasses may refine it.
        position = getattr(self, 'source_position', None)
        meta = getattr(position, 'meta', None)
        if meta is not None and position is not None and not getattr(meta, 'empty', True) and position.input_data:
            return position.input_data[meta.start_pos:meta.end_pos]
        return f'{type(self).__name__}()'


SyntaxT = TypeVar('SyntaxT')


@typing.dataclass_transform(eq_default=False, frozen_default=True, field_specifiers=(dataclasses.field,))
def syntax(cls: type[SyntaxT]) -> type[SyntaxT]:
    """Make cls an immutable syntax class whose constructor checks its arguments.

    Syntax objects compare by identity and keep the source text as their default repr.
    """
    cls = dataclasses.dataclass(frozen=True, eq=False, repr=False)(cls)
    cls.__init__ = check_parameters(cls.__init__)  # type: ignore[method-assign]
    return cls


@syntax
class SyntaxElement(SourceElement):
    """An immutable part of the syntax tree below declarations.

    Compiler stages keep the information they derive elsewhere. Subclasses
    that only declare attribute types for their own subclasses are not
    decorated with @syntax.
    """

    _: dataclasses.KW_ONLY
    source_position: SourcePosition
    outer_attributes: tuple = ()
    inner_attributes: tuple = ()
    attributes: tuple = ()


class CompilerHint:
    @check_parameters
    def __init__(self, source_element: SourceElement, msg: str) -> None:
        self.source_element = source_element
        self.msg = msg
        self.fake_ui = lark.exceptions.UnexpectedInput()
        self.fake_ui.pos_in_stream = self.source_element.source_position.start_pos  # type: ignore[assignment]

    def __str__(self) -> str:
        return self.msg

    def get_context(self) -> str:
        assert self.source_element is not None
        return self.fake_ui.get_context(self.source_element.source_position.input_data)


class ProgramError(Exception):
    @check_parameters
    def __init__(self, source_element: typing.Optional[SourceElement], msg: str,
                 inner_exception: BaseException | None = None, *, hints: typing.List[CompilerHint] = []) -> None:
        # raise Exception()
        self.source_element = source_element
        self.msg = msg
        self.inner_exception = inner_exception
        self.hints = hints
        if self.source_element is not None:
            self.fake_ui = lark.exceptions.UnexpectedInput()
            self.fake_ui.pos_in_stream = self.source_element.source_position.start_pos  # type: ignore[assignment]

        if self.source_element is None and not isinstance(self, InternalCompilerError):
            raise Exception('self.source_element is None and not isinstance(self, InternalCompilerError)')

    def __str__(self) -> str:
        return self.msg

    def get_context(self) -> str:
        assert self.source_element is not None
        return self.fake_ui.get_context(self.source_element.source_position.input_data)


class InternalCompilerError(ProgramError):
    @check_parameters
    def __init__(self, source_element: typing.Optional[SourceElement], inner_exception: BaseException, *,
                 msg: str | None = None) -> None:
        if msg is None:
            msg = 'Internal Compiler Error: ' + str(inner_exception)
        super().__init__(source_element, msg, inner_exception)


@syntax
class Identifier(SyntaxElement):
    id_str: str

    def __post_init__(self) -> None:
        if self.id_str.startswith('r#'):
            object.__setattr__(self, 'id_str', self.id_str[2:])

    def __repr__(self) -> str:
        # return f'{self.id_str} [{self.source_position.start_line}]'
        return self.id_str


@syntax
class Value(SyntaxElement):
    pass


class Declaration(SourceElement):
    """Declarations implement only the preparation operations they need."""

    def register_interface_declarations(self, scope: Any) -> None:
        pass

    def collect_declarations(self, scope: Any) -> None:
        pass

    def prepare_interface(self, scope: Any) -> None:
        pass

    def bind_body(self, scope: Any) -> None:
        pass
