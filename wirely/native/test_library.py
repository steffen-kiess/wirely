from wirely.contracts import test_library as contracts
from wirely.runtime.native_api import ProcedureServices


class TestNode:

    class TestProcedure(contracts.TestNodeTestProcedure):
        def run(self, *, services: ProcedureServices) -> contracts.TestNodeTestProcedureResult:
            return contracts.TestNodeTestProcedureResult()
