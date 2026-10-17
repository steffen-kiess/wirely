"""Compile-time TwoN configuration functions."""

import os

from wirely.compiler.contracts import two_n as contracts
from wirely.runtime.structs import TwoNCredentials

from ..native_functions import FunctionServices, RegisteredConstantFunction, constant_function

native_functions: dict[str, RegisteredConstantFunction] = {}


@constant_function(native_functions, contracts.IsValidCode)
def is_valid_code(code: str, *, services: FunctionServices) -> bool:
    return len(code) <= 15 and all('0' <= digit <= '9' for digit in code)


@constant_function(native_functions, contracts.ReadCredentials)
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
