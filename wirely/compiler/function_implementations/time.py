def is_valid_daily_interval(*, context):
    return context.arguments['start'] != context.arguments['end']
