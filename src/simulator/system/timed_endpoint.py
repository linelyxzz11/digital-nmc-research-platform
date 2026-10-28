"""Bounded packet ingress, vector/scalar Root service and held completion.

RX depth counts queued packets PLUS one reserved in-flight packet head. One
active packet register is additional. Indexed PSUM capacity covers output_count:
the current FIFO/no-reorder protocol cannot safely block arbitrary new indices
with a smaller scratchpad. Only one transaction owns the endpoint.
"""
from collections import deque
from dataclasses import dataclass, replace

from simulator.communication import ResultPacket, StatusCode, TransactionDonePacket
from simulator.functional import Demo0Tile, int32_wrap
from simulator.functional.demo0_model import Y_START
from simulator.system.endpoint_common import GatherConfig, TransactionError
from simulator.system.root_config import RootCapacityError, RootTiming


@dataclass(frozen=True)
class RootCycle:
    cycle: int
    packet_committed: ResultPacket | None = None
    done_created: TransactionDonePacket | None = None
    done_accepted: TransactionDonePacket | None = None


@dataclass(frozen=True)
class RootWriteRequest:
    """One atomic Result SRAM vector; data lane i maps to address + 4*i."""
    address: int
    lane_mask: int
    data: tuple[int, int, int, int]

    @property
    def packed_data(self):
        return sum((value & 0xffffffff) << (32*lane) for lane,value in enumerate(self.data))


