from tests.infra.native_extension.generated import test_library as contracts
from wirely.runtime.native_api import ProcedureServices





class TestNodeTestProcedure(contracts.TestNodeTestProcedure):
    def run(self, *, services: ProcedureServices) -> contracts.TestNodeTestProcedureResult:
        return contracts.TestNodeTestProcedureResult()
