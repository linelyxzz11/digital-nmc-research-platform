"""Instance-local XY Mesh and v0.2 completion masks for up to 64 Tiles.

RESULT/MEM_WRITE/ACK layouts, router FIFOs, wormhole locks and arbitration are
the validated v0.1 implementation. Only the completion-mask field grows.
"""
from dataclasses import dataclass

from simulator.communication import v0_1


@dataclass(frozen=True)
class PacketCodec:
    tile_count: int

    def __post_init__(self):
        if type(self.tile_count) is not int or not 1<=self.tile_count<=64:
            raise ValueError('v0.2 transactions support 1..64 Tiles')

    @property
    def source_mask_bits(self):
        return max(4,self.tile_count)

    def pack(self,packet):
        return v0_1.pack_packet(packet,tile_count=self.tile_count,source_mask_bits=self.source_mask_bits)

    def unpack(self,flits):
        return v0_1.unpack_packet(flits,tile_count=self.tile_count,source_mask_bits=self.source_mask_bits)


class Mesh(v0_1.Mesh2x2):
    def __init__(self,mesh_x=8,mesh_y=8,*,fifo_depth=4,ni_depth=4):
        codec=PacketCodec(mesh_x*mesh_y)
        super().__init__(fifo_depth,ni_depth,mesh_x=mesh_x,mesh_y=mesh_y,codec=codec)
