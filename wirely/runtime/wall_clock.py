"""Wall-clock requests and event-loop waiting."""

import asyncio
import ctypes
import datetime
import errno
import os
import sys
import sysconfig
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WallWakeup:
    start: datetime.datetime | None = None
    end: datetime.datetime | None = None
    utc_offset: datetime.timedelta | None = None

    def is_due(self, now: datetime.datetime | None) -> bool:
        if self.start is None:
            return now is not None
        if now is None or (self.utc_offset is not None and now.utcoffset() != self.utc_offset):
            return True
        utc = now.astimezone(datetime.timezone.utc)
        assert self.end is not None
        return not self.start <= utc < self.end


class _Timespec(ctypes.Structure):
    _fields_ = [('seconds', ctypes.c_long), ('nanoseconds', ctypes.c_long)]


class _Itimerspec(ctypes.Structure):
    _fields_ = [('interval', _Timespec), ('initial', _Timespec)]


class TimerFD:
    """An absolute, cancel-on-clock-change realtime timer."""

    def __init__(self, *, use_libc: bool = False) -> None:
        self._read: Callable[[int, int], bytes] = os.read
        self._settime: Any = None
        create = getattr(os, 'timerfd_create', None)
        self._settime_ns = getattr(os, 'timerfd_settime_ns', None)
        if not use_libc and create is not None and self._settime_ns is not None:
            self.fd: int = create(time.CLOCK_REALTIME, flags=os.O_NONBLOCK | os.O_CLOEXEC)
            return
        # Only use the compatibility binding with a verified native ABI.
        if (sys.platform != 'linux' or ctypes.sizeof(ctypes.c_long) != 8
                or sysconfig.get_config_var('SIZEOF_TIME_T') != 8):
            raise NotImplementedError('No compatible timerfd binding')
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            create = libc.timerfd_create
            self._settime = libc.timerfd_settime
        except AttributeError as error:
            raise NotImplementedError('timerfd is unavailable') from error
        create.argtypes = [ctypes.c_int, ctypes.c_int]
        create.restype = ctypes.c_int
        self._settime.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(_Itimerspec), ctypes.c_void_p]
        self._settime.restype = ctypes.c_int
        self.fd = create(time.CLOCK_REALTIME, os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        self._settime_ns = None

    def arm(self, deadline: datetime.datetime) -> None:
        delta = deadline - datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
        nanoseconds = ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000
        if not 0 < nanoseconds <= 2**63 - 1:
            raise NotImplementedError('Deadline is outside the timerfd range')
        flags = 1 | 2  # TFD_TIMER_ABSTIME | TFD_TIMER_CANCEL_ON_SET
        if self._settime_ns is not None:
            self._settime_ns(self.fd, flags=flags, initial=nanoseconds)
        else:
            seconds, remainder = divmod(nanoseconds, 1_000_000_000)
            value = _Itimerspec(_Timespec(0, 0), _Timespec(seconds, remainder))
            if self._settime(self.fd, flags, ctypes.byref(value), None) < 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code))

    def consume(self) -> bool:
        try:
            self._read(self.fd, 8)
        except OSError as error:
            if error.errno == errno.EAGAIN:
                return False
            if error.errno != errno.ECANCELED:
                raise
        return True

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


class WallClockWaiter:
    """Wake an engine for wall deadlines, clock jumps, and offset changes."""

    def __init__(self, get_time: Callable[[], datetime.datetime | None], wake: Callable[[], None],
                 *, timer_factory: Callable[[], TimerFD] = TimerFD) -> None:
        self._loop = asyncio.get_running_loop()
        self._get_time = get_time
        self._wake = wake
        self._timer_factory = timer_factory
        self._timer: TimerFD | None = None
        self._poll_handle: asyncio.TimerHandle | None = None
        self._offset: datetime.timedelta | None = None
        self._fallback = False

    def arm(self, deadline: datetime.datetime | None, *, real: bool,
            now: datetime.datetime | None, monitor_offset: bool) -> None:
        self.close()
        if not real:
            return
        self._offset = None if now is None else now.utcoffset()
        self._fallback = True
        if deadline is not None:
            try:
                self._timer = self._timer_factory()
                self._timer.arm(deadline)
                self._loop.add_reader(self._timer.fd, self._ready)
                self._fallback = False
            except (NotImplementedError, OSError) as error:
                if isinstance(error, OSError) and error.errno == errno.ECANCELED:
                    self._wake()
                elif isinstance(error, OSError) and error.errno not in (
                        errno.ENOSYS, errno.EINVAL, errno.EPERM, errno.ENOTSUP):
                    self.close()
                    raise
                self.close()
        if self._fallback or monitor_offset:
            self._poll_handle = self._loop.call_later(1, self._poll)

    def _ready(self) -> None:
        assert self._timer is not None
        if self._timer.consume():
            self._loop.remove_reader(self._timer.fd)
            self._wake()

    def _poll(self) -> None:
        now = self._get_time()
        offset = None if now is None else now.utcoffset()
        if self._fallback or offset != self._offset:
            self._offset = offset
            self._wake()
        self._poll_handle = self._loop.call_later(1, self._poll)

    def close(self) -> None:
        if self._poll_handle is not None:
            self._poll_handle.cancel()
            self._poll_handle = None
        if self._timer is not None:
            self._loop.remove_reader(self._timer.fd)
            self._timer.close()
            self._timer = None
