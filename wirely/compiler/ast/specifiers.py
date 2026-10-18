"""Port directions, port types, and declaration specifiers."""

import dataclasses
import enum

from .base import SourcePosition, SyntaxElement, Value, ProgramError, syntax


class PortDirection(enum.Enum):
    Input = enum.auto()
    Output = enum.auto()


@syntax
class PortDirectionSpecifier(SyntaxElement):
    direction: PortDirection

    @staticmethod
    def create_input(*, source_position: SourcePosition) -> 'PortDirectionSpecifier':
        return PortDirectionSpecifier(PortDirection.Input, source_position=source_position)

    @staticmethod
    def create_output(*, source_position: SourcePosition) -> 'PortDirectionSpecifier':
        return PortDirectionSpecifier(PortDirection.Output, source_position=source_position)


@syntax
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
class ConnectionKind(enum.Enum):
    Value = enum.auto()
    Event = enum.auto()


@syntax
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


@syntax
class PortTypeSpecificationData(PortTypeSpecification):
    list_spec: ListSpecifier
    kind_spec: ConnectionKindSpecifier
    ty: Value


@syntax
class PortTypeSpecificationResource(PortTypeSpecification):
    list_spec: ListSpecifier
    ty: Value

    def check_supported_input(self) -> None:
        if self.list_spec.is_list:
            raise ProgramError(self, 'Multi resource inputs are not supported')


@syntax
class NodeTypeSpecifier(SyntaxElement):
    specifier_str: str

    @staticmethod
    def create_singleton(*, source_position: SourcePosition) -> 'NodeTypeSpecifier':
        return NodeTypeSpecifier('singleton', source_position=source_position)


@syntax
class NativeSpecifier(NodeTypeSpecifier):
    specifier_str: str = dataclasses.field(default='native', init=False)


@syntax
class OptionalSpecifier(NodeTypeSpecifier):
    specifier_str: str = dataclasses.field(default='optional', init=False)
