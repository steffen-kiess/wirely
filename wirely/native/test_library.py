import dataclasses

from wirely.native import native_node_type
from wirely.runtime.contexts import NativeState

native_node_types = {}
native_resource_types = {}


@native_node_type(native_node_types, has_native_state=False)
class TestNode(NativeState):
    @staticmethod
    def test_procedure(*, context):
        pass
