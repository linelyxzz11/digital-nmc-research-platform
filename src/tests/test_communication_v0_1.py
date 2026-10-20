"""Executable acceptance tests for Communication Architecture v0.1."""

from __future__ import annotations

import pytest

from simulator.communication import (
    ErrorPacket,
    Flit,
    FlitType,
    MemAckPacket,
    MemWritePacket,
    Mesh2x2,
    PacketFormatError,
    ResultNI,
    ResultPacket,
    StatusCode,
    TransactionDonePacket,
    pack_packet,
    unpack_packet,
)
from simulator.functional.demo0_model import ResultBeat


@pytest.mark.parametrize(
    "packet",
    [
        ResultPacket(
            src_tile=0,
            dst_tile=3,
            tid=0xFFFF,
            base_index=0xFFFF,
            lane_mask=0b1111,
            data=(-0x8000_0000, -1, 0, 0x7FFF_FFFF),
            kind="PSUM",
            last=True,
        ),
        MemWritePacket(
            src_tile=0,
            dst_tile=2,
            tid=0xABCD,
            local_addr=0x13FC,
            wstrb=0b1011,
            sequence=0xFFFF,
            wdata=0xDEAD_BEEF,
        ),
        MemAckPacket(
            src_tile=2,
            dst_tile=0,
            tid=7,
            sequence=0x1234,
            status=StatusCode.ADDR_RANGE,
        ),
        TransactionDonePacket(
            src_tile=3,
            dst_tile=0,
            tid=0x1357,
            completed_element_count=256,
            completed_source_mask=0b1111,
        ),
        ErrorPacket(
            src_tile=1,
            dst_tile=0,
            tid=0x2468,
            status=StatusCode.CONFIG_MISS,
            error_detail=0xBEEF,
        ),
    ],
)
def test_packet_pack_unpack_round_trip_at_field_boundaries(packet: object) -> None:
    flits = pack_packet(packet)  # type: ignore[arg-type]
    assert unpack_packet(flits) == packet
    assert all(0 <= flit.data < (1 << 128) for flit in flits)


def test_result_packet_exact_wire_layout_and_signed_lane_order() -> None:
    packet = ResultPacket(
        src_tile=0,
        dst_tile=1,
        tid=0x1234,
        base_index=0x5678,
        lane_mask=0b0011,
        data=(-2, 0x1234_5678, 0, 0),
        kind="FINAL",
        last=True,
    )
    head, tail = pack_packet(packet)

    assert head.type is FlitType.HEAD
    assert head.data & 0xF == 0x1
    assert (head.data >> 4) & 0xFF == 0
    assert (head.data >> 12) & 0xFF == 1
    assert (head.data >> 20) & 0xFFFF == 0x1234
    assert (head.data >> 36) & 0xFF == 0
    assert (head.data >> 44) & 0xFFFF == 0x5678
    assert (head.data >> 60) & 0xF == 0b0011
    assert (head.data >> 64) & 1 == 0
    assert (head.data >> 65) & 1 == 1
    assert head.data >> 66 == 0

    assert tail.type is FlitType.TAIL
    assert tail.data & 0xFFFF_FFFF == 0xFFFF_FFFE
    assert (tail.data >> 32) & 0xFFFF_FFFF == 0x1234_5678
    assert tail.data >> 64 == 0


def test_unpack_rejects_reserved_bits_and_bad_flit_sequence() -> None:
    packet = ResultPacket(0, 1, 1, 0, 0b0001, (7, 0, 0, 0), "FINAL", True)
    head, tail = pack_packet(packet)
    bad_reserved = Flit(FlitType.HEAD, head.data | (1 << 127))
    with pytest.raises(PacketFormatError):
        unpack_packet((bad_reserved, tail))
    with pytest.raises(PacketFormatError):
        unpack_packet((tail, head))


def _beat(dst: int, tid: int = 9, value0: int = 17, value1: int = -3) -> ResultBeat:
    return ResultBeat(
        tid=tid,
        dst_tile=dst,
        base_index=4,
        lane_mask=0b0011,
        data=(value0, value1, 0, 0),
        kind="FINAL",
        last=True,
    )


