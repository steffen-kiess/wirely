"""Log errors that repeat, such as failing connection attempts, without flooding the log."""

import time
from collections.abc import Callable


# How long identical errors stay suppressed before a reminder is logged, in seconds.
REMINDER_INTERVAL = 24 * 60 * 60.0


_UNITS = ((24 * 60 * 60, 'day', 'days'), (60 * 60, 'h', 'h'), (60, 'min', 'min'), (1, 's', 's'))


def format_duration(seconds: float) -> str:
    """Format a duration with its largest unit and the next one if not zero, such as `3 h 12 min`."""
    remaining = int(seconds)
    amounts = []
    for size, singular, plural in _UNITS:
        amount, remaining = divmod(remaining, size)
        amounts.append((amount, singular if amount == 1 else plural))
    first = next((index for index, (amount, _) in enumerate(amounts) if amount), len(amounts) - 1)
    parts = [f'{amounts[first][0]} {amounts[first][1]}']
    if first + 1 < len(amounts) and amounts[first + 1][0]:
        parts.append(f'{amounts[first + 1][0]} {amounts[first + 1][1]}')
    return ' '.join(parts)


class RepeatedError:
    """The errors of one subject, such as one connection.

    The first two occurrences of an error are logged, identical ones after
    that only once per REMINDER_INTERVAL. When the error ends, by end() or
    by a different error, a summary counts the occurrences.
    """

    def __init__(self, subject: str, *, output: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.subject = subject
        self._output = output
        self._clock = clock
        self._error: str | None = None
        self._count = 0
        self._start = 0.0
        self._last_printed = 0.0

    def _print(self, text: str) -> None:
        if self._output is None:
            print(text, flush=True)
        else:
            self._output(text)

    def report(self, error: str) -> None:
        """Record an occurrence of an error."""
        if error != self._error:
            self.end()
            self._error = error
            self._start = self._clock()
        self._count += 1
        now = self._clock()
        if self._count == 1:
            self._print(f'Warning: {self.subject} failed: {error}')
        elif self._count == 2:
            self._print(f'Warning: {self.subject} failed: {error} (further identical errors are suppressed)')
        elif now - self._last_printed >= REMINDER_INTERVAL:
            self._print(f'Warning: {self.subject} is still failing: {error} '
                        f'({self._count} times in {format_duration(now - self._start)})')
        else:
            return
        self._last_printed = now

    def end(self) -> None:
        """Record that the error has ended; summarize it if identical errors were suppressed."""
        if self._error is not None and self._count > 2:
            self._print(f'{self.subject} had failed {self._count} times in '
                        f'{format_duration(self._clock() - self._start)} with: {self._error}')
        self._error = None
        self._count = 0