class TimedRoot:
    def __init__(self, config: GatherConfig, root: Demo0Tile, timing=RootTiming()):
        if not isinstance(timing,RootTiming):
            raise ValueError('timing must be RootTiming')
        if root.physical_tile_id != config.root_tile:
            raise ValueError("root Tile does not match config")
        if config.mode == 'REDUCE_PSUM' and timing.reduction_entries < config.output_count:
            raise RootCapacityError("Root reduction_entries must cover output_count in the FIFO/no-reorder design")
        self.config, self.root, self.timing = config, root, timing
        self.rx, self.done_fifo = deque(), deque()
        self.header_reserved = False
        self.active = None
        self.elements, self.chunk, self.updated = [], [], []
        self.position = self.remaining = 0
        self.stage, self.cycle, self.last_event = 'IDLE', 0, None
        self.values, self.accumulators = {}, {}
        self.received_source_masks, self.accepted_source_masks = {}, {}
        self.last_mask = self.accepted_last_mask = 0
        self.done = self.error = None
        self.psum_elements = self.duplicate_errors = 0
        self.counters = dict(accepted_packets=0,processed_packets=0,read_elements=0,
            add_elements=0,partial_update_elements=0,result_write_elements=0,
            max_rx_slots=0,max_reduction_entries=0,max_done_queue=0,
            rx_full_cycles=0,rx_blocked_flit_cycles=0,reduction_full_cycles=0,
            done_stall_cycles=0,result_write_stall_cycles=0,done_created_count=0,done_accepted_count=0)
        self.stage_cycles = {s:0 for s in ('IDLE','DISPATCH','READ','ADD','UPDATE','WRITE_RESULT','PACKET_DONE','CREATE_DONE')}

    @property
    def rx_slots(self):
        return len(self.rx) + int(self.header_reserved)

    def can_accept(self, packet=None):
        return self.rx_slots < self.timing.rx_queue_packets

    def reserve_header(self):
        if self.header_reserved: raise RuntimeError("nested Root packet reservation")
        if not self.can_accept(): raise BufferError("Root RX packet slots are full")
        self.header_reserved = True
        self._check_bounds()

    def _check_bounds(self):
        assert self.rx_slots <= self.timing.rx_queue_packets
        assert len(self.accumulators) <= self.timing.reduction_entries
        assert len(self.done_fifo) <= self.timing.done_queue_packets
        self.counters['max_rx_slots'] = max(self.counters['max_rx_slots'],self.rx_slots)
        self.counters['max_reduction_entries'] = max(self.counters['max_reduction_entries'],len(self.accumulators))
        self.counters['max_done_queue'] = max(self.counters['max_done_queue'],len(self.done_fifo))

    def _fail(self,status,detail):
        self.error = status
        if status == StatusCode.DUPLICATE_RESULT: self.duplicate_errors += 1
        raise TransactionError(status,detail)

    def _expected_mask(self,index):
        method=getattr(self.config,'source_mask_for',None)
        return method(index) if method else self.config.source_mask

    def _validate(self,p):
        c=self.config
        if self.error is not None: raise TransactionError(self.error,"transaction already terminated")
        if self.done is not None: self._fail(StatusCode.PROTOCOL_ERROR,"RESULT after global completion")
        if p.tid!=c.tid or p.dst_tile!=c.root_tile:
            self._fail(StatusCode.CONFIG_MISS,"tid or destination mismatch")
        if p.status!=StatusCode.OK: self._fail(StatusCode.PROTOCOL_ERROR,"RESULT has error status")
        kind='PSUM' if c.mode=='REDUCE_PSUM' else 'FINAL'
        if p.kind!=kind: self._fail(StatusCode.KIND_MISMATCH,"RESULT kind does not match transaction")
        if type(p.src_tile) is not int or not 0<=p.src_tile<getattr(c,'tile_count',4) or not c.source_mask & (1<<p.src_tile):
            self._fail(StatusCode.BAD_TILE,"unexpected source")
        if type(p.lane_mask) is not int or not 0<p.lane_mask<16 or len(p.data)!=4:
            self._fail(StatusCode.BAD_FIELD,"invalid lane mask or payload")
        elements=[(p.base_index+lane,p.data[lane]) for lane in range(4) if p.lane_mask & (1<<lane)]
        for index,value in elements:
            if not c.output_base<=index<c.output_base+c.output_count:
                self._fail(StatusCode.BAD_FIELD,"output index outside transaction")
            if not self._expected_mask(index)&(1<<p.src_tile):
                self._fail(StatusCode.BAD_TILE,"source does not own this output index")
            mask=self.accepted_source_masks.get(index,0)
            if (mask & (1<<p.src_tile)) or (kind=='FINAL' and mask):
                self._fail(StatusCode.DUPLICATE_RESULT,"duplicate accepted output/source")
            if type(value) is not int or not -(1<<31)<=value<(1<<31):
                self._fail(StatusCode.BAD_FIELD,"output outside signed INT32")
        if self.accepted_last_mask & (1<<p.src_tile):
            self._fail(StatusCode.PROTOCOL_ERROR,"RESULT after source last")
        return elements

    def accept(self,packet,*,reserved=False):
        if self.error is not None:
            raise TransactionError(self.error,'transaction already terminated')
        if reserved:
            if not self.header_reserved: raise RuntimeError("RESULT tail has no reserved slot")
        elif not self.can_accept(packet): raise BufferError("Root RX packet slots are full")
        elements=self._validate(packet)
        # Validate the WHOLE packet before reserving duplicate/source metadata.
        for index,_ in elements:
            self.accepted_source_masks[index]=self.accepted_source_masks.get(index,0)|(1<<packet.src_tile)
        if packet.last: self.accepted_last_mask |= 1<<packet.src_tile
        if reserved: self.header_reserved=False
        self.rx.append(replace(packet,data=tuple(packet.data)))
        self.counters['accepted_packets']+=1
        self._check_bounds()
        return None

    def _start_chunk(self):
        self.chunk=self.elements[self.position:self.position+self.timing.lanes_per_cycle]
        self.updated=[]
        if self.config.mode=='REDUCE_PSUM': self.stage,self.remaining='READ',self.timing.read_cycles
        else:
            self.updated=[(i,v,0) for i,v in self.chunk]
            self.stage,self.remaining='WRITE_RESULT',self.timing.write_cycles

    def _next_chunk(self):
        self.position+=len(self.chunk)
        if self.position<len(self.elements): self._start_chunk()
        else: self.stage='PACKET_DONE'

    def write_request(self):
        """Valid on the final service edge; hold this vector until write_ready."""
        if self.error is not None or self.stage!='WRITE_RESULT' or self.remaining!=1:
            return None
        values=[(i,v) for i,v,mask in self.updated
                if self.config.mode=='GATHER_FINAL' or mask==self._expected_mask(i)]
        if not values: return None
        base=min(i for i,_ in values)
        data=[0]*4
        for index,value in values: data[index-base]=value
        return RootWriteRequest(Y_START+4*(base-self.config.output_base),
                                sum(1<<(i-base) for i,_ in values),tuple(data))

    def step(self,*,done_ready=True,write_ready=True):
        if self.error is not None: raise TransactionError(self.error,"transaction already terminated")
        cycle=self.cycle
        stage='DISPATCH' if self.stage=='IDLE' and self.rx else self.stage
        write_request=self.write_request()
        write_blocked=write_request is not None and not write_ready
        if write_blocked: self.counters['result_write_stall_cycles']+=1
        self.stage_cycles[stage]+=1
        observed_packet=self.rx[0] if stage=='DISPATCH' else self.active
        before=dict(cycle=cycle,stage=stage,rx_slots=self.rx_slots,queued_packets=len(self.rx),
            header_reserved=self.header_reserved,active=self.active is not None,
            reduction_entries=len(self.accumulators),done_valid=bool(self.done_fifo),
            done_ready=bool(done_ready),write_valid=write_request is not None,
            write_ready=bool(write_ready),write_accepted=write_request is not None and bool(write_ready),
            indices=[i for i,_ in self.chunk] if self.active else [],
            source=observed_packet.src_tile if observed_packet is not None else None,
            packet_base_index=observed_packet.base_index if observed_packet is not None else None)
        if self.rx_slots==self.timing.rx_queue_packets: self.counters['rx_full_cycles']+=1
        if len(self.accumulators)==self.timing.reduction_entries: self.counters['reduction_full_cycles']+=1
        accepted=created=committed=None
        if self.done_fifo:
            if done_ready:
                accepted=self.done_fifo.popleft()
                self.counters['done_accepted_count']+=1
            else: self.counters['done_stall_cycles']+=1
        if stage=='DISPATCH':
            self.active=self.rx.popleft()
            self.elements=[(self.active.base_index+i,self.active.data[i]) for i in range(4) if self.active.lane_mask&(1<<i)]
            self.position=0
            self._start_chunk()
        elif stage in ('READ','ADD','UPDATE','WRITE_RESULT') and not write_blocked:
            self.remaining-=1
            if self.remaining==0:
                if stage=='READ':
                    self.read_values=[(i,self.accumulators.get(i,0),self.received_source_masks.get(i,0),v) for i,v in self.chunk]
                    before['read_values']=list(self.read_values)
                    self.counters['read_elements']+=len(self.chunk)
                    self.stage,self.remaining='ADD',self.timing.add_cycles
                elif stage=='ADD':
                    self.updated=[(i,int32_wrap(old+value),mask|(1<<self.active.src_tile)) for i,old,mask,value in self.read_values]
                    before['updated_values']=list(self.updated)
                    self.counters['add_elements']+=len(self.chunk)
                    self.stage,self.remaining='UPDATE',self.timing.update_cycles
                elif stage=='UPDATE':
                    for index,value,mask in self.updated:
                        self.accumulators[index]=value
                        self.received_source_masks[index]=mask
                    self.psum_elements+=len(self.chunk)
                    self.counters['partial_update_elements']+=len(self.chunk)
                    if any(mask==self._expected_mask(index) for index,_,mask in self.updated):
                        self.stage,self.remaining='WRITE_RESULT',self.timing.write_cycles
                    else: self._next_chunk()
                else:
                    written=[]
                    for index,value,mask in self.updated:
                        if self.config.mode=='GATHER_FINAL' or mask==self._expected_mask(index):
                            offset=4*(index-self.config.output_base)
                            self.root.result_sram[offset:offset+4]=value.to_bytes(4,'little',signed=True)
                            self.values[index]=value
                            self.accumulators.pop(index,None)
                            written.append(index)
                    self.counters['result_write_elements']+=len(written)
                    before['written_indices']=written
                    self._next_chunk()
        elif stage=='PACKET_DONE':
            committed=self.active
            if self.active.last: self.last_mask |= 1<<self.active.src_tile
            self.counters['processed_packets']+=1
            self.active=None
            self.chunk=[]
            self.stage=('CREATE_DONE' if len(self.values)==self.config.output_count and
                        self.last_mask==self.config.source_mask else 'IDLE')
        elif stage=='CREATE_DONE':
            if len(self.done_fifo)<self.timing.done_queue_packets:
                created=TransactionDonePacket(self.config.root_tile,self.config.host_tile,self.config.tid,
                                              self.config.output_count,self.config.source_mask)
                self.done=created
                self.done_fifo.append(created)
                self.counters['done_created_count']+=1
                self.stage='IDLE'
        self._check_bounds()
        before.update(rx_slots_after=self.rx_slots,reduction_entries_after=len(self.accumulators),
                      done_valid_after=bool(self.done_fifo),completed_outputs=len(self.values))
        self.last_event=before
        self.cycle+=1
        return RootCycle(cycle,committed,created,accepted)

    def snapshot(self,packet):
        return [dict(index=packet.base_index+lane,
                     acc32=self.values.get(packet.base_index+lane,self.accumulators.get(packet.base_index+lane,0)),
                     received_source_mask=self.received_source_masks.get(packet.base_index+lane,0),
                     complete=packet.base_index+lane in self.values)
                for lane in range(4) if packet.lane_mask&(1<<lane)]

    def idle(self):
        return not self.rx and not self.header_reserved and self.active is None and not self.done_fifo and self.stage=='IDLE'

    def stats(self):
        return dict(elapsed_cycles=self.cycle,**self.counters,stage_cycles=dict(self.stage_cycles),
                    service_cycles=self.cycle-self.stage_cycles['IDLE'])
