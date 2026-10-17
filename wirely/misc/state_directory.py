"""Exclusive use of a runtime state directory."""

import contextlib
import errno
import fcntl
import os
import struct
import sys
from collections.abc import Iterator

LOCK_FILE_NAME = 'lock'
# Python 3.11 lacks the constant; Linux uses this value on all architectures.
F_OFD_SETLK = getattr(fcntl, 'F_OFD_SETLK', 37 if sys.platform == 'linux' else None)


class StateDirectoryLocked(Exception):
    pass


@contextlib.contextmanager
def lock_state_directory(path: str) -> Iterator[None]:
    """Create a state directory if needed and hold its lock, failing if another runtime holds it."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    fd = _lock_file(os.path.join(path, LOCK_FILE_NAME))
    try:
        yield
    finally:
        # Keep the lock file; removing it would race with a starting runtime.
        os.close(fd)


def _lock_file(path: str) -> int:
    """Open a file and hold a write lock on all of it, failing if it is locked."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        if F_OFD_SETLK is not None:
            # Locks of the open file description, unlike lockf(), survive closing
            # other descriptors of the file. struct flock: type, whence, start,
            # length (0 = to the end), pid (must be 0).
            flock = struct.pack('hhqqi', fcntl.F_WRLCK, os.SEEK_SET, 0, 0, 0)
            fcntl.fcntl(fd, F_OFD_SETLK, flock)
        else:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException as error:
        os.close(fd)
        if isinstance(error, OSError) and error.errno in (errno.EAGAIN, errno.EACCES):
            raise StateDirectoryLocked(
                f'State directory {os.path.dirname(path)} is in use by another runtime') from None
        raise
    return fd
