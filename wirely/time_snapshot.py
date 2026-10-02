"""Clock values captured at one execution boundary."""

import datetime
from dataclasses import dataclass

from .time import MonotonicTime


@dataclass(frozen=True, kw_only=True)
class TimeSnapshot:
    monotonic: MonotonicTime
    utc: datetime.datetime
    local: datetime.datetime
