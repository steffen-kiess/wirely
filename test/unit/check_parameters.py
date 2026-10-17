from unittest import TestCase

from wirely.util import check_parameters


checks = TestCase()


class Text(str):
    pass


@check_parameters
def describe(name: str, count: int, *, label: str | None = None, items: list[int] = []):
    return name, count, label, items


@check_parameters
def pair(value: int | str, values: tuple[int, ...] = (), point: tuple[int, str] = (0, '')):
    return value, values, point


@check_parameters
def collect(*values: int, **options: int):
    return values, options


def check_conversion():
    # Subclasses of str, int, and bool become the exact type, also inside unions and lists.
    name, count, label, items = describe(Text('a'), True, label=Text('b'), items=[False])
    checks.assertEqual((type(name), type(count), type(label)), (str, int, str))
    checks.assertEqual(items, [0])
    checks.assertIs(type(items[0]), int)
    checks.assertIsNone(describe('a', 1)[2])


def check_unions_and_tuples():
    checks.assertEqual(pair('x'), ('x', (), (0, '')))
    checks.assertEqual(pair(1, (2, 3), (4, 'y')), (1, (2, 3), (4, 'y')))
    for arguments, name in (((1.5,), 'value'), ((1, [2]), 'values'), ((1, (), (4,)), 'point')):
        with checks.assertRaisesRegex(TypeError, f"argument '{name}' of pair"):
            pair(*arguments)


def check_errors():
    with checks.assertRaisesRegex(TypeError, "argument 'count' of describe: got str, expected int"):
        describe('a', '1')
    with checks.assertRaisesRegex(TypeError, "argument 'label' of describe"):
        describe('a', 1, label=1)
    with checks.assertRaisesRegex(TypeError, "argument 'items' of describe"):
        describe('a', 1, items=['1'])


def check_unchecked_variadics():
    checks.assertEqual(collect('a', b='c'), (('a',), {'b': 'c'}))


def check_unsupported_annotations():
    with checks.assertRaisesRegex(TypeError, 'Unsupported parameter annotation'):
        @check_parameters
        def unsupported(value: dict[str, int]):
            pass


check_conversion()
check_unions_and_tuples()
check_errors()
check_unchecked_variadics()
check_unsupported_annotations()


async def run_test():
    pass
