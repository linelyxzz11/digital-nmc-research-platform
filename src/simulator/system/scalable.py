"""Shared-clock multi-worker GEMV, bounded endpoint trees and a finite XY Mesh."""
from collections import Counter
from dataclasses import asdict, dataclass

from compiler.grid_mapper import GridPlan
from simulator.communication import MemAckPacket, MemWritePacket, ResultPacket, StatusCode, TransactionDonePacket
from simulator.communication.scalable import Mesh
from simulator.communication.v0_1 import FlitType, PacketType
from simulator.functional import Demo0Tile, int32_wrap
from simulator.system.demo1 import commit_mem_write
from simulator.system.endpoint_common import GatherConfig
from simulator.system.root_config import RootTiming
from simulator.system.tile_config import TileComputeConfig
from simulator.system.tile_timing import PortScheduledCompute
from simulator.system.timed_endpoint import TimedRoot


@dataclass(frozen=True)
class GridGatherConfig(GatherConfig):
    tile_count: int = 64
    ranges: tuple[tuple[int,int,int],...] = ()

    def __post_init__(self):
        if (type(self.tile_count) is not int or not 1<=self.tile_count<=64 or
            not 0<=self.root_tile<self.tile_count or not 0<=self.host_tile<self.tile_count or
            not 0<=self.tid<=65535 or not 0<self.source_mask<(1<<self.tile_count) or
            not 0<=self.output_base<=65535 or not 1<=self.output_count<=256 or
            self.output_base+self.output_count>65536 or self.mode not in ('GATHER_FINAL','REDUCE_PSUM')):
            raise ValueError('invalid grid transaction configuration')
        position=self.output_base;union=0
        for begin,end,mask in self.ranges:
            if begin!=position or end<=begin or not 0<mask<(1<<self.tile_count):
                raise ValueError('source ranges must cover outputs once and use legal source masks')
            position=end;union|=mask
        if self.ranges and (position!=self.output_base+self.output_count or union!=self.source_mask):
            raise ValueError('source ranges disagree with the transaction')

    def source_mask_for(self,index):
        if not self.output_base<=index<self.output_base+self.output_count:
            raise ValueError('output index outside configured transaction')
        for begin,end,mask in self.ranges:
            if begin<=index<end: return mask
        return self.source_mask


class ReductionEndpoint:
    """Reuse TimedRoot, with one held outgoing ResultPacket at intermediate nodes.

    The final outgoing vector waits for local source-last completion. Other
    vectors stream as SRAM writes commit. A full output register blocks the
    next WRITE_RESULT, propagating pressure to RX and the network.
    """
    def __init__(self,config,tile,timing,parent=None,kind='PSUM'):
        self.root=TimedRoot(config,tile,timing)
        self.parent,self.kind=parent,kind
        self.output=None
        self.final_output=False
        self.accepted_outputs=0
        self.max_output_slots=0

    def step(self,mesh):
        root=self.root;node=root.config.root_tile
        if self.output is not None and (not self.final_output or root.done is not None):
            if mesh.can_inject(node,2):
                mesh.inject_packet(self.output);self.output=None;self.accepted_outputs+=1
        request=root.write_request()
        event=root.step(done_ready=self.parent is not None or mesh.can_inject(node,1),
                        write_ready=self.parent is None or self.output is None)
        if self.parent is not None and request is not None and root.last_event['write_accepted']:
            assert self.output is None
            self.final_output=len(root.values)==root.config.output_count
            self.output=ResultPacket(node,self.parent,root.config.tid,
                root.config.output_base+(request.address-0x2000)//4,request.lane_mask,
                request.data,self.kind,self.final_output)
            self.max_output_slots=1
        if self.parent is None and event.done_accepted is not None:
            mesh.inject_packet(event.done_accepted)
        return event

    def idle(self): return self.root.idle() and self.output is None


