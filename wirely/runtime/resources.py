"""Resource values passed between native states and procedures."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResourceType:
    """A resource type known to the Python runtime."""

    name: str  # Native name, such as 'KNX.Bus'.
    python_type: type  # Class of the resource objects.


@dataclass(frozen=True, slots=True)
class ResourceValue:
    """A resource object of a checked resource type."""

    type: ResourceType
    object: object

    def __post_init__(self) -> None:
        if not isinstance(self.object, self.type.python_type):
            raise TypeError(f'Resource {self.type.name} requires a {self.type.python_type.__qualname__}, '
                            f'got {type(self.object).__qualname__}')
