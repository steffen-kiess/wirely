from ..native_functions import NativeFunctionContext


def is_valid_daily_interval(*, context: NativeFunctionContext) -> bool:
    start, end = context.arguments
    return start != end