@dataclass
class GridRun:
    plan: GridPlan
    y: list[int]
    cycles: int
    phases: dict
    network: dict
    compute: dict
    endpoints: dict
    packet_counts: dict
    ack_count: int
    done_count: int
    tile_timing: dict
    root_timing: dict

    def summary(self):
        return dict(schema_version=1,scope='weight-resident; functional input commit; timed Tile/Root/NoC; module-calibrated, not full-system RTL',
            plan_sha256=self.plan.digest,mesh=[self.plan.mesh_x,self.plan.mesh_y],
            m=self.plan.m,k=self.plan.k,grid=list(self.plan.grid),workers=list(self.plan.workers),
            root=self.plan.root_tile,reduction=self.plan.reduction,reducer_tiles=[r.tile_id for r in self.plan.reducers],
            cycles=self.cycles,phase_cycles=self.phases,network=self.network,compute_stats=self.compute,
            endpoint_stats=self.endpoints,packet_counts=self.packet_counts,input_acks=self.ack_count,
            transaction_done=self.done_count,tile_timing=self.tile_timing,root_timing=self.root_timing,
            hardware_budget=dict(physical_tiles=self.plan.tile_count,mac_lanes_per_tile=4,
                weight_bytes_per_tile=4096,input_bytes_per_tile=1024,result_bytes_per_tile=1024,
                reduction_capability_per_tile=asdict(RootTiming(**self.root_timing))),y=self.y)


