"""Compile-time TwoN configuration functions."""

import os

from wirely.generated.structs import TwoNCredentials
from wirely.two_n import settings

from ..native_functions import FunctionServices


def is_valid_code(code: str, *, services: FunctionServices) -> bool:
    try:
        settings.check_code(code)
    except ValueError:
        return False
    return True


def read_credentials(filename: str, *, services: FunctionServices) -> TwoNCredentials:
    if not os.path.isabs(filename):
        raise services.error(
            'UsernamePasswordFile must be absolute; use '
            'Path.Join(Path.Parent(Path.SourceFile()), "credentials.txt")',
            argument=0,
        )
    try:
        with open(filename, 'r') as file:
            username = file.readline().strip()
            password = file.readline().strip()
        if username == '' or password == '':
            raise ValueError(f'Failed to read username and password from {filename!r}')
    except (OSError, ValueError) as error:
        raise services.error(str(error), argument=0) from error
    return TwoNCredentials(username, password)
