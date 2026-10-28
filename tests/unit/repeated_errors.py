"""Logging of repeated errors."""

from wirely.misc.repeated_errors import REMINDER_INTERVAL, RepeatedError, format_duration


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make():
    lines = []
    clock = Clock()
    return RepeatedError('Thing', output=lines.append, clock=clock), lines, clock


def expect(lines, *expected):
    assert lines == list(expected), lines
    lines.clear()


def check_suppression():
    errors, lines, clock = make()
    errors.report('broken')
    expect(lines, 'Warning: Thing failed: broken')
    errors.report('broken')
    expect(lines, 'Warning: Thing failed: broken (further identical errors are suppressed)')
    clock.now += 60
    errors.report('broken')
    expect(lines)

    # A reminder once per interval after the last logged line.
    clock.now += REMINDER_INTERVAL - 60
    errors.report('broken')
    expect(lines, 'Warning: Thing is still failing: broken (4 times in 1 day)')
    clock.now += REMINDER_INTERVAL - 1
    errors.report('broken')
    expect(lines)

    clock.now += 10 * 3600
    errors.end()
    expect(lines, 'Thing had failed 5 times in 2 days 9 h with: broken')
    errors.end()
    expect(lines)


def check_changes():
    errors, lines, clock = make()
    for _ in range(3):
        errors.report('first')
    lines.clear()
    clock.now += 45
    errors.report('second')
    expect(lines, 'Thing had failed 3 times in 45 s with: first', 'Warning: Thing failed: second')

    # Nothing was suppressed, so there is no summary.
    errors.report('second')
    errors.end()
    expect(lines, 'Warning: Thing failed: second (further identical errors are suppressed)')
    errors.report('second')
    expect(lines, 'Warning: Thing failed: second')


def check_durations():
    assert [format_duration(seconds) for seconds in (0, 45.9, 61, 3600, 3 * 3600 + 12 * 60 + 5, 86400 + 10 * 3600,
                                                     2 * 86400 + 30)] == [
        '0 s', '45 s', '1 min 1 s', '1 h', '3 h 12 min', '1 day 10 h', '2 days']


async def run_test():
    check_suppression()
    check_changes()
    check_durations()
