"""Validate MQTT connection settings, at compile time and when loading."""

import dataclasses
import urllib.parse
from typing import Literal

from wirely.types.time import TICKS_PER_SECOND, Duration


def keep_alive_seconds(keep_alive: Duration) -> int:
    """Round the keepalive interval up to whole seconds, as MQTT requires."""
    if keep_alive.ticks == 0:
        raise ValueError('MQTT keepalive must not be zero')
    seconds = -(-keep_alive.ticks // TICKS_PER_SECOND)
    if seconds > 65535:
        raise ValueError(f'MQTT keepalive {keep_alive.to_iso()} is longer than 65535 seconds')
    return seconds


class MQTTUriParseException(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class ParsedMQTTUri:
    transport: Literal['tcp', 'unix']
    use_tls : bool
    hostname: str
    port: int


def parse_uri(uri: str) -> ParsedMQTTUri:
    split = urllib.parse.urlsplit(uri)
    if split.scheme == '':
        raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Not a URI (scheme is missing)'.format(uri))
    scheme = split.scheme.lower()

    if split.fragment != '':
        raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got a non-empty fragment'.format(uri))
    query = urllib.parse.parse_qs(split.query, keep_blank_values=True, errors='strict', strict_parsing=False)

    def check_query(*allowed_keys: str) -> None:
        allowed = set(allowed_keys)
        for key in query:
            if key not in allowed:
                raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got unknown parameter {!r}'.format(uri, key))

    def parse_path(path: str) -> str | None:
        if path == '':
            return None
        if not path.startswith('/'):
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Path is not absolute'.format(uri))
        res = ''
        for component in path[1:].split('/'):
            if ';' in component:
                raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got semicolon in path'.format(uri))
            unq = urllib.parse.unquote(component, errors='strict')
            if '\0' in unq:
                raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got encoded NUL in path'.format(uri))
            if '/' in unq:
                raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got encoded slash in path'.format(uri))
            res += '/' + unq
        return res

    if scheme in ['mqtt', 'mqtts']:
        if scheme == 'mqtt':
            use_tls = False
        elif scheme == 'mqtts':
            use_tls = True
        else:
            raise Exception('Should not be reached')

        if split.path != '':
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got a non-empty path'.format(uri))
        if split.netloc == '':
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Authority is missing'.format(uri))
        if split.username is not None:
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got a non-empty username'.format(uri))
        if split.password is not None:
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got a non-empty password'.format(uri))
        hostname = split.hostname
        if hostname is None:
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got no hostname'.format(uri))
        port = split.port
        if port is None:
            if use_tls:
                port = 8883
            else:
                port = 1883

        check_query()

        return ParsedMQTTUri(
            transport='tcp',
            use_tls=use_tls,
            hostname=hostname,
            port=port,
        )
    elif scheme in ['mqtt+unix']:
        path = parse_path(split.path)
        if path is None:
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Missing path'.format(uri))

        check_query()

        if split.netloc != '':
            raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Got non-empty authority'.format(uri))

        return ParsedMQTTUri(
            transport='unix',
            use_tls=False,
            hostname=path,
            # Note: paho.mqtt does not like it if 0 is passed here
            #port=0,
            port=1,
        )
    else:
        raise MQTTUriParseException('Unable to parse MQTT URI {!r}: Unknown scheme {!r}'.format(uri, scheme))


def check_client_id(client_id: str) -> None:
    if client_id == '':
        raise ValueError('MQTT client ID must not be empty')
