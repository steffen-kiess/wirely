from wirely.contracts import test_library as contracts
from wirely.native import native_node_type, native_procedure
from wirely.runtime.native_api import ProcedureServices

native_node_types: dict[str, type] = {}
native_resource_types: dict[str, type] = {}


@native_node_type(native_node_types, has_native_state=False)
class TestNode:
    @native_procedure('test_procedure')
    class TestProcedure(contracts.TestNodeTestProcedure):
        def run(self, *, services: ProcedureServices) -> contracts.TestNodeTestProcedureResult:
            return contracts.TestNodeTestProcedureResult()
