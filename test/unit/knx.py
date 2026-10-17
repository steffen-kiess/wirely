import subprocess
import sys
from unittest import TestCase

import wirely
from wirely import value_types
from wirely.knx.address import parse_group_address
from wirely.knx.value_types import get_value_type


checks = TestCase()


def check_conversions():
    binary = get_value_type(value_types.boolean)
    for value in (False, True):
        checks.assertEqual(binary.from_knx(value), value_types.boolean.from_native(value))
        checks.assertIs(binary.to_knx(binary.from_knx(value)), value)
    for value in (None, 0, 1, 'true'):
        with checks.assertRaises(ValueError):
            binary.from_knx(value)

    for descriptor, cls in ((value_types.int16, wirely.Int16), (value_types.uint16, wirely.UInt16),
                            (value_types.int32, wirely.Int32), (value_types.uint32, wirely.UInt32)):
        entry = get_value_type(descriptor)
        for value in (cls.min_value, 0, cls.max_value):
            dynamic = entry.from_knx(value)
            checks.assertEqual(dynamic, descriptor.from_native(cls(value)))
            checks.assertEqual(entry.to_knx(dynamic), value)
            checks.assertIs(type(entry.to_knx(dynamic)), int)
        for value in (cls.min_value - 1, cls.max_value + 1, True, False, 1.0, '1', None):
            with checks.assertRaises(ValueError):
                entry.from_knx(value)
        with checks.assertRaises(ValueError):
            entry.to_knx(value_types.boolean.from_native(True))
    with checks.assertRaises(ValueError):
        binary.to_knx(value_types.uint16.from_native(wirely.UInt16(1)))
    with checks.assertRaisesRegex(ValueError, 'Unsupported type for KNX'):
        get_value_type(value_types.string)
    with checks.assertRaisesRegex(ValueError, 'Expected KNX type'):
        get_value_type(value_types.uint16, 'binary')


def check_addresses():
    for text, raw in (('0', 0), ('65535', 65535), ('00001', 1), ('0/0', 0), ('31/2047', 65535),
                      ('0/0/0', 0), ('31/7/255', 65535), ('1/2/3', 2563), ('01/02/0003', 2563)):
        checks.assertEqual(parse_group_address(text), wirely.UInt16(raw))
    for text in ('', '-1', '65536', '1/2/3/4', '32/0/0', '1/8/0', '1/2/256', '1/2048',
                 ' 1', '1 ', '+1', '1//2', '1/2/', '001/2/3'):
        with checks.assertRaises(ValueError):
            parse_group_address(text)


def check_import_boundaries():
    subprocess.run([sys.executable, '-c', '''
import builtins
import sys
original_import = builtins.__import__
def checked_import(name, *args, **kwargs):
    if name == 'xknx' or name.startswith('xknx.'):
        raise AssertionError('Unexpected xknx import: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = checked_import
import wirely.knx.address
import wirely.knx.value_types
assert not any(name == 'wirely.compiler' or name.startswith('wirely.compiler.') for name in sys.modules)
from pathlib import Path
from wirely.compiler import _standard_library_sources, compile_strs
source = Path('test/compiler/knx.wrl')
assert compile_strs(_standard_library_sources(['std', 'knx']) + [(str(source), source.read_text())]) is not None
'''], check=True)


check_conversions()
check_addresses()
check_import_boundaries()


async def run_test():
    pass
