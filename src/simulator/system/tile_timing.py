"""Request/response single-Tile Compute with explicit SRAM bank arbitration.

The default ports realize Compute v0.1 section 9.1. Alternate bank sharing,
bandwidth, latency and operand buffering are explicit sensitivity assumptions.
No NoC, host transfers, endpoint reduction or RTL calibration is included.
"""
from dataclasses import asdict

from simulator.functional.demo0_model import (
    CycleSignals, Demo0Tile, LANES, LocalDone, ResultBeat, TileTask,
    int32_wrap,
)
from simulator.system.tile_memory import SramRequest, TileMemoryConfig, TileMemoryPorts


class PortScheduledCompute:
    """Two operand-pair slots by default; one registered 4-Lane MAC update/cycle.

    step() returns pre-edge ready/valid signals and applies the edge. A read
    granted at cycle c can first feed the MAC at c + latency. Both W and X
    must be present, matched by (group,k). A pair slot is freed after its MAC;
    without combinational lookahead it is reusable on the following cycle.
    """

    def __init__(self, tile: Demo0Tile, task: TileTask, memory=TileMemoryConfig(), *,
                 operand_slots=2, mac_interval=1):
        if tile._validate(task) != "OK":
            raise ValueError(f"invalid timed task: {tile._validate(task)}")
        if any(type(v) is not int or v < 1 for v in (operand_slots, mac_interval)):
            raise ValueError("operand_slots and mac_interval must be positive integers")
        self.tile, self.task = tile, task
        self.memory = TileMemoryPorts(tile, memory)
        self.operand_slots, self.mac_interval = operand_slots, mac_interval
        self.groups = (task.m_local + LANES - 1) // LANES
        self.group = self.issue_k = self.consume_k = self.next_mac_cycle = 0
        self.stage, self.finished, self.beat = "INIT_ACC", False, None
        self.done = LocalDone(task.tid, "OK")
        self.acc, self.operands, self.written = [0] * LANES, {}, set()
        self.counters = dict(mac_active_cycles=0, useful_mac_ops=0, operand_wait_cycles=0,
                             mac_interval_wait_cycles=0, result_stall_cycles=0,
                             done_stall_cycles=0, max_operand_slots_used=0)
        self.stage_cycles = {s: 0 for s in ("INIT_ACC", "RUN", "WRITE_RESULT", "EMIT_RESULT", "LOCAL_DONE")}
        self.last_event = None

    def signals(self):
        result, done = self.stage == "EMIT_RESULT", self.stage == "LOCAL_DONE"
        return CycleSignals(result, self.beat if result else None, done, self.done if done else None)

    def _read_requests(self):
        if self.issue_k < self.task.k_local and len(self.operands) < self.operand_slots:
            self.operands[self.issue_k] = {"issued": set(), "values": {}}
            self.issue_k += 1
        self.counters["max_operand_slots_used"] = max(self.counters["max_operand_slots_used"], len(self.operands))
        requests = []
        for region in ("weight", "input"):
            for k, entry in self.operands.items():
                if region not in entry["issued"]:
                    address = (self.task.w_base + 4 * (self.group * self.task.k_local + k)
                               if region == "weight" else self.task.x_base + k)
                    requests.append(SramRequest(region, region, address,
                                                4 if region == "weight" else 1, (self.group, k)))
                    break
        return requests

    def _write_requests(self):
        return [SramRequest(f"result{lane}", "result", self.task.y_base + 4 * (4 * self.group + lane),
                            4, (self.group, lane), self.acc[lane].to_bytes(4, "little", signed=True))
                for lane in range(min(LANES, self.task.m_local - 4 * self.group))
                if lane not in self.written]

    def step(self, *, result_ready, local_done_ready=True):
        before = self.signals()
        if self.finished:
            return before
        stage, group = self.stage, self.group
        self.stage_cycles[stage] += 1
        requests = (self._read_requests() if stage == "RUN" else
                    self._write_requests() if stage == "WRITE_RESULT" else [])
        event = self.memory.tick(requests)
        acc_before = tuple(self.acc)
        mac_k = None
        if stage == "INIT_ACC":
            self.acc = [0] * LANES
            self.issue_k = self.consume_k = 0
            self.operands, self.written = {}, set()
            self.stage = "RUN"
        elif stage == "RUN":
            for request in event.grants:
                self.operands[request.tag[1]]["issued"].add(request.region)
            for response in event.responses:
                assert response.request.tag[0] == self.group
                self.operands[response.request.tag[1]]["values"][response.request.region] = response.data
            entry = self.operands.get(self.consume_k)
            if entry is None or len(entry["values"]) != 2:
                self.counters["operand_wait_cycles"] += 1
            elif event.cycle < self.next_mac_cycle:
                self.counters["mac_interval_wait_cycles"] += 1
            else:
                mac_k = self.consume_k
                x = int.from_bytes(entry["values"]["input"], "little", signed=True)
                weights = entry["values"]["weight"]
                valid = min(LANES, self.task.m_local - 4 * self.group)
                for lane in range(valid):
                    w = int.from_bytes(weights[lane:lane + 1], "little", signed=True)
                    self.acc[lane] = int32_wrap(self.acc[lane] + w * x)
                self.counters["mac_active_cycles"] += 1
                self.counters["useful_mac_ops"] += valid
                del self.operands[self.consume_k]
                self.consume_k += 1
                self.next_mac_cycle = event.cycle + self.mac_interval
                if self.consume_k == self.task.k_local:
                    assert not self.operands
                    self.stage = "WRITE_RESULT"
        elif stage == "WRITE_RESULT":
            self.written.update(r.tag[1] for r in event.grants)
            valid = min(LANES, self.task.m_local - 4 * self.group)
            if len(self.written) == valid:
                self.beat = ResultBeat(self.task.tid, self.task.result_dst_tile,
                                       self.task.global_row_base + 4 * self.group,
                                       (1 << valid) - 1, tuple(self.acc), self.task.result_kind,
                                       self.task.task_last and self.group == self.groups - 1)
                self.stage = "EMIT_RESULT"
        elif stage == "EMIT_RESULT":
            if result_ready:
                self.group += 1
                self.beat = None
                self.stage = "LOCAL_DONE" if self.group == self.groups else "INIT_ACC"
            else:
                self.counters["result_stall_cycles"] += 1
        elif stage == "LOCAL_DONE":
            if local_done_ready:
                self.finished, self.stage = True, "IDLE"
            else:
                self.counters["done_stall_cycles"] += 1
        self.last_event = dict(cycle=event.cycle, stage=stage, group=group, mac_k=mac_k,
                               requests=[asdict(r) for r in requests],
                               grants=[asdict(r) for r in event.grants],
                               responses=[asdict(r) for r in event.responses],
                               denied=[asdict(r) for r in event.denied],
                               bank_locations=[dict(client=r.client, pool=self.memory.location(r)[0],
                                                    bank=self.memory.location(r)[1]) for r in requests],
                               acc_before=acc_before, acc_after=tuple(self.acc),
                               result_valid=before.result_valid, result_ready=bool(result_ready),
                               result_payload=asdict(before.result_payload) if before.result_valid else None,
                               local_done_valid=before.local_done_valid, local_done_ready=bool(local_done_ready),
                               local_done_payload=asdict(before.local_done_payload) if before.local_done_valid else None)
        return before

    def stats(self):
        elapsed = self.memory.cycle
        return dict(local_elapsed_cycles=elapsed, **self.counters, **self.memory.counters,
                    lane_slot_utilization=self.counters["useful_mac_ops"] / (4 * elapsed) if elapsed else 0,
                    stage_cycles=dict(self.stage_cycles))
