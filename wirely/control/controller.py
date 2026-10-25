"""Control operations on a running engine."""

import collections
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..runtime.engine import Engine

from .messages import (
    BUILD_ID_LENGTH, BUILD_ID_PREFIX_LENGTH, Bank, ControlError, ErrorCode, Op, RunState, get_field,
)

MAX_SNAPSHOTS = 4


class RuntimeController:
    """Handles control requests other than Hello for one engine."""

    supported_ops = (
        Op.HELLO, Op.STATUS, Op.PAUSE, Op.RESUME, Op.STEP, Op.BIND_PROGRAM,
        Op.SNAPSHOT, Op.READ_SNAPSHOT, Op.RELEASE_SNAPSHOT,
    )
    # Operations that refer to program entities and require a bound session.
    bound_ops = (Op.SNAPSHOT,)

    def __init__(self, engine: 'Engine', build_id: bytes) -> None:
        if len(build_id) != BUILD_ID_LENGTH:
            raise ValueError(f'Build IDs have {BUILD_ID_LENGTH} bytes')
        self.engine = engine
        self.build_id = build_id
        # Counts program activations; a session is bound while its binding has the current count.
        self.activation = 0
        self._snapshots: collections.OrderedDict[int, tuple[bytes, ...]] = collections.OrderedDict()  # Least recently used first.
        self._next_snapshot_id = 1

    def handle(self, op: int, body: Any, *, bound: bool) -> Any:
        """Run an operation other than BindProgram and return its response body."""
        handler = self._handlers.get(op)
        if handler is None:
            raise ControlError(ErrorCode.UNKNOWN_OP)
        if op in self.bound_ops and not bound:
            raise ControlError(ErrorCode.NOT_BOUND, 'Send BindProgram first')
        return handler(self, body)

    def bind(self, body: Any) -> tuple[dict[int, Any], int]:
        """Check a BindProgram request; return its response body and the activation to bind to."""
        prefix = get_field(body, 1, bytes)
        if prefix is not None and len(prefix) > BUILD_ID_LENGTH:
            raise ControlError(ErrorCode.INVALID_ARGUMENT, f'Build IDs have {BUILD_ID_LENGTH} bytes')
        if prefix is None:
            return {1: self.build_id[:BUILD_ID_PREFIX_LENGTH]}, self.activation
        if not self.build_id.startswith(prefix):
            raise ControlError(ErrorCode.NOT_BOUND, 'Another program is active')
        return {}, self.activation

    def _status(self, body: Any) -> dict[int, Any]:
        run_state = RunState.PAUSED if self.engine.paused else RunState.RUNNING
        return {1: int(run_state), 2: self.engine.step_count}

    def _pause(self, body: Any) -> dict[int, Any]:
        # Requests run between steps, so no step is running here.
        self.engine.pause()
        return self._status(body)

    def _resume(self, body: Any) -> dict[int, Any]:
        self.engine.resume()
        return self._status(body)

    def _step(self, body: Any) -> dict[int, Any]:
        count = get_field(body, 1, int, default=1)
        if not self.engine.paused:
            raise ControlError(ErrorCode.WRONG_STATE, 'Runtime is not paused')
        procedures = 0
        for _ in range(count):
            run_count, _, _ = self.engine.step()
            procedures += run_count
        return {1: count, 2: procedures}

    def _snapshot(self, body: Any) -> dict[int, Any]:
        engine = self.engine
        banks = (bytes(engine.state.data), b'' if engine.cache is None else bytes(engine.cache.data))
        snapshot_id = self._next_snapshot_id
        self._next_snapshot_id += 1
        self._snapshots[snapshot_id] = banks
        while len(self._snapshots) > MAX_SNAPSHOTS:
            self._snapshots.popitem(last=False)
        return {1: snapshot_id, 2: engine.step_count, 3: [len(bank) for bank in banks]}

    def _get_snapshot(self, body: Any) -> tuple[int, tuple[bytes, ...]]:
        snapshot_id = get_field(body, 1, int, required=True)
        try:
            banks = self._snapshots[snapshot_id]
        except KeyError:
            raise ControlError(ErrorCode.NOT_FOUND, f'Unknown snapshot {snapshot_id}') from None
        return snapshot_id, banks

    def _read_snapshot(self, body: Any) -> dict[int, Any]:
        snapshot_id, banks = self._get_snapshot(body)
        bank = get_field(body, 2, int, required=True)
        ranges = get_field(body, 3, list, required=True)
        if bank not in tuple(Bank):
            raise ControlError(ErrorCode.INVALID_ARGUMENT, f'Unknown bank {bank}')
        data = banks[bank]
        result = bytearray()
        for item in ranges:
            if (not isinstance(item, list) or len(item) != 2
                    or not all(type(value) is int and value >= 0 for value in item)):
                raise ControlError(ErrorCode.INVALID_ARGUMENT, 'Ranges must be [offset, length] pairs')
            offset, length = item
            if offset + length > len(data):
                raise ControlError(ErrorCode.INVALID_ARGUMENT, f'Range {offset}+{length} exceeds bank size')
            result += data[offset:offset + length]
        self._snapshots.move_to_end(snapshot_id)
        return {1: bytes(result)}

    def _release_snapshot(self, body: Any) -> dict[int, Any]:
        snapshot_id, _ = self._get_snapshot(body)
        del self._snapshots[snapshot_id]
        return {}

    _handlers: 'dict[int, Callable[[RuntimeController, Any], Any]]' = {
        Op.STATUS: _status,
        Op.PAUSE: _pause,
        Op.RESUME: _resume,
        Op.STEP: _step,
        Op.SNAPSHOT: _snapshot,
        Op.READ_SNAPSHOT: _read_snapshot,
        Op.RELEASE_SNAPSHOT: _release_snapshot,
    }
