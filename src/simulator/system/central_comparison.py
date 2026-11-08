"""Matched-budget ROW GEMV experiment; experimental streaming endpoints, not RTL.

Reuse the existing finite XY routers and PortScheduledCompute. Only weight
access location changes: local Bank or credit-controlled remote stream.
"""
from collections import Counter, deque
from dataclasses import dataclass
import struct

from compiler.grid_mapper import map_grid
from simulator.communication.v0_1 import Mesh2x2, Flit, FlitType
from simulator.functional import Demo0Tile, int32_wrap
from simulator.system.tile_timing import PortScheduledCompute
from simulator.system.tile_memory import SramCycle, SramResponse, TileMemoryPorts


@dataclass(frozen=True)
class StreamPacket:
    src_tile: int
    dst_tile: int
    kind: int  # input=1, weight=2, result=3, weight credit=4
    worker: int
    offset: int
    data: bytes = b''


class StreamCodec:
    """Experimental header plus <=16 byte payload, same router destination bits."""
    def pack(self, p):
        if not (0 <= p.src_tile < 64 and 0 <= p.dst_tile < 64 and
                0 <= p.worker < 64 and 0 <= p.offset < 65536 and
                p.kind in (1, 2, 3, 4) and len(p.data) <= 16):
            raise ValueError('invalid streaming packet')
        h = (p.kind | p.src_tile << 4 | p.dst_tile << 12 | p.worker << 20 |
             p.offset << 28 | len(p.data) << 44)
        if not p.data:
            return (Flit(FlitType.HEADTAIL, h),)
        return (Flit(FlitType.HEAD, h),
                Flit(FlitType.TAIL, int.from_bytes(p.data, 'little')))

    def unpack(self, flits):
        h = flits[0].data
        size = (h >> 44) & 31
        if size:
            if len(flits) != 2 or flits[0].type is not FlitType.HEAD or flits[1].type is not FlitType.TAIL:
                raise ValueError('invalid streaming framing')
            payload = flits[1].data.to_bytes(16, 'little')[:size]
        else:
            if len(flits) != 1 or flits[0].type is not FlitType.HEADTAIL:
                raise ValueError('invalid credit framing')
            payload = b''
        return StreamPacket((h >> 4) & 255, (h >> 12) & 255, h & 15,
                            (h >> 20) & 255, (h >> 28) & 65535, payload)


class RemoteWeightPorts:
    """Same local X/Y ports; bounded prefetched W FIFO feeds a 1-cycle read port."""
    def __init__(self, local, worker, release):
        self.local, self.worker, self.release = local, worker, release
        self.queue = deque()
        self.pending = []
        self.consumed = 0
        self.received = 0
        self.max_packets = 0
        self.counters = local.counters

    @property
    def cycle(self):
        return self.local.cycle

    def location(self, request):
        return self.local.location(request)

    def receive(self, packet):
        if packet.offset != self.received:
            raise AssertionError('weight stream reordered or duplicated')
        self.received += len(packet.data)
        self.queue.append([packet.offset, packet.data, 0])
        self.max_packets = max(self.max_packets, len(self.queue))

    def tick(self, requests=()):
        c = self.cycle
        responses = tuple(self.pending)
        self.pending = []
        event = self.local.tick(r for r in requests if r.region != 'weight')
        grants, denied = list(event.grants), list(event.denied)
        for r in requests:
            if r.region != 'weight':
                continue
            if not self.queue:
                denied.append(r)
                continue
            offset, data, used = self.queue[0]
            assert r.address == offset + used and r.size == 4
            value = data[used:used + 4]
            assert len(value) == 4
            self.pending.append(SramResponse(r, value, c, c + 1))
            grants.append(r)
            self.consumed += 4
            self.counters['read_requests'] += 1
            self.counters['read_bytes'] += 4
            used += 4
            if used == len(data):
                self.queue.popleft()
                self.release(self.worker, offset)
            else:
                self.queue[0][2] = used
        return SramCycle(c, tuple(grants), (*event.responses, *responses), tuple(denied))


