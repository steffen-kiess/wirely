import enum
import os
import typing


EnumT = typing.TypeVar('EnumT', bound=enum.Enum)


def immutable_enum(cls: type[EnumT]) -> type[EnumT]:
    """Prevent assignment and deletion of attributes on enum members."""
    def reject(self: enum.Enum, name: str, *args: object) -> typing.NoReturn:
        raise AttributeError(f'{type(self).__name__} members are immutable')

    setattr(cls, '__setattr__', reject)
    setattr(cls, '__delattr__', reject)
    return cls


def getEnvironmentBool(name: str, defVal: bool) -> bool:
    if type(defVal) != bool:
        raise Exception('type(defVal) != bool')

    if not name in os.environ:
        return defVal
    value = os.environ[name]
    s = value.lower()
    if s == '':
        return defVal
    elif s == '0' or s == 'false':
        return False
    elif s == '1' or s == 'true':
        return True
    else:
        raise Exception('Invalid value for environment variant %s: %s' % (repr(name), repr(value)))


def verbose_print(*values: object, flush: bool = False) -> None:
    """Print routine messages of native nodes only if WIRELY_VERBOSE is set."""
    if getEnvironmentBool('WIRELY_VERBOSE', False):
        print(*values, flush=flush)
