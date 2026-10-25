from wirely.control import diagnostic


def check_round_trips():
    cases = {
        '0': 0,
        '-24': -24,
        "h''": b'',
        "h'2A 00\tff'": b'\x2a\x00\xff',
        '"a\\"b\\\\c\\n\\u00e9"': 'a"b\\c\né',
        'true': True,
        'false': False,
        'null': None,
        '[]': [],
        '{}': {},
        ' [1, [true, null], {1: h\'01\', "key": [-1]}] ': [1, [True, None], {1: b'\x01', 'key': [-1]}],
    }
    for text, value in cases.items():
        assert diagnostic.parse(text) == value, text
        assert diagnostic.parse(diagnostic.format(value)) == value, text
    assert diagnostic.format([b'\x07\x00', 'é', {1: None}]) == "[h'0700', \"é\", {1: null}]"


def check_errors():
    for text in ['', '1 2', 'one', "h'abc'", '"open', '[1 2]', '{1 2}', '[1,]', '{1: }', '1.5']:
        try:
            diagnostic.parse(text)
        except ValueError:
            continue
        raise AssertionError(f'Accepted {text!r}')


async def run_test():
    check_round_trips()
    check_errors()
