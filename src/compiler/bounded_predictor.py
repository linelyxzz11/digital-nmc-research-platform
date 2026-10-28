"""Timing-only packet surrogate with finite credits and upstream backpressure.

No matrix MACs, input SRAM writes, measured-cycle labels, or full flit Mesh are
executed. Whole packets occupy their flit credits until each hop completes;
this store-and-forward approximation is deliberately distinct from wormhole.
Endpoint service reuses the declared finite hardware contract, on zero data.
"""
from collections import Counter,defaultdict,deque
from dataclasses import dataclass

from simulator.communication import ResultPacket,TransactionDonePacket
from simulator.functional import Demo0Tile
from simulator.system.scalable import GridGatherConfig,ReductionEndpoint


@dataclass
class _Transfer:
    source: tuple[int,int]
    destination: tuple[int,int] | None
    packet: object
    remaining: int


class PacketNetwork:
    """Four-flit router/NI credits; one RR packet transfer per output.

    Ports retain NORTH/EAST/SOUTH/WEST/LOCAL order. A Result has two flits;
    Done has one. One input can send only its head packet. Blocked grants are
    held. Receiver slots are reserved when ejection starts, then committed at
    packet completion. No same-edge dequeue lookahead creates extra credit.
    """
    fifo_depth=ni_depth=4

    def __init__(self,width,host,endpoints):
        self.width,self.host,self.endpoints=width,host,endpoints
        self.cycle=0
        self.queues=defaultdict(deque)
        self.used=defaultdict(int)
        self.active_inputs=set()
        self.ni=defaultdict(deque);self.ni_used=defaultdict(int)
        self.flights={};self.rr=defaultdict(int);self.held={}
        self.link_flits=Counter()
        self.done_count=0
        self.blocked_cycles=self.max_router_flits=self.max_ni_flits=0

    @staticmethod
    def flits(packet):
        return 2 if isinstance(packet,ResultPacket) else 1

    def can_inject(self,node,count):
        return self.ni_used[node]+count<=self.ni_depth

    def inject_packet(self,packet):
        count=self.flits(packet);node=packet.src_tile
        if not self.can_inject(node,count):
            raise BufferError('packet predictor NI is full')
        self.ni[node].append(packet);self.ni_used[node]+=count
        self.max_ni_flits=max(self.max_ni_flits,self.ni_used[node])

    def route(self,node,destination):
        if node%self.width<destination%self.width: return 1,(node+1,3)
        if node%self.width>destination%self.width: return 3,(node-1,1)
        if node<destination: return 2,(node+self.width,0)
        if node>destination: return 0,(node-self.width,2)
        return 4,None

    def _reserve(self,destination,count):
        self.used[destination]+=count
        assert self.used[destination]<=self.fifo_depth
        self.max_router_flits=max(self.max_router_flits,self.used[destination])

    def step(self,rx_ready):
        requests=defaultdict(dict)
        for source in self.active_inputs:
            packet=self.queues[source][0]
            output,destination=self.route(source[0],packet.dst_tile)
            requests[source[0],output][source[1]]=(destination,packet)
        for output,inputs in requests.items():
            if output in self.flights: continue
            node,port=output
            selected=self.held.get(output)
            if selected is None:
                selected=min(inputs,key=lambda candidate:(candidate-self.rr[output])%5)
            destination,packet=inputs[selected]
            count=self.flits(packet)
            ready=(self.used[destination]+count<=self.fifo_depth if destination is not None else
                   rx_ready.get(node,False) if isinstance(packet,ResultPacket) else node==self.host)
            if not ready:
                self.held[output]=selected;self.blocked_cycles+=1;continue
            self.held.pop(output,None)
            if destination is not None:
                self._reserve(destination,count)
                self.link_flits[node,destination[0]]+=count
            else:
                if isinstance(packet,ResultPacket): self.endpoints[node].root.reserve_header()
            self.flights[output]=_Transfer((node,selected),destination,packet,count)
        # Injection is a separate local input link; it uses the same pre-edge
        # credits as routed traffic, without a combinational bypass.
        for node,queue in self.ni.items():
            output=(node,-1)
            if not queue or output in self.flights: continue
            packet=queue[0];count=self.flits(packet);destination=(node,4)
            if self.used[destination]+count>self.fifo_depth:
                self.blocked_cycles+=1;continue
            self._reserve(destination,count)
            self.flights[output]=_Transfer((node,-1),destination,packet,count)
        for output,transfer in list(self.flights.items()):
            transfer.remaining-=1
            if transfer.remaining: continue
            count=self.flits(transfer.packet);node,port=transfer.source
            if port==-1:
                assert self.ni[node].popleft() is transfer.packet
                self.ni_used[node]-=count
            else:
                assert self.queues[transfer.source].popleft() is transfer.packet
                self.used[transfer.source]-=count
                if not self.queues[transfer.source]: self.active_inputs.remove(transfer.source)
                self.rr[output]=(port+1)%5
            if transfer.destination is not None:
                self.queues[transfer.destination].append(transfer.packet)
                self.active_inputs.add(transfer.destination)
            elif isinstance(transfer.packet,ResultPacket):
                self.endpoints[node].root.accept(transfer.packet,reserved=True)
            else:
                if not isinstance(transfer.packet,TransactionDonePacket) or node!=self.host:
                    raise ValueError('unexpected predictor completion')
                self.done_count+=1
                assert self.done_count==1
            del self.flights[output]
        self.cycle+=1

    def idle(self):
        return not self.active_inputs and not self.flights and not any(self.ni.values())


