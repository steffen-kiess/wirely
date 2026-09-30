import dataclasses

import wirely as blocks
from wirely.native import native_block_type, native_procedure

native_block_types = {}


@native_block_type(native_block_types, has_state=False)
class TestBlock(blocks.NativeState):
    @staticmethod
    @native_procedure
    def test_procedure(self, *, context):
        pass
