"""Parse KNX group addresses without a transport dependency."""

import re

from wirely import UInt16


_ADDRESS = re.compile(r'(?P<main>\d{1,2})(/(?P<middle>\d{1,2}))?/(?P<sub>\d{1,4})$')


def parse_group_address(address: str) -> UInt16:
    if address.isdigit():
        try:
            raw = int(address)
        except ValueError:
            raise ValueError(f'Invalid format for KNX group address: {address!r}') from None
        if raw > 65535:
            raise ValueError(f'Address out of range (0..65535): {address!r}')
        return UInt16(raw)

    match = _ADDRESS.match(address)
    if match is None:
        raise ValueError(f'Invalid format for KNX group address: {address!r}')
    main = int(match['main'])
    middle = int(match['middle']) if match['middle'] is not None else None
    sub = int(match['sub'])
    if main > 31:
        raise ValueError(f'Main group out of range (0..31): {address!r}')
    if middle is not None and middle > 7:
        raise ValueError(f'Middle group out of range (0..7): {address!r}')
    maximum = 2047 if middle is None else 255
    if sub > maximum:
        raise ValueError(f'Sub group out of range (0..{maximum}): {address!r}')
    return UInt16((main << 11) + ((middle or 0) << 8) + sub)
