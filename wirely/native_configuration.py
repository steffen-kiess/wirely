"""Compile-time configuration support for native nodes."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

import wirely as blocks


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class NativeNodeConfigurationContext:
    name: str
    parameters: Mapping[str, object]
    _source: object = field(repr=False)
    _origins: Mapping = field(repr=False)

    def __post_init__(self):
        for name in ('parameters', '_origins'):
            value = getattr(self, name)
            if not isinstance(value, MappingProxyType):
                object.__setattr__(self, name, MappingProxyType(dict(value)))

    def _location(self, parameter):
        if parameter is None:
            return self._source, ()
        if not isinstance(parameter, str) or parameter not in self._origins:
            raise ValueError(f'Unknown parameter name {parameter!r}')
        return self._origins[parameter]

    def error(self, message, *, parameter=None):
        source, forwarding = self._location(parameter)
        hints = [blocks.CompilerHint(binding, 'Parameter was forwarded here')
                 for binding in forwarding]
        return blocks.ProgramError(source, message, hints=hints)

    def resolve_path(self, value, *, parameter=None):
        source, _ = self._location(parameter)
        try:
            path = Path(value)
        except (TypeError, ValueError) as error:
            raise self.error(str(error), parameter=parameter) from error
        if not path.is_absolute():
            filename = source.source_position.filename
            if not filename or (filename.startswith('<') and filename.endswith('>')):
                raise self.error('Relative path requires a source directory', parameter=parameter)
            path = Path(os.path.abspath(filename)).parent / path
        return Path(os.path.normpath(path))
