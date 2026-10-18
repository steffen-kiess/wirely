"""Port directions, port types, and declaration specifiers."""

import dataclasses
import enum

from wirely.util import immutable_enum

from .base import SourcePosition, SyntaxElement, Value, ProgramError


@immutable_enum
class PortDirection(enum.Enum):
    Input = enum.auto()
    Output = enum.auto()


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class PortDirectionSpecifier(SyntaxElement):
    direction: PortDirection

    @staticmethod
    def create_input(*, source_position: SourcePosition) -> 'PortDirectionSpecifier':
        return PortDirectionSpecifier(PortDirection.Input, source_position=source_position)

    @staticmethod
    def create_output(*, source_position: SourcePosition) -> 'PortDirectionSpecifier':
        return PortDirectionSpecifier(PortDirection.Output, source_position=source_position)


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class ListSpecifier(SyntaxElement):
    is_list: bool

    @staticmethod
    def create_list(*, source_position: SourcePosition) -> 'ListSpecifier':
        return ListSpecifier(True, source_position=source_position)

    @staticmethod
    def create_nolist(*, source_position: SourcePosition) -> 'ListSpecifier':
        return ListSpecifier(False, source_position=source_position)


# TODO: Rename Value to Level or State or keep Value?
# TODO: Rename Signal to Impulse or Trigger or Event?
@immutable_enum
class ConnectionKind(enum.Enum):
    Value = enum.auto()
    Event = enum.auto()


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class ConnectionKindSpecifier(SyntaxElement):
    kind: ConnectionKind

    @staticmethod
    def create_value(*, source_position: SourcePosition) -> 'ConnectionKindSpecifier':
        return ConnectionKindSpecifier(ConnectionKind.Value, source_position=source_position)

    @staticmethod
    def create_event(*, source_position: SourcePosition) -> 'ConnectionKindSpecifier':
        return ConnectionKindSpecifier(ConnectionKind.Event, source_position=source_position)


class PortTypeSpecification(SyntaxElement):
    list_spec: ListSpecifier
    ty: Value


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class PortTypeSpecificationData(PortTypeSpecification):
    list_spec: ListSpecifier
    kind_spec: ConnectionKindSpecifier
    ty: Value


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class PortTypeSpecificationResource(PortTypeSpecification):
    list_spec: ListSpecifier
    ty: Value

    def check_supported_input(self) -> None:
        if self.list_spec.is_list:
            raise ProgramError(self, 'Multi resource inputs are not supported')


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class NodeTypeSpecifier(SyntaxElement):
    specifier_str: str

    @staticmethod
    def create_singleton(*, source_position: SourcePosition) -> 'NodeTypeSpecifier':
        return NodeTypeSpecifier('singleton', source_position=source_position)


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class NativeSpecifier(NodeTypeSpecifier):
    specifier_str: str = dataclasses.field(default='native', init=False)


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class OptionalSpecifier(NodeTypeSpecifier):
    specifier_str: str = dataclasses.field(default='optional', init=False)
