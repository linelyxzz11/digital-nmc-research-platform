"""Single-Tile SRAM service: address-based banks, pipelined reads and RR ports.

This is an executable implementation choice for the v0.1 Compute ports, not
a foundry SRAM characterization. External preload/readback is outside a task.
Reads sample bytes on grant and return exactly read_latency cycles later;
writes commit on their grant edge. Responses cannot be backpressured.
"""
from collections import defaultdict
from dataclasses import dataclass

from simulator.functional.demo0_model import Demo0Tile, W_START, X_START, Y_START


@dataclass(frozen=True)
class SramGeometry:
    banks: int = 1
    word_bytes: int = 4
    ports_per_bank: int = 1
    read_latency: int = 1
    issue_interval: int = 1

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in vars(self).values()):
            raise ValueError("SRAM geometry/timing values must be positive integers")


@dataclass(frozen=True)
class TileMemoryConfig:
    weight: SramGeometry = SramGeometry()
    input: SramGeometry = SramGeometry(word_bytes=1)
    result: SramGeometry = SramGeometry(banks=4)
    share_weight_input: bool = False

    def __post_init__(self):
        if any(not isinstance(g, SramGeometry) for g in (self.weight, self.input, self.result)):
            raise ValueError("each SRAM needs a geometry")
        if type(self.share_weight_input) is not bool:
            raise ValueError("share_weight_input must be boolean")
        if self.share_weight_input and self.weight != self.input:
            raise ValueError("shared Weight/Input must use the same bank geometry")


@dataclass(frozen=True)
class SramRequest:
    client: str
    region: str
    address: int
    size: int
    tag: tuple[int, int]
    write_data: bytes | None = None


@dataclass(frozen=True)
class SramResponse:
    request: SramRequest
    data: bytes
    accepted_cycle: int
    available_cycle: int


@dataclass(frozen=True)
class SramCycle:
    cycle: int
    grants: tuple[SramRequest, ...]
    responses: tuple[SramResponse, ...]
    denied: tuple[SramRequest, ...]


class TileMemoryPorts:
    """One request/client/cycle; round robin independently at each bank.

    There is no request queue: denied requests must be presented again. Read
    pipelines belong to their banks; Compute separately bounds operand slots.
    Bank = floor(byte_address / word_bytes) % banks. Each access must fit one
    physical word, even if it requests only one byte of a wider word.
    """

    CLIENTS = ("weight", "input", "result0", "result1", "result2", "result3")

    def __init__(self, tile: Demo0Tile, config=TileMemoryConfig(), *, clients=CLIENTS):
        if not clients or len(set(clients)) != len(clients):
            raise ValueError("clients must be nonempty and unique")
        self.config, self.clients = config, tuple(clients)
        self.regions = {"weight": (W_START, tile.weight_sram),
                        "input": (X_START, tile.input_sram),
                        "result": (Y_START, tile.result_sram)}
        self.cycle = 0
        self._next_free, self._rr = {}, {}
        self._pending = defaultdict(list)
        self.counters = dict(read_requests=0, write_requests=0, read_bytes=0,
                             write_bytes=0, bank_conflict_request_cycles=0,
                             port_busy_request_cycles=0)

    def location(self, request):
        geometry = getattr(self.config, request.region)
        pool = ("operands" if self.config.share_weight_input and request.region in ("weight", "input")
                else request.region)
        bank = (request.address // geometry.word_bytes) % geometry.banks
        return pool, bank

    def _validate(self, request):
        if request.client not in self.clients or request.region not in self.regions:
            raise ValueError("unknown SRAM client or region")
        base, memory = self.regions[request.region]
        if (type(request.address) is not int or type(request.size) is not int
                or request.size < 1 or request.address < base
                or request.address + request.size > base + len(memory)):
            raise ValueError("SRAM request is out of range")
        width = getattr(self.config, request.region).word_bytes
        if request.address // width != (request.address + request.size - 1) // width:
            raise ValueError("SRAM request crosses a physical word")
        if request.write_data is not None and (
                not isinstance(request.write_data, bytes) or len(request.write_data) != request.size):
            raise ValueError("write payload size mismatch")

    def tick(self, requests=()):
        requests = tuple(requests)
        for request in requests:
            self._validate(request)
        if len({r.client for r in requests}) != len(requests):
            raise ValueError("at most one request per client per cycle")
        # Validate the whole batch before advancing time or mutating SRAM.
        responses = tuple(self._pending.pop(self.cycle, ()))
        demands = defaultdict(list)
        for request in requests:
            demands[self.location(request)].append(request)
        grants, denied, writes = [], [], []
        for key, candidates in sorted(demands.items()):
            geometry = getattr(self.config, candidates[0].region)
            ready_at = self._next_free.setdefault(key, [0] * geometry.ports_per_bank)
            pointer = self._rr.get(key, 0)
            candidates.sort(key=lambda r: (self.clients.index(r.client) - pointer) % len(self.clients))
            free_ports = [i for i, value in enumerate(ready_at) if value <= self.cycle]
            accepted = min(len(free_ports), len(candidates))
            conflict = max(0, len(candidates) - geometry.ports_per_bank)
            self.counters["bank_conflict_request_cycles"] += conflict
            self.counters["port_busy_request_cycles"] += len(candidates) - accepted - conflict
            denied.extend(candidates[accepted:])
            for request, port in zip(candidates[:accepted], free_ports):
                grants.append(request)
                ready_at[port] = self.cycle + geometry.issue_interval
                self._rr[key] = (self.clients.index(request.client) + 1) % len(self.clients)
                base, memory = self.regions[request.region]
                offset = request.address - base
                if request.write_data is None:
                    # All reads sample pre-edge bytes, before any same-edge writes.
                    response = SramResponse(request, bytes(memory[offset:offset + request.size]),
                                            self.cycle, self.cycle + geometry.read_latency)
                    self._pending[response.available_cycle].append(response)
                    self.counters["read_requests"] += 1
                    self.counters["read_bytes"] += request.size
                else:
                    writes.append((memory, offset, request.write_data))
                    self.counters["write_requests"] += 1
                    self.counters["write_bytes"] += request.size
        for memory, offset, data in writes:
            memory[offset:offset + len(data)] = data
        result = SramCycle(self.cycle, tuple(grants), responses, tuple(denied))
        self.cycle += 1
        return result
