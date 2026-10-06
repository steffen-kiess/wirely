import dataclasses

from wirely.native import native_block_type
from wirely.runtime.contexts import NativeState

native_block_types = {}


@native_block_type(native_block_types, has_native_state=False)
class TestBlock(NativeState):
    @staticmethod
    def test_procedure(*, context):
        pass
