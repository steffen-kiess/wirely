from wirely import TimeOfDay
from wirely.compiler.contracts import schedule as contracts

from ..native_functions import FunctionServices, RegisteredConstantFunction, constant_function

native_functions: dict[str, RegisteredConstantFunction] = {}


@constant_function(native_functions, contracts.IsValidDailyInterval)
def is_valid_daily_interval(start: TimeOfDay, end: TimeOfDay, *, services: FunctionServices) -> bool:
    return start != end
