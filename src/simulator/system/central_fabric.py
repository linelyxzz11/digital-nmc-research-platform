"""Bounded gateway-to-array fabric; independent full-duplex 16 B/cycle ports.

The arbiter is shared across ports in each direction. A packet occupies one
finite port slot through its final beat's propagation, including backpressure.
No packet becomes visible at its destination before that final beat arrives.
"""
from collections import Counter, deque
from dataclasses import dataclass


@dataclass
class _Transfer:
    packet: object
    beats: int
    sent: int = 0
    started: bool = False


class CentralFabric:
    def __init__(self, entries, *, hub, lanes=4, depth=2, base_latency=1):
        self.entries = tuple(entries)
        if (not self.entries or len(set(self.entries)) != len(self.entries) or
                any(type(n) is not int or not 0 <= n < 64 for n in self.entries) or
                type(hub) is not int or not 0 <= hub < 64):
            raise ValueError('invalid fabric endpoints')
        if any(type(v) is not int or v < 1 for v in (lanes, depth, base_latency)) or lanes > 4:
            raise ValueError('fabric capacities and latency must be positive; max four lanes')
        self.lanes, self.depth = lanes, depth
        self.latency = {n: base_latency + abs(n % 8 - hub % 8) + abs(n // 8 - hub // 8)
                        for n in self.entries}
        self.queues = {d: {n: deque() for n in self.entries} for d in ('in', 'out')}
        self.occupied = {d: Counter() for d in self.queues}
        self.flights = {d: {n: deque() for n in self.entries} for d in self.queues}
        self.reserved = Counter()
        self.rr = dict.fromkeys(self.queues, 0)
        self.beats = {d: Counter() for d in self.queues}
        self.peak = {d: Counter() for d in self.queues}
        self.max_cycle_beats = Counter()
        self.arbitration_wait = Counter()
        self.destination_wait = Counter()
        self.last_cycle = -1

    @staticmethod
    def _destination(direction, node, packet):
        return (direction, node) if direction == 'out' else (direction, packet.kind, packet.worker)

    def can_submit(self, direction, node):
        return self.occupied[direction][node] < self.depth

    def submit(self, direction, node, packet):
        if not self.can_submit(direction, node):
            return False
        # Internal header/descriptor plus padded payload: no free internal routing.
        self.queues[direction][node].append(_Transfer(packet, 1 + (len(packet.data) + 15) // 16))
        self.occupied[direction][node] += 1
        self.peak[direction][node] = max(self.peak[direction][node], self.occupied[direction][node])
        return True

    def tick(self, cycle, *, ready, deliver):
        """Deliver earlier transfers, then grant at most lanes distinct ports.

        ready(direction, node, packet, reservations) must account for destination
        queue slots already reserved by other packets in this fabric. A slot is
        reserved on the first beat and released only when the full packet arrives.
        """
        if cycle <= self.last_cycle:
            raise ValueError('fabric requires strictly increasing core cycles')
        self.last_cycle = cycle
        for direction in self.queues:
            for node in self.entries:
                flights = self.flights[direction][node]
                while flights and flights[0][0] <= cycle:
                    _, packet = flights.popleft()
                    key = self._destination(direction, node, packet)
                    self.reserved[key] -= 1
                    self.occupied[direction][node] -= 1
                    deliver(direction, node, packet)
            start = self.rr[direction]
            granted = 0
            last_grant = None
            for step in range(len(self.entries)):
                index = (start + step) % len(self.entries)
                node = self.entries[index]
                q = self.queues[direction][node]
                if not q:
                    continue
                transfer = q[0]
                key = self._destination(direction, node, transfer.packet)
                if not transfer.started and not ready(direction, node, transfer.packet, self.reserved[key]):
                    self.destination_wait[direction] += 1
                    continue
                if granted == self.lanes:
                    self.arbitration_wait[direction] += 1
                    continue
                if not transfer.started:
                    transfer.started = True
                    self.reserved[key] += 1
                transfer.sent += 1
                self.beats[direction][node] += 1
                granted += 1
                last_grant = index
                if transfer.sent == transfer.beats:
                    self.flights[direction][node].append((cycle + self.latency[node], transfer.packet))
                    q.popleft()
            self.max_cycle_beats[direction] = max(self.max_cycle_beats[direction], granted)
            if last_grant is not None:
                self.rr[direction] = (last_grant + 1) % len(self.entries)
            assert granted <= self.lanes
            assert all(0 <= self.occupied[direction][n] <= self.depth for n in self.entries)

    def idle(self):
        return not any(self.occupied[d][n] for d in self.queues for n in self.entries)

    def stats(self):
        return dict(lanes_per_direction=self.lanes, port_bytes_per_cycle=16,
                    aggregate_bytes_per_cycle=16*self.lanes,
                    queue_packets_per_port=self.depth, latency_cycles=self.latency,
                    transferred_flits={d: dict(self.beats[d]) for d in self.beats},
                    max_occupied_packets={d: dict(self.peak[d]) for d in self.peak},
                    max_flits_per_cycle=dict(self.max_cycle_beats),
                    arbitration_wait_port_cycles=dict(self.arbitration_wait),
                    destination_wait_port_cycles=dict(self.destination_wait))
