"""Executable acceptance tests from Compute Architecture v0.1 section 13."""

import pytest

from simulator.functional import Demo0Tile, ResultEmitter, TileTask, int32_wrap


def make_task(**overrides: object) -> TileTask:
    fields = dict(tid=7, tile_id=0, m_local=2, k_local=2)
    fields.update(overrides)
    return TileTask(**fields)


def test_demo0a_2x2_basic_correctness() -> None:
    tile = Demo0Tile(physical_tile_id=0)
    tile.load_weight_matrix([[1, 2], [3, 4]])
    tile.load_input_vector([5, 6])

    beats, done = tile.execute(make_task(global_row_base=20))

    assert [tile.read_result(row) for row in range(2)] == [17, 39]
    assert len(beats) == 1
    assert beats[0].base_index == 20
    assert beats[0].lane_mask == 0b0011
    assert beats[0].data == (17, 39, 0, 0)
    assert beats[0].last is True
    assert done.status == "OK"

    not_last_beats, not_last_done = tile.execute(make_task(task_last=False))
    assert not_last_beats[0].last is False
    assert not_last_done.status == "OK"


def test_demo0b_signed_values_two_groups_and_tail_mask() -> None:
    x = [2, -1, 3]
    w = [
        [1, 2, -1],
        [-2, 0, 4],
        [3, -1, 2],
        [0, 5, -2],
        [-1, 2, 1],
    ]
    tile = Demo0Tile()
    tile.load_weight_matrix(w)
    tile.load_input_vector(x)

    beats, done = tile.execute(make_task(m_local=5, k_local=3, global_row_base=8))

    assert [tile.read_result(row) for row in range(5)] == [-3, 8, 13, -11, -1]
    assert [(b.base_index, b.lane_mask, b.data, b.last) for b in beats] == [
        (8, 0b1111, (-3, 8, 13, -11), False),
        (12, 0b0001, (-1, 0, 0, 0), True),
    ]
    assert done.status == "OK"


def test_demo0c_backpressure_holds_payload_and_delays_done() -> None:
    tile = Demo0Tile()
    tile.load_weight_matrix([[1, 2], [3, 4]])
    tile.load_input_vector([5, 6])
    beats, done = tile.execute(make_task())
    emitter = ResultEmitter(beats, done)

    stalled = [emitter.step(result_ready=False) for _ in range(3)]
    assert all(cycle.result_valid for cycle in stalled)
    assert [cycle.result_payload for cycle in stalled] == [beats[0]] * 3
    assert all(not cycle.local_done_valid for cycle in stalled)

    accepted = emitter.step(result_ready=True)
    assert accepted.result_payload == beats[0]
    assert emitter.signals().local_done_valid is True

    done_stalled = [
        emitter.step(result_ready=False, local_done_ready=False) for _ in range(3)
    ]
    assert all(cycle.local_done_valid for cycle in done_stalled)
    assert [cycle.local_done_payload for cycle in done_stalled] == [done] * 3

    done_cycle = emitter.step(result_ready=False, local_done_ready=True)
    assert done_cycle.local_done_valid is True
    assert done_cycle.local_done_payload == done
    assert emitter.signals().local_done_valid is False


@pytest.mark.parametrize(
    ("changes", "expected_status"),
    [
        ({"m_local": 0}, "BAD_SHAPE"),
        ({"k_local": 0}, "BAD_SHAPE"),
        ({"w_base": 0x0002}, "ADDR_ALIGN"),
        ({"y_base": 0x2002}, "ADDR_ALIGN"),
        ({"w_base": 0x0FFC, "m_local": 5, "k_local": 3}, "ADDR_RANGE"),
        ({"tile_id": 1}, "BAD_TILE"),
        ({"result_dst_tile": 4}, "BAD_TILE"),
        ({"version": 2}, "BAD_FIELD"),
        ({"result_kind": "INVALID"}, "BAD_FIELD"),
        ({"global_row_base": 0xFFFF, "m_local": 2}, "BAD_FIELD"),
        ({"opcode": "INVALID", "m_local": 0}, "BAD_OPCODE"),
    ],
    ids=[
        "m_local_zero",
        "k_local_zero",
        "w_base_unaligned",
        "y_base_unaligned",
        "weight_address_out_of_range",
        "tile_id_mismatch",
        "destination_tile_out_of_range",
        "unsupported_version",
        "invalid_result_kind",
        "global_result_index_overflow",
        "error_priority_opcode_before_shape",
    ],
)
def test_demo0d_invalid_task_has_no_visible_side_effect(
    changes: dict[str, object], expected_status: str
) -> None:
    tile = Demo0Tile()
    before = (
        bytes(tile.weight_sram),
        bytes(tile.input_sram),
        bytes(tile.result_sram),
    )

    beats, done = tile.execute(make_task(**changes))

    assert beats == []
    assert done.status == expected_status
    assert (
        bytes(tile.weight_sram),
        bytes(tile.input_sram),
        bytes(tile.result_sram),
    ) == before

    emitter = ResultEmitter(beats, done)
    stalled = emitter.step(result_ready=False, local_done_ready=False)
    assert stalled.local_done_valid is True
    assert stalled.local_done_payload == done


@pytest.mark.parametrize(
    "changes",
    [
        {"tid": 0x1_0000},
        {"task_last": 1},
    ],
    ids=["tid_width_overflow", "task_last_not_boolean"],
)
def test_unencodable_task_is_rejected_before_handshake(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        make_task(**changes)


def test_int32_addition_wrap_is_explicit() -> None:
    assert int32_wrap(0x7FFF_FFFF + 1) == -0x8000_0000
