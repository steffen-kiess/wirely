import dataclasses

import wirely as blocks
from wirely.native import native_block_type
from wirely.native.functions import native_function
from wirely.runtime.contexts import NativeState

native_block_types = {}
native_functions = {}


@native_function(native_functions, 'Clamp')
def clamp(*, context):
    arguments = context.arguments
    return min(max(arguments['value'], arguments['low']), arguments['high'])


@native_function(native_functions, 'Nothing')
def nothing(*, context):
    return blocks.Unit()


@native_function(native_functions, 'Fail')
def fail(*, context):
    raise RuntimeError('Fail always fails')


@native_block_type(native_block_types, has_native_state=False)
class TestBlock(NativeState):
    @staticmethod
    def test_procedure(*, context):
        pass