def run_comparison(matrix, vector, *, architecture, workers=None, central_tile=0,
                   link_interval=1, prefetch_packets=8, sink_interval=1,
                   max_cycles=200000):
    """Return Host-visible numeric result and bounded cycle/traffic evidence.

    link_interval: one 128-bit flit opportunity per directed link every N core
    clocks. This changes the whole network equally in both architectures.
    """
    if architecture not in ('central', 'nmc'):
        raise ValueError('choose central or nmc')
    if any(type(v) is not int or v < 1 for v in
           (link_interval, prefetch_packets, sink_interval, max_cycles)):
        raise ValueError('intervals and capacities must be positive integers')
    if not 0 <= central_tile < 64:
        raise ValueError('central Tile outside Mesh')
    m, k = len(matrix), len(vector)
    if not m or not k or any(len(row) != k for row in matrix):
        raise ValueError('invalid matrix/vector dimensions')
    if any(type(v) is not int or not -128 <= v <= 127 for row in matrix for v in row) or any(type(v) is not int or not -128 <= v <= 127 for v in vector):
        raise ValueError('operands must be signed INT8')
    if workers is None:
        # Fixed across both architectures and central endpoint placements.
        pool = [i for i in range(64) if i not in (0, 36)]
        workers = tuple(sorted(pool, key=lambda i: (i % 8 + i // 8, i))[:32])
    workers = tuple(workers)
    if central_tile in workers or 0 in workers:
        raise ValueError('host/central endpoint must be outside storage workers')
    plan = map_grid(m, k, grid=(len(workers), 1), workers=workers, reduction='central')
    codec = StreamCodec()
    mesh = Mesh2x2(mesh_x=8, mesh_y=8, codec=codec, fifo_depth=4, ni_depth=4)
    tiles, engines, remote = {}, {}, {}
    credits = {w: prefetch_packets for w in workers}
    credit_tx = deque()
    def release(w, offset):
        credit_tx.append(StreamPacket(central_tile, w, 4, w, offset))
    images = {}
    for shard, task in zip(plan.slices, plan.tasks):
        tile = Demo0Tile(shard.tile_id, tile_count=64)
        tile.load_weight_matrix(matrix[shard.row_start:shard.row_end])
        tiles[shard.tile_id] = tile
        engine = PortScheduledCompute(tile, task)
        if architecture == 'central':
            port = RemoteWeightPorts(engine.memory, shard.tile_id, release)
            engine.memory = port
            remote[shard.tile_id] = port
        engines[shard.tile_id] = engine
        size = 4 * ((task.m_local + 3) // 4) * k
        images[shard.tile_id] = bytes(tile.weight_sram[:size])
    # Input buffers are physically separate per 4-MAC cluster in both designs.
    # Central clusters share the finite central NI but each retains a 1B/cycle X port.
    inputs = deque(StreamPacket(0, w if architecture == 'nmc' else central_tile,
                                1, w, offset,
                                bytes(v & 255 for v in vector[offset:offset + 16]))
                   for offset in range(0, k, 16) for w in workers)
    input_rx = {w: deque() for w in workers}
    loaded = Counter()
    source = {w: dict(offset=0, data=bytearray(), pending=None, packet=None,
                      outstanding=set(), max_outstanding=0) for w in workers}
    result_tx = deque()
    result_rx = deque()
    output = {}
    seen = [0] * 64
    traffic = Counter()
    payload = Counter()
    last_write = -sink_interval
    compute_start = None
    first_mac = last_mac = None
    max_input_rx = max_result_rx = max_credit_tx = max_result_tx = 0
    central_rr = 0
    sender_rr = 0
    source_reads = 0
    local_done = set()

    def inject(p):
        fs = codec.pack(p)
        if not mesh.can_inject(p.src_tile, len(fs)):
            return False
        mesh.inject_flits(p.src_tile, fs)
        name = {1:'input', 2:'weight', 3:'result', 4:'credit'}[p.kind]
        traffic[name] += len(fs)
        payload[name] += len(p.data)
        return True

    for cycle in range(max_cycles):
        # Accepted input packets write through the same 1-byte X SRAM port.
        for w in workers:
            if input_rx[w]:
                p, used = input_rx[w][0]
                tiles[w].input_sram[p.offset + used] = p.data[used]
                loaded[w] += 1
                used += 1
                if used == len(p.data):
                    input_rx[w].popleft()
                else:
                    input_rx[w][0][1] = used
        if compute_start is None and all(loaded[w] == k for w in workers):
            compute_start = cycle
        # One shared 16-byte Host output write port; actual received values commit.
        if result_rx and cycle - last_write >= sink_interval:
            p = result_rx.popleft()
            for j, value in enumerate(struct.unpack('<' + 'i' * (len(p.data)//4), p.data)):
                index = p.offset + j
                assert index not in output and index < m
                output[index] = value
            last_write = cycle
        if architecture == 'central':
            for w in workers:
                s = source[w]
                if s['pending'] is not None:
                    s['data'].extend(s['pending']); s['pending'] = None
                    if len(s['data']) == 16 or s['offset'] == len(images[w]):
                        s['packet'] = StreamPacket(w, central_tile, 2, w,
                            s['offset'] - len(s['data']), bytes(s['data']))
                        s['data'].clear()
                if s['packet'] is not None and inject(s['packet']):
                    s['packet'] = None
                if s['packet'] is None and s['offset'] < len(images[w]):
                    if not s['data']:
                        if credits[w] == 0:
                            continue
                        credits[w] -= 1
                        s['outstanding'].add(s['offset'])
                        s['max_outstanding'] = max(s['max_outstanding'], len(s['outstanding']))
                    s['pending'] = images[w][s['offset']:s['offset']+4]
                    s['offset'] += 4
                    source_reads += 4
        if compute_start is not None:
            for w, engine in engines.items():
                if engine.finished:
                    continue
                sig = engine.signals()
                ready = False
                if sig.result_valid:
                    beat = sig.result_payload
                    values = [v for lane, v in enumerate(beat.data) if beat.lane_mask & (1 << lane)]
                    packet = StreamPacket(w if architecture == 'nmc' else central_tile,
                                          0, 3, w, beat.base_index,
                                          struct.pack('<' + 'i'*len(values), *values))
                    if architecture == 'nmc':
                        ready = inject(packet)
                    elif len(result_tx) < len(workers):
                        result_tx.append(packet); ready = True
                engine.step(result_ready=ready)
                if engine.last_event['mac_k'] is not None:
                    first_mac = cycle if first_mac is None else first_mac
                    last_mac = cycle
                if engine.finished:
                    local_done.add(w)
        # A single central NI arbitrates input (when co-located), credit and result.
        queues = [credit_tx, result_tx]
        if central_tile == 0:
            queues.append(inputs)
        for delta in range(len(queues)):
            i = (central_rr + delta) % len(queues)
            if queues[i] and inject(queues[i][0]):
                queues[i].popleft(); central_rr = (i+1) % len(queues); break
        if central_tile != 0 and inputs and inject(inputs[0]):
            inputs.popleft()
        # Bound receive buffers before accepting HEAD; a started packet has a slot.
        for node in range(64):
            mesh.ejection_ready[node] = True
            f = mesh.visible_local_output(node)
            if f is None or f.type not in (FlitType.HEAD, FlitType.HEADTAIL):
                continue
            h = f.data; kind = h & 15; w = (h >> 20) & 255
            if kind == 1:
                mesh.ejection_ready[node] = len(input_rx[w]) < 2
            elif kind == 3:
                mesh.ejection_ready[node] = len(result_rx) < 2
        if cycle % link_interval == 0:
            mesh.step()
            for node in range(64):
                for p in mesh.received_packets[node][seen[node]:]:
                    assert p.dst_tile == node
                    if p.kind == 1:
                        input_rx[p.worker].append([p, 0])
                        max_input_rx = max(max_input_rx, len(input_rx[p.worker]))
                    elif p.kind == 2:
                        remote[p.worker].receive(p)
                        assert len(remote[p.worker].queue) <= prefetch_packets
                    elif p.kind == 3:
                        result_rx.append(p)
                        max_result_rx = max(max_result_rx, len(result_rx))
                    elif p.kind == 4:
                        s = source[p.worker]
                        assert p.offset in s['outstanding']
                        s['outstanding'].remove(p.offset)
                        credits[p.worker] += 1
                        assert credits[p.worker] <= prefetch_packets
                seen[node] = len(mesh.received_packets[node])
        max_credit_tx = max(max_credit_tx, len(credit_tx))
        max_result_tx = max(max_result_tx, len(result_tx))
        assert max_input_rx <= 2 and max_result_rx <= 2
        assert len(credit_tx) <= len(workers)*prefetch_packets
        if (len(output) == m and len(local_done) == len(workers) and not result_rx
                and not result_tx and not credit_tx and not inputs and mesh.idle()):
            y = [output[i] for i in range(m)]
            golden = [int32_wrap(sum(a*b for a,b in zip(row,vector))) for row in matrix]
            assert y == golden
            stats = {w:e.stats() for w,e in engines.items()}
            useful = sum(s['useful_mac_ops'] for s in stats.values())
            assert useful == m*k
            if architecture == 'central':
                assert source_reads == sum(len(a) for a in images.values())
                assert all(credits[w] == prefetch_packets and not source[w]['outstanding'] for w in workers)
                assert all(remote[w].consumed == len(images[w]) for w in workers)
            total = cycle + 1
            return dict(architecture=architecture, m=m, k=k, workers=list(workers),
                central_tile=central_tile, link_interval=link_interval,
                prefetch_packets=prefetch_packets, sink_interval=sink_interval,
                total_cycles=total, input_ready_cycle=compute_start,
                kernel_to_host_cycles=total-compute_start,
                host_output_ready_cycles=last_write+1,
                final_drain_cycles=total-last_write-1,
                first_mac_cycle=first_mac,last_mac_cycle=last_mac,
                useful_mac_ops=useful, mac_utilization_total=useful/(4*len(workers)*total),
                operand_wait_cluster_cycles=sum(s['operand_wait_cycles'] for s in stats.values()),
                result_stall_cluster_cycles=sum(s['result_stall_cycles'] for s in stats.values()),
                weight_bank_read_bytes=source_reads if architecture=='central' else sum(len(a) for a in images.values()),
                network_payload_bytes=dict(payload), network_injected_flits=dict(traffic),
                flit_hops=mesh.flit_hops, router_blocked_port_ticks=mesh.output_blocked_cycles,
                router_max_flits=mesh.max_occupancy,max_input_rx_packets=max_input_rx,
                max_result_rx_packets=max_result_rx,max_credit_tx_packets=max_credit_tx,
                max_result_tx_packets=max_result_tx,
                max_weight_outstanding_packets=max((s['max_outstanding'] for s in source.values()),default=0),
                golden_ok=True,y=y)
    raise TimeoutError(f'comparison exceeded {max_cycles} core cycles')
