import datetime
import re


def add_wrap(*, context):
    time = context.arguments['time']
    offset = context.arguments['offset']
    parsed = re.fullmatch(r'(\+|-|)([0-9]+):([0-9]+)', offset)
    if not parsed:
        raise context.error(f'Cannot parse time delta {offset!r}', argument='offset')
    sign = -1 if parsed[1] == '-' else 1
    delta = datetime.timedelta(hours=int(parsed[2]), minutes=int(parsed[3])) * sign
    time_delta = datetime.datetime.combine(datetime.date.min, time) - datetime.datetime.min
    return (datetime.datetime.min + (time_delta + delta) % datetime.timedelta(days=1)).time()
