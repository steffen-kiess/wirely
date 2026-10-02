import dataclasses

import wirely as blocks
from wirely.native import native_block_type

native_block_types = {}


@native_block_type(native_block_types, has_native_state=False)
class TestBlock(blocks.NativeState):
    @staticmethod
    def test_procedure(*, context):
        pass
