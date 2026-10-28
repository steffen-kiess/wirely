"""Parse KNX group and individual addresses without a transport dependency."""

import re

from wirely import UInt16


_ADDRESS = re.compile(r'(?P<main>[0-9]{1,2})(/(?P<middle>[0-9]{1,2}))?/(?P<sub>[0-9]{1,4})')
_INDIVIDUAL_ADDRESS = re.compile(r'(?P<area>[0-9]{1,2})\.(?P<line>[0-9]{1,2})\.(?P<device>[0-9]{1,3})')


def parse_group_address(address: str) -> UInt16:
    if re.fullmatch('[0-9]+', address):
        raw = int(address)
        if raw > 65535:
            raise ValueError(f'Address out of range (0..65535): {address!r}')
        return UInt16(raw)

    match = _ADDRESS.fullmatch(address)
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


def parse_individual_address(address: str) -> UInt16:
    """Parse an individual address such as "1.1.1", or its raw number."""
    if re.fullmatch('[0-9]+', address):
        raw = int(address)
        if raw > 65535:
            raise ValueError(f'Address out of range (0..65535): {address!r}')
        return UInt16(raw)

    match = _INDIVIDUAL_ADDRESS.fullmatch(address)
    if match is None:
        raise ValueError(f'Invalid format for KNX individual address: {address!r}')
    area = int(match['area'])
    line = int(match['line'])
    device = int(match['device'])
    if area > 15:
        raise ValueError(f'Area out of range (0..15): {address!r}')
    if line > 15:
        raise ValueError(f'Line out of range (0..15): {address!r}')
    if device > 255:
        raise ValueError(f'Device out of range (0..255): {address!r}')
    return UInt16((area << 12) + (line << 8) + device)