def input_cost(plan,path_for):
    """Exact functional-commit stop-and-wait cost of the reference evaluator.

    Per request: two-flit write (d+3 edges), one-flit ACK (d+2 edges),
    hence 2*d+5. There is no overlap between different requests.
    This does not describe the RTL's additional narrow SRAM write service.
    """
    cycles=packets=0;links=Counter()
    for shard in plan.slices:
        requests=(shard.k_end-shard.k_start+3)//4
        forward=path_for(plan.host_tile,shard.tile_id,plan.mesh_x)[:-1]
        reverse=path_for(shard.tile_id,plan.host_tile,plan.mesh_x)[:-1]
        cycles+=requests*(len(forward)+len(reverse)+5)
        packets+=requests
        for edge in forward: links[edge]+=2*requests
        for edge in reverse: links[edge]+=requests
    return cycles,packets,links


def warm_arbitration(network,plan):
    """Carry the serialized input phase's RR state into compute.

    All write/ACK packets drain before the next request. A worker repeats the
    same paths, so only its LAST write/ACK pair can determine any final RR
    pointer. Sort those final pairs by sequence, then original worker order.
    """
    ordered=sorted(enumerate(plan.slices),
                   key=lambda item:((item[1].k_end-item[1].k_start-1)//4,item[0]))
    for _,shard in ordered:
        for source,destination in ((plan.host_tile,shard.tile_id),(shard.tile_id,plan.host_tile)):
            incoming=4;node=source
            while True:
                output,next_input=network.route(node,destination)
                network.rr[node,output]=(incoming+1)%5
                if next_input is None: break
                node,incoming=next_input


def bounded_estimate(plan,tile_timing,root_timing,*,beats_for,path_for,max_cycles=1000000):
    root_mask=0
    for _,_,mask in plan.root_ranges: root_mask|=mask
    config=GridGatherConfig(plan.tid,plan.root_tile,plan.host_tile,root_mask,
        0,plan.m,plan.root_mode,plan.tile_count,plan.root_ranges)
    endpoints={plan.root_tile:ReductionEndpoint(config,
        Demo0Tile(plan.root_tile,tile_count=plan.tile_count),root_timing)}
    for node in plan.reducers:
        mask=sum(1<<child for child in node.sources)
        config=GridGatherConfig(plan.tid,node.tile_id,plan.host_tile,mask,node.row_start,
            node.row_end-node.row_start,'REDUCE_PSUM',plan.tile_count,
            ((node.row_start,node.row_end,mask),))
        endpoints[node.tile_id]=ReductionEndpoint(config,Demo0Tile(node.tile_id,tile_count=plan.tile_count),
            root_timing,node.parent,'FINAL' if node.parent==plan.root_tile else 'PSUM')
    network=PacketNetwork(plan.mesh_x,plan.host_tile,endpoints)
    warm_arbitration(network,plan)
    schedules={task.tile_id:beats_for(task.m_local,task.k_local,tile_timing) for task in plan.tasks}
    positions={task.tile_id:0 for task in plan.tasks}
    release={node:beats[0][0] for node,beats in schedules.items()}
    finished={};source_stalls=0
    for cycle in range(max_cycles):
        for task in plan.tasks:
            node=task.tile_id;position=positions[node];beats=schedules[node]
            if position==len(beats) or release[node]>cycle: continue
            if not network.can_inject(node,2): source_stalls+=1;continue
            _,base,mask,last=beats[position]
            network.inject_packet(ResultPacket(node,task.result_dst_tile,task.tid,
                task.global_row_base+base,mask,(0,0,0,0),task.result_kind,last))
            positions[node]+=1
            if positions[node]==len(beats): finished[node]=cycle+1
            else: release[node]=cycle+beats[position+1][0]-beats[position][0]
        rx_ready={node:endpoint.root.can_accept() for node,endpoint in endpoints.items()}
        for endpoint in endpoints.values(): endpoint.step(network)
        network.step(rx_ready)
        if (network.done_count and len(finished)==len(plan.tasks) and
            all(done<=cycle for done in finished.values()) and network.idle() and
            all(endpoint.idle() for endpoint in endpoints.values())):
            load,_,links=input_cost(plan,path_for)
            links.update(network.link_flits)
            return dict(cycles=load+network.cycle,input_cycles=load,
                compute_gather_cycles=network.cycle,flit_hops=sum(links.values()),
                peak_link_flits=max(links.values(),default=0),
                endpoint_service_cycles=sum(endpoint.root.stats()['service_cycles'] for endpoint in endpoints.values()),
                model='bounded-packet-v0.4',source_stall_cycles=source_stalls,
                network_blocked_cycles=network.blocked_cycles,
                max_router_flits=network.max_router_flits,max_ni_flits=network.max_ni_flits,
                max_rx_slots=max(endpoint.root.stats()['max_rx_slots'] for endpoint in endpoints.values()))
    raise TimeoutError('bounded packet predictor exceeded modeled-cycle limit')
