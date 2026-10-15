def is_valid_daily_interval(*, context):
    start, end = context.arguments
    return start != end
