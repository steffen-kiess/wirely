from wirely import TimeOfDay

from ..native_functions import FunctionServices


def is_valid_daily_interval(start: TimeOfDay, end: TimeOfDay, *, services: FunctionServices) -> bool:
    return start != end
