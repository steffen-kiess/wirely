"""Compile-time TwoN configuration functions."""

import os


def is_valid_code(*, context):
    code = context.arguments['code']
    return len(code) <= 15 and all('0' <= digit <= '9' for digit in code)


def read_credentials(*, context):
    filename = context.arguments['filename']
    if not os.path.isabs(filename):
        raise context.error(
            'UsernamePasswordFile must be absolute; use '
            'Path.Join(Path.Parent(Path.SourceFile()), "credentials.txt")',
            argument='filename',
        )
    try:
        with open(filename, 'r') as file:
            username = file.readline().strip()
            password = file.readline().strip()
        if username == '' or password == '':
            raise ValueError(f'Failed to read username and password from {filename!r}')
    except (OSError, ValueError) as error:
        raise context.error(str(error), argument='filename') from error
    return {'username': username, 'password': password}
