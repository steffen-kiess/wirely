from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from ..program import RunStage, Variable, VariableTable
from ..types.time import MonotonicTime
from .time_snapshot import TimeSnapshot

if TYPE_CHECKING:
    from .engine import Engine


class VariableTableInstance:
    """Write-once runtime values stored in an aligned byte buffer."""

    def __init__(self, variable_table: VariableTable) -> None:
        """Allocate zeroed storage and track initialization separately."""
        if not isinstance(variable_table, VariableTable):
            raise Exception('variable_table parameter is not a VariableTable')
        self.variable_table = variable_table

        self.data = bytearray(variable_table.size)
        self._initialized: set[Variable] = set()  # Variable identities, including zero-sized units.
        last = next(reversed(variable_table.offsets), None)
        # Final table padding follows the last slot in layout order.
        self._padding_start = 0 if last is None else variable_table.offsets[last] + last.var_type.size

    def add(self, variable: Variable, value: object) -> None:
        """Serialize a value into its slot, allowing one successful write."""
        if variable not in self.variable_table.variable_set:
            raise Exception(f'Variable {variable!r} not in VariableTable {self.variable_table.name}')
        if variable in self._initialized:
            raise Exception(f'Variable {variable} already set')
        encoded = variable.var_type.serialize(value)
        offset = self.variable_table.offsets[variable]
        self.data[offset:offset + variable.var_type.size] = (
            encoded + bytes(variable.var_type.size - len(encoded))
        )
        self._initialized.add(variable)

    def __getitem__(self, variable: Variable) -> object:
        """Read an initialized value, validating its slot and table padding."""
        if variable not in self.variable_table.variable_set:
            raise Exception(f'Variable {variable!r} not in VariableTable {self.variable_table.name}')
        if variable not in self._initialized:
            raise Exception(f'Variable {variable} not yet set')
        offset = self.variable_table.offsets[variable]
        end = offset + variable.var_type.data_size
        if any(self.data[end:offset + variable.var_type.size]) or any(self.data[self._padding_start:]):
            raise ValueError(f'Non-zero padding in VariableTable {self.variable_table.name}')
        return variable.var_type.deserialize(memoryview(self.data)[offset:end])


class StepContext:
    def __init__(self) -> None:
        self._stage_times: dict[RunStage, TimeSnapshot] = {}
        self.stage_start_time_monotonic: dict[RunStage, MonotonicTime] = {}
        self.stage_start_time_utc: dict[RunStage, datetime.datetime] = {}
        self.stage_start_time: dict[RunStage, datetime.datetime] = {}

    def start_stage(self, engine: Engine, stage: RunStage) -> None:
        if stage in self.stage_start_time:
            raise Exception(f'Stage {stage} already started')

        now_monotonic = engine.get_monotonic_time()
        # print(f'StepContext for {stage} started at {now_monotonic}')

        now = engine.get_wall_time()
        now_utc = now.astimezone(datetime.timezone.utc)

        self._stage_times[stage] = TimeSnapshot(
            monotonic=now_monotonic, utc=now_utc, local=now,
        )
        self.stage_start_time_monotonic[stage] = now_monotonic
        self.stage_start_time_utc[stage] = now_utc
        self.stage_start_time[stage] = now

    def __repr__(self) -> str:
        return f'StepContext({self.__dict__!r})'
