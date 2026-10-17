import dataclasses

from wirely.native import native_node_type, native_procedure
from wirely.runtime.contexts import NativeProcedure, NativeState

native_node_types = {}
native_resource_types = {}


@native_node_type(native_node_types, has_native_state=False)
class TestNode(NativeState):
    @native_procedure('test_procedure')
    class TestProcedure(NativeProcedure):
        def __call__(self, *, context):
            pass
