"""A subset of CBOR diagnostic notation (RFC 8949, section 8) for message values.

Supported are integers, byte strings (h'...'), text strings with JSON escapes, true, false,
null, arrays, and maps.
"""

import json
import re
from typing import Any

_TOKEN = re.compile(r'''\s*(?:
    (?P<int>-?\d+)
  | h'(?P<hex>[0-9a-fA-F\s]*)'
  | (?P<text>"(?:[^"\\]|\\.)*")
  | (?P<word>true|false|null)
  | (?P<punct>[\[\]{},:])
)''', re.VERBOSE)

_WORDS = {'true': True, 'false': False, 'null': None}


def parse(text: str) -> Any:
    """Parse one value, raising ValueError with the position of an error."""
    parser = _Parser(text)
    value = parser.value()
    parser.skip_space()
    if parser.position != len(text):
        raise ValueError(f'Unexpected text at position {parser.position}')
    return value


def format(value: Any) -> str:
    """Return a value in diagnostic notation."""
    if value is None or isinstance(value, bool):
        return {None: 'null', True: 'true', False: 'false'}[value]
    if isinstance(value, int):
        return str(value)
    if isinstance(value, bytes):
        return f"h'{value.hex()}'"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return '[' + ', '.join(format(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{' + ', '.join(f'{format(key)}: {format(item)}' for key, item in value.items()) + '}'
    raise ValueError(f'Cannot format {type(value).__name__}')


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.position = 0

    def skip_space(self) -> None:
        while self.position < len(self.text) and self.text[self.position].isspace():
            self.position += 1

    def token(self) -> re.Match[str]:
        match = _TOKEN.match(self.text, self.position)
        if match is None or match.end() == self.position:
            self.skip_space()
            raise ValueError(f'Unexpected text at position {self.position}')
        self.position = match.end()
        return match

    def expect(self, punctuation: str) -> None:
        start = self.position
        if self.token()['punct'] != punctuation:
            self.position = start
            self.skip_space()
            raise ValueError(f'Expected {punctuation!r} at position {self.position}')

    def peek(self, punctuation: str) -> bool:
        self.skip_space()
        return self.text.startswith(punctuation, self.position)

    def value(self) -> Any:
        self.skip_space()
        start = self.position
        match = self.token()
        if match['int'] is not None:
            return int(match['int'])
        if match['hex'] is not None:
            digits = ''.join(match['hex'].split())
            if len(digits) % 2:
                raise ValueError(f'Odd number of hex digits at position {start}')
            return bytes.fromhex(digits)
        if match['text'] is not None:
            try:
                return json.loads(match['text'])
            except json.JSONDecodeError as error:
                raise ValueError(f'Invalid text string at position {start}: {error.msg}') from None
        if match['word'] is not None:
            return _WORDS[match['word']]
        if match['punct'] == '[':
            items: list[Any] = []
            while not self.peek(']'):
                if items:
                    self.expect(',')
                items.append(self.value())
            self.expect(']')
            return items
        if match['punct'] == '{':
            entries: dict[Any, Any] = {}
            while not self.peek('}'):
                if entries:
                    self.expect(',')
                key = self.value()
                self.expect(':')
                entries[key] = self.value()
            self.expect('}')
            return entries
        raise ValueError(f'Unexpected {match["punct"]!r} at position {start}')
