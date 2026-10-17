"""Port directions, port types, and declaration specifiers."""

import enum

from wirely.util import check_parameters

from .base import SourcePosition, SourceElement, Value, ProgramError


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


class PortTypeSpecification(SourceElement):
    ty: Value  # Set by the subclasses.

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

    def check_supported_input(self):
        if self.list_spec.is_list:
            raise ProgramError(self, 'Multi resource inputs are not supported')


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
