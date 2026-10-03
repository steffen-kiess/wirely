"""Compile-time configuration support for native nodes."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from . import model as blocks


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


_configuration_factories = {}
_loaded_namespaces = set()


def register_configuration(data_type, factory):
    """Register a compile-time factory for a native configuration class."""
    if data_type in _configuration_factories:
        raise ValueError(f'Configuration factory already registered for {data_type!r}')
    _configuration_factories[data_type] = factory


def configuration_factory(data_type, type_name):
    import importlib
    from ..native import native_block_pkgs

    namespace = type_name[:-1]
    if namespace not in _loaded_namespaces:
        pkg_name = native_block_pkgs.get(namespace)
        if pkg_name is not None:
            try:
                importlib.import_module('.native_preparation.' + pkg_name, 'wirely.compiler')
            except ModuleNotFoundError as error:
                if error.name != 'wirely.compiler.native_preparation.' + pkg_name:
                    raise
            _loaded_namespaces.add(namespace)
    try:
        return _configuration_factories[data_type]
    except KeyError:
        raise ValueError(f'No configuration factory registered for {data_type!r}') from None