def test_comm0a_resultbeat_packetization_is_atomic_and_reconstructs() -> None:
    ni = ResultNI(tile_id=0, depth=4)
    beat = _beat(dst=1)

    assert ni.result_ready
    head, tail = ni.accept_result(beat)
    assert (head.type, tail.type) == (FlitType.HEAD, FlitType.TAIL)
    assert unpack_packet((head, tail)) == ResultPacket(
        src_tile=0,
        dst_tile=1,
        tid=beat.tid,
        base_index=beat.base_index,
        lane_mask=beat.lane_mask,
        data=beat.data,
        kind=beat.kind,
        last=beat.last,
    )

    ni.accept_result(_beat(dst=1, tid=10))
    assert not ni.result_ready
    before = tuple(ni.injection_fifo)
    with pytest.raises(BufferError):
        ni.accept_result(_beat(dst=1, tid=11))
    assert tuple(ni.injection_fifo) == before

    mesh = Mesh2x2()
    mesh.inject_flits(0, (head, tail))
    mesh.run_until_idle()
    assert mesh.received_packets[1] == [unpack_packet((head, tail))]
    assert mesh.received_flits[1] == [head, tail]


def test_comm0b_two_hop_xy_path_and_flit_hops() -> None:
    packet = ResultPacket(0, 3, 0x22, 0, 0b0011, (5, -7, 0, 0), "FINAL", True)
    mesh = Mesh2x2()
    mesh.inject_packet(packet)
    mesh.run_until_idle()

    assert mesh.received_packets[3] == [packet]
    assert mesh.flit_hops == 4
    assert mesh.link_flit_count == {(0, 1): 2, (1, 3): 2}
    assert (0, 2) not in mesh.link_flit_count


def test_comm0c_three_cycle_stability_then_sustained_backpressure() -> None:
    # Phase A: the exact three-cycle requirement checks valid/data stability.
    packet = ResultPacket(0, 3, 0x31, 8, 0b0011, (11, 13, 0, 0), "FINAL", True)
    mesh = Mesh2x2()
    mesh.inject_packet(packet)
    for _ in range(20):
        mesh.step()
        if mesh.visible_local_output(3) is not None:
            break
    else:
        pytest.fail("packet never reached Tile 3 local output")

    mesh.ejection_ready[3] = False
    visible = mesh.visible_local_output(3)
    assert visible is not None
    for _ in range(3):
        mesh.step()
        assert mesh.visible_local_output(3) == visible
    assert mesh.output_blocked_cycles >= 3
    assert mesh.max_occupancy <= 4

    mesh.ejection_ready[3] = True
    mesh.run_until_idle()
    assert mesh.received_packets[3] == [packet]

    # Phase B: keep the sink blocked long enough for finite buffering to fill
    # and for ready=0 to reach the source NI.  Three cycles alone cannot
    # guarantee this in a multi-stage network with initially empty FIFOs.
    congested = Mesh2x2()
    congested.ejection_ready[3] = False
    injected: list[ResultPacket] = []
    next_tid = 0x100
    for _ in range(200):
        if congested.can_inject(0, 2):
            burst_packet = ResultPacket(
                0, 3, next_tid, 0, 0b0001, (next_tid, 0, 0, 0), "FINAL", True
            )
            congested.inject_packet(burst_packet)
            injected.append(burst_packet)
            next_tid += 1
        congested.step()
        if congested.inject_stall_cycles[0] > 0 and congested.fifo_full_cycles > 0:
            break
    else:
        pytest.fail("backpressure did not propagate to the source NI")

    assert congested.max_occupancy == 4
    assert congested.inject_stall_cycles[0] > 0
    assert congested.fifo_full_cycles > 0

    congested.ejection_ready[3] = True
    congested.run_until_idle(max_cycles=1000)
    assert congested.received_packets[3] == injected
    assert len(congested.received_flits[3]) == 2 * len(injected)