def run_grid(plan,matrix,vector,*,tile_timing=TileComputeConfig(),root_timing=RootTiming(),
             max_cycles=1000000,sink_ready=None):
    if not isinstance(plan,GridPlan): raise ValueError('expected a validated GridPlan')
    plan.validate()
    if len(matrix)!=plan.m or len(vector)!=plan.k or any(len(row)!=plan.k for row in matrix):
        raise ValueError('workload shape disagrees with the plan')
    tiles=[Demo0Tile(i,tile_count=plan.tile_count) for i in range(plan.tile_count)]
    inputs={}
    for shard in plan.slices:
        tiles[shard.tile_id].load_weight_matrix([row[shard.k_start:shard.k_end] for row in matrix[shard.row_start:shard.row_end]])
        inputs[shard.tile_id]=vector[shard.k_start:shard.k_end]
    mesh=Mesh(plan.mesh_x,plan.mesh_y)
    root_mask=0
    for _,_,mask in plan.root_ranges: root_mask|=mask
    configs={plan.root_tile:GridGatherConfig(plan.tid,plan.root_tile,plan.host_tile,root_mask,
        0,plan.m,plan.root_mode,plan.tile_count,plan.root_ranges)}
    endpoints={plan.root_tile:ReductionEndpoint(configs[plan.root_tile],tiles[plan.root_tile],root_timing)}
    for node in plan.reducers:
        mask=sum(1<<s for s in node.sources)
        config=GridGatherConfig(plan.tid,node.tile_id,plan.host_tile,mask,node.row_start,
            node.row_end-node.row_start,'REDUCE_PSUM',plan.tile_count,
            ((node.row_start,node.row_end,mask),))
        endpoints[node.tile_id]=ReductionEndpoint(config,tiles[node.tile_id],root_timing,node.parent,
            'FINAL' if node.parent==plan.root_tile else 'PSUM')
    writes=[]
    for sequence,offset in enumerate(range(0,max(map(len,inputs.values())),4)):
        for tile in plan.workers:
            chunk=inputs[tile][offset:offset+4]
            if chunk:
                writes.append(MemWritePacket(plan.host_tile,tile,plan.tid,0x1000+offset,
                    (1<<len(chunk))-1,sequence,sum((v&255)<<(8*i) for i,v in enumerate(chunk))))
    next_write=0;outstanding=None;pending_ack=None;acks=set();done=[];local_done=set()
    seen=[0]*plan.tile_count;seen_flits={node:0 for node in endpoints}
    emitters={};phase='LOAD';compute_start=gather_complete=None;packet_counts=Counter()
    for _ in range(max_cycles):
        if pending_ack is not None and mesh.can_inject(pending_ack.src_tile,1):
            mesh.inject_packet(pending_ack);pending_ack=None
        if phase=='LOAD' and outstanding is None and next_write<len(writes) and mesh.can_inject(plan.host_tile,2):
            packet=writes[next_write];next_write+=1;mesh.inject_packet(packet)
            outstanding=(packet.dst_tile,packet.tid,packet.sequence)
        if phase=='LOAD' and next_write==len(writes) and len(acks)==len(writes):
            phase='COMPUTE';compute_start=mesh.cycle
            emitters={task.tile_id:PortScheduledCompute(tiles[task.tile_id],task,tile_timing.memory,
                operand_slots=tile_timing.operand_slots,mac_interval=tile_timing.mac_interval) for task in plan.tasks}
        for tile,emitter in emitters.items():
            if emitter.finished: continue
            signals=emitter.signals();ready=mesh.can_inject(tile,2)
            if signals.result_valid and ready:
                beat=signals.result_payload
                mesh.inject_packet(ResultPacket(tile,beat.dst_tile,beat.tid,beat.base_index,
                    beat.lane_mask,beat.data,beat.kind,beat.last))
            if signals.local_done_valid:
                assert tile not in local_done and signals.local_done_payload.status=='OK'
                local_done.add(tile)
            emitter.step(result_ready=ready)
        for node,endpoint in endpoints.items():
            root=endpoint.root
            assert root.cycle==mesh.cycle
            mesh.ejection_ready[node]=sink_ready(mesh.cycle,node) if sink_ready else True
            visible=mesh.visible_local_output(node)
            if (visible is not None and visible.type is FlitType.HEAD and
                visible.data&15==int(PacketType.RESULT) and not root.can_accept()):
                if mesh.ejection_ready[node]: root.counters['rx_blocked_flit_cycles']+=1
                mesh.ejection_ready[node]=False
            event=endpoint.step(mesh)
            if node==plan.root_tile and event.done_created is not None: gather_complete=mesh.cycle+1
        mesh.step()
        for node,endpoint in endpoints.items():
            for flit in mesh.received_flits[node][seen_flits[node]:]:
                if flit.type is FlitType.HEAD and flit.data&15==int(PacketType.RESULT):
                    endpoint.root.reserve_header()
            seen_flits[node]=len(mesh.received_flits[node])
        for tile in range(plan.tile_count):
            for packet in mesh.received_packets[tile][seen[tile]:]:
                packet_counts[type(packet).__name__]+=1
                if isinstance(packet,MemWritePacket):
                    assert phase=='LOAD' and pending_ack is None
                    pending_ack=commit_mem_write(tiles[tile],packet,idle=True)
                elif isinstance(packet,MemAckPacket):
                    key=(packet.src_tile,packet.tid,packet.sequence)
                    if tile!=plan.host_tile or key!=outstanding or key in acks or packet.status!=StatusCode.OK:
                        raise RuntimeError('unexpected/error input acknowledgement')
                    acks.add(key);outstanding=None
                elif isinstance(packet,ResultPacket):
                    if tile not in endpoints: raise RuntimeError('RESULT has no planned reducer')
                    endpoints[tile].root.accept(packet,reserved=True)
                elif isinstance(packet,TransactionDonePacket):
                    if tile!=plan.host_tile or packet!=endpoints[plan.root_tile].root.done or done:
                        raise RuntimeError('unexpected/repeated global completion')
                    done.append(packet)
                else: raise RuntimeError('unexpected packet')
            seen[tile]=len(mesh.received_packets[tile])
        if (done and local_done==set(plan.workers) and mesh.idle() and pending_ack is None and
            all(endpoint.idle() for endpoint in endpoints.values())):
            y=[tiles[plan.root_tile].read_result(i) for i in range(plan.m)]
            golden=[int32_wrap(sum(w*x for w,x in zip(row,vector))) for row in matrix]
            if y!=golden: raise AssertionError('multi-Tile output differs from INT32 golden')
            assert sum(e.stats()['useful_mac_ops'] for e in emitters.values())==plan.m*plan.k
            phases=dict(input_load=compute_start,compute_and_gather=gather_complete-compute_start,
                        completion_drain=mesh.cycle-gather_complete)
            assert sum(phases.values())==mesh.cycle
            network=dict(flit_hops=mesh.flit_hops,ni_inject_stall_tile_cycles=sum(mesh.inject_stall_cycles),
                fifo_full_input_cycles=mesh.fifo_full_cycles,output_blocked_port_cycles=mesh.output_blocked_cycles,
                max_fifo_occupancy=mesh.max_occupancy,link_flits={f'{a}->{b}':v for (a,b),v in mesh.link_flit_count.items()})
            stats={node:dict(**endpoint.root.stats(),max_output_slots=endpoint.max_output_slots,
                            forwarded_result_packets=endpoint.accepted_outputs) for node,endpoint in endpoints.items()}
            return GridRun(plan,y,mesh.cycle,phases,network,{t:e.stats() for t,e in emitters.items()},
                stats,dict(packet_counts),len(acks),len(done),asdict(tile_timing),asdict(root_timing))
    raise TimeoutError(f'grid GEMV exceeded {max_cycles} modeled cycles')
