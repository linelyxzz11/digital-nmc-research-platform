"""Host-side completion aggregation for independent final Roots."""

from simulator.communication import StatusCode, TransactionDonePacket


class RootDoneTracker:
    """Validate one completion packet per planned final Root."""

    def __init__(self, tid: int, host_tile: int,
                 expected: dict[int, tuple[int, int]]):
        if not expected or len(expected) not in (1, 2, 4):
            raise ValueError("expected completion from 1/2/4 final Roots")
        self.tid = tid
        self.host_tile = host_tile
        self.expected = dict(expected)
        self.done_by_root: dict[int, TransactionDonePacket] = {}

    @property
    def count(self) -> int:
        return len(self.done_by_root)

    @property
    def complete(self) -> bool:
        return self.count == len(self.expected)

    def accept(self, packet: TransactionDonePacket) -> None:
        if not isinstance(packet, TransactionDonePacket):
            raise RuntimeError("expected a Root completion packet")
        if packet.src_tile not in self.expected:
            raise RuntimeError("completion came from an unplanned Root")
        if packet.src_tile in self.done_by_root:
            raise RuntimeError("duplicate Root completion")
        count, mask = self.expected[packet.src_tile]
        if (packet.dst_tile != self.host_tile or packet.tid != self.tid or
            packet.status is not StatusCode.OK or
            packet.completed_element_count != count or
            packet.completed_source_mask != mask):
            raise RuntimeError("Root completion disagrees with the plan")
        self.done_by_root[packet.src_tile] = packet
