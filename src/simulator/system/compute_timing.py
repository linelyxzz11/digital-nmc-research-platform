"""Draft cycle-level Compute schedule matching Compute v0.1 section 9.1.

Weight and Input SRAM have independent read ports. Read initiation intervals
model port service limits, not bank conflicts or DRAM timing. No inter-group
overlap or cross-group input reuse is assumed. Arithmetic is executed at MAC
events; no output SRAM value is visible before its WRITE_RESULT event.
"""
from dataclasses import dataclass

from simulator.functional.demo0_model import (
    CycleSignals, Demo0Tile, LANES, LocalDone, ResultBeat, TileTask,
    W_START, X_START, Y_START, int32_wrap,
)


@dataclass(frozen=True)
class ComputeTiming:
    read_latency: int = 1
    weight_read_interval: int = 1
    input_read_interval: int = 1
    mac_interval: int = 1
    result_write_cycles: int = 1

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise ValueError("all Compute timing parameters must be positive integers")

    @property
    def issue_interval(self):
        return max(self.weight_read_interval, self.input_read_interval, self.mac_interval)

    def group_cycles(self, k):
        # INIT + read fill + first MAC + remaining MAC intervals + write + handshake.
        return 1 + self.read_latency + 1 + (k - 1) * self.issue_interval + self.result_write_cycles + 1


class TimedCompute:
    def __init__(self, tile: Demo0Tile, task: TileTask, timing: ComputeTiming):
        status = tile._validate(task)
        if status != "OK":
            raise ValueError(f"invalid timed task: {status}")
        self.tile, self.task, self.timing = tile, task, timing
        self.groups = (task.m_local + LANES - 1) // LANES
        self.group = self.age = 0
        self.acc = [0] * LANES
        self.beat = None
        self.done = LocalDone(task.tid, "OK")
        self.finished = False
        self.elapsed_cycles = self.mac_cycles = self.result_stall_cycles = self.done_stall_cycles = 0
        self.useful_mac_ops = 0

    @property
    def stage(self):
        if self.finished:
            return "IDLE"
        if self.group == self.groups:
            return "LOCAL_DONE"
        if self.age == 0:
            return "INIT_ACC"
        first = 1 + self.timing.read_latency
        last = first + (self.task.k_local - 1) * self.timing.issue_interval
        if self.age < first:
            return "FETCH"
        if self.age <= last:
            return "MAC" if (self.age - first) % self.timing.issue_interval == 0 else "RESOURCE_WAIT"
        if self.age < self.timing.group_cycles(self.task.k_local) - 1:
            return "WRITE_RESULT"
        return "EMIT_RESULT"

    def signals(self):
        result_valid = self.stage == "EMIT_RESULT"
        done_valid = self.stage == "LOCAL_DONE"
        return CycleSignals(result_valid, self.beat if result_valid else None,
                            done_valid, self.done if done_valid else None)

    def step(self, *, result_ready, local_done_ready=True):
        before = self.signals()
        if self.finished:
            return before
        self.elapsed_cycles += 1
        if before.local_done_valid:
            if local_done_ready:
                self.finished = True
            else:
                self.done_stall_cycles += 1
            return before
        if before.result_valid:
            if result_ready:
                self.group += 1
                self.age = 0
                self.beat = None
            else:
                self.result_stall_cycles += 1
            return before
        if self.stage == "INIT_ACC":
            self.acc = [0] * LANES
        elif self.stage == "MAC":
            k = (self.age - 1 - self.timing.read_latency) // self.timing.issue_interval
            # Operands are immutable during a task. Reading at the modeled
            # response/MAC event is equivalent to latching the earlier request.
            x = self.tile.input_sram[self.task.x_base - X_START + k]
            x = x if x < 128 else x - 256
            address = self.task.w_base - W_START + LANES * (self.group * self.task.k_local + k)
            valid = min(LANES, self.task.m_local - LANES * self.group)
            for lane in range(valid):
                w = self.tile.weight_sram[address + lane]
                w = w if w < 128 else w - 256
                self.acc[lane] = int32_wrap(self.acc[lane] + w * x)
            self.mac_cycles += 1
            self.useful_mac_ops += valid
        if self.age == self.timing.group_cycles(self.task.k_local) - 2:
            valid = min(LANES, self.task.m_local - LANES * self.group)
            for lane in range(valid):
                offset = self.task.y_base - Y_START + 4 * (LANES * self.group + lane)
                self.tile.result_sram[offset:offset + 4] = self.acc[lane].to_bytes(4, "little", signed=True)
            self.beat = ResultBeat(self.task.tid, self.task.result_dst_tile,
                                   self.task.global_row_base + LANES * self.group,
                                   (1 << valid) - 1, tuple(self.acc), self.task.result_kind,
                                   self.task.task_last and self.group == self.groups - 1)
        self.age += 1
        return before

    def stats(self):
        return {
            "local_elapsed_cycles": self.elapsed_cycles,
            "local_no_stall_cycles": self.groups * self.timing.group_cycles(self.task.k_local) + 1,
            "mac_active_cycles": self.mac_cycles,
            "useful_mac_ops": self.useful_mac_ops,
            "lane_slot_utilization": self.useful_mac_ops / (LANES * self.elapsed_cycles),
            "weight_read_requests": self.mac_cycles,
            "input_read_requests": self.mac_cycles,
            "resource_gap_cycles": self.groups * (self.task.k_local - 1) * (self.timing.issue_interval - 1),
            "result_stall_cycles": self.result_stall_cycles,
            "done_stall_cycles": self.done_stall_cycles,
        }
