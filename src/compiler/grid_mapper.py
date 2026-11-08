"""Multi-worker ROW/K/2D plans sharing the frozen TileTask and SRAM layout."""
from dataclasses import asdict, dataclass
import hashlib
import json
from math import ceil
import random

from compiler.auto_mapper_v0_1 import MappingError, _resources
from simulator.functional import TileTask
from simulator.functional.demo0_model import W_START


@dataclass(frozen=True)
class Slice:
    tile_id: int
    row_start: int
    row_end: int
    k_start: int
    k_end: int


@dataclass(frozen=True)
class Reducer:
    tile_id: int
    sources: tuple[int,...]
    row_start: int
    row_end: int
    parent: int


@dataclass(frozen=True)
class FinalRootRange:
    root_tile: int
    row_start: int
    row_end: int
    source_mask: int


@dataclass(frozen=True)
class TileTaskQueue:
    tile_id: int
    tasks: tuple[TileTask,...]


@dataclass(frozen=True)
class GridPlan:
    mesh_x: int
    mesh_y: int
    m: int
    k: int
    grid: tuple[int,int]
    workers: tuple[int,...]
    host_tile: int
    root_tile: int
    final_roots: tuple[int,...]
    reduction: str
    fan_in: int
    tid: int
    placement: str
    seed: int
    slices: tuple[Slice,...]
    tasks: tuple[TileTask,...]
    task_queues: tuple[TileTaskQueue,...]
    reducers: tuple[Reducer,...]
    root_ranges: tuple[tuple[int,int,int],...]
    final_root_ranges: tuple[FinalRootRange,...]

    @property
    def tile_count(self): return self.mesh_x*self.mesh_y

    @property
    def root_mode(self):
        return 'REDUCE_PSUM' if self.reduction=='central' and self.grid[1]>1 else 'GATHER_FINAL'

    def to_dict(self):
        payload=asdict(self)
        if self.final_roots==(self.root_tile,):
            payload.pop('final_roots');payload.pop('final_root_ranges');payload.pop('task_queues')
            return dict(plan_version='0.2',**payload)
        if self.grid[1]==1:
            payload.pop('task_queues')
            return dict(plan_version='0.3',**payload)
        if self.grid[0]==1:
            return dict(plan_version='0.4',**payload)
        payload.pop('task_queues')
        return dict(plan_version='0.5',**payload)

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.to_dict(),sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def validate(self):
        expected=map_grid(self.m,self.k,mesh_x=self.mesh_x,mesh_y=self.mesh_y,grid=self.grid,
            workers=self.workers,root_tile=self.root_tile,host_tile=self.host_tile,
            final_roots=self.final_roots,
            reduction=self.reduction,fan_in=self.fan_in,placement=self.placement,seed=self.seed,tid=self.tid)
        if self!=expected: raise MappingError('plan tasks/ranges/reduction graph disagree with its specification')


def _bounds(size,parts):
    return [(i*size+parts-1)//parts for i in range(parts+1)]


def map_grid(m,k,*,mesh_x=8,mesh_y=8,grid=(4,8),workers=None,root_tile=0,
             final_roots=None,host_tile=0,reduction='central',fan_in=4,
             placement='compact',seed=0,tid=1):
    values=(m,k,mesh_x,mesh_y,root_tile,host_tile,fan_in,seed,tid)
    if any(type(v) is not int for v in values) or m<1 or k<1 or mesh_x<1 or mesh_y<1:
        raise MappingError('dimensions and identifiers must be integers with positive dimensions')
    n=mesh_x*mesh_y
    if n>64 or not 0<=root_tile<n or not 0<=host_tile<n or not 0<=tid<=65535:
        raise MappingError('v0.2 needs 1..64 Tiles and valid host/root/tid')
    if m>256 or k>65535 or seed<0:
        raise MappingError('single-transaction output is limited to 256 INT32 words')
    if not isinstance(grid,(tuple,list)) or len(grid)!=2 or any(type(v) is not int or v<1 for v in grid):
        raise MappingError('grid must contain positive row/K partitions')
    rows,cols=grid
    if final_roots is None:
        final_roots=(root_tile,)
    elif not isinstance(final_roots,(tuple,list)):
        raise MappingError('final_roots must be a sequence')
    else:
        final_roots=tuple(final_roots)
    if (len(final_roots) not in (1,2,4) or len(set(final_roots))!=len(final_roots) or
        final_roots[0]!=root_tile or
        any(type(root) is not int or not 0<=root<n for root in final_roots)):
        raise MappingError('final_roots must contain 1/2/4 distinct valid Tiles and start with root_tile')
    multi_row=len(final_roots)>1 and cols==1
    multi_k=len(final_roots)>1 and rows==1 and cols>1
    multi_2d=len(final_roots)>1 and rows>1 and cols>1
    if len(final_roots)>1 and not (multi_row or multi_k or multi_2d):
        raise MappingError('multi-root needs pure ROW, pure K, or 2D partitioning')
    if multi_row and len(final_roots)>rows:
        raise MappingError('each final Root needs at least one ROW worker')
    if multi_k and reduction!='central':
        raise MappingError('K multi-root v0.1 supports central reduction only')
    if multi_k and len(final_roots)>ceil(m/4):
        raise MappingError('each final Root needs at least one four-row group')
    if multi_2d and reduction!='central':
        raise MappingError('2D multi-root v0.1 supports central reduction only')
    if multi_2d and len(final_roots)>rows:
        raise MappingError('each final Root needs at least one ROW partition')
    if rows>m or cols>k: raise MappingError('grid would contain empty shards')
    if reduction not in ('central','tree') or fan_in<2:
        raise MappingError('choose central/tree reduction with fan_in>=2')
    if placement not in ('compact','spread','random'):
        raise MappingError('unknown placement')
    distance=lambda a,b:abs(a%mesh_x-b%mesh_x)+abs(a//mesh_x-b//mesh_x)
    reserved=set(final_roots)|{host_tile}
    available=[i for i in range(n) if i not in reserved]
    count=rows*cols
    if workers is None:
        distance_to_roots=lambda tile:min(distance(tile,root) for root in final_roots)
        if placement=='compact': available.sort(key=lambda i:(distance_to_roots(i),i))
        elif placement=='random': random.Random(seed).shuffle(available)
        else:
            selected=[];remaining=set(available)
            while remaining:
                choice=max(remaining,key=lambda i:(min(distance(i,s) for s in selected) if selected else distance_to_roots(i),-i))
                selected.append(choice);remaining.remove(choice)
            available=selected
        workers=tuple(available[:count])
    else: workers=tuple(workers)
    if (len(workers)!=count or len(set(workers))!=count or
        any(type(t) is not int or t not in available for t in workers)):
        raise MappingError('not enough distinct compute Tiles outside host/root')
    rb,kb=_bounds(m,rows),_bounds(k,cols)
    slices=[]
    for row in range(rows):
        for column in range(cols):
            tile=workers[row*cols+column]
            _resources(tile,rb[row+1]-rb[row],kb[column+1]-kb[column])
            slices.append(Slice(tile,rb[row],rb[row+1],kb[column],kb[column+1]))
    root_group_bounds=_bounds(rows,len(final_roots)) if not multi_k else (0,rows)
    owner_for_row=({row:root for root_index,root in enumerate(final_roots)
                    for row in range(root_group_bounds[root_index],root_group_bounds[root_index+1])}
                   if not multi_k else {0:root_tile})
    free=set(available)-set(workers);parents={};nodes=[];root_ranges=[]
    for row in range(rows):
        children=list(workers[row*cols:(row+1)*cols])
        row_owner=owner_for_row[row]
        if reduction=='tree':
            while len(children)>1:
                next_level=[]
                for offset in range(0,len(children),fan_in):
                    group=children[offset:offset+fan_in]
                    if len(group)==1: next_level.extend(group);continue
                    if not free: raise MappingError('reduction tree exceeds the same physical Tile budget')
                    node=min(free,key=lambda t:(sum(distance(t,c) for c in group)+distance(t,row_owner),t))
                    free.remove(node)
                    for child in group: parents[child]=node
                    nodes.append((node,tuple(group),rb[row],rb[row+1]));next_level.append(node)
                children=next_level
        for child in children: parents[child]=row_owner
        root_ranges.append((rb[row],rb[row+1],sum(1<<c for c in children)))
    reducers=tuple(Reducer(tile,children,start,end,parents[tile]) for tile,children,start,end in nodes)
    final_root_ranges=[]
    if multi_k:
        group_bounds=_bounds(ceil(m/4),len(final_roots))
        row_bounds=[min(4*group,m) for group in group_bounds]
        source_mask=sum(1<<worker for worker in workers)
        for root_index,root in enumerate(final_roots):
            final_root_ranges.append(FinalRootRange(
                root,row_bounds[root_index],row_bounds[root_index+1],source_mask))
    else:
        for root_index,root in enumerate(final_roots):
            first,last=root_group_bounds[root_index],root_group_bounds[root_index+1]
            source_mask=0
            for row in range(first,last): source_mask|=root_ranges[row][2]
            final_root_ranges.append(FinalRootRange(root,rb[first],rb[last],source_mask))
    if multi_k:
        task_queues=[]
        for worker_index,shard in enumerate(slices):
            offset=worker_index%len(final_root_ranges)
            owners=final_root_ranges[offset:]+final_root_ranges[:offset]
            k_local=shard.k_end-shard.k_start
            queue=tuple(TileTask(tid,shard.tile_id,owner.row_end-owner.row_start,k_local,
                w_base=W_START+4*(owner.row_start//4)*k_local,
                global_row_base=owner.row_start,result_kind='PSUM',
                result_dst_tile=owner.root_tile,task_last=True) for owner in owners)
            task_queues.append(TileTaskQueue(shard.tile_id,queue))
        tasks=tuple(task for queue in task_queues for task in queue.tasks)
    else:
        tasks=tuple(TileTask(tid,s.tile_id,s.row_end-s.row_start,s.k_end-s.k_start,
            global_row_base=s.row_start,result_kind='FINAL' if cols==1 else 'PSUM',
            result_dst_tile=parents[s.tile_id]) for s in slices)
        task_queues=[TileTaskQueue(task.tile_id,(task,)) for task in tasks]
    return GridPlan(mesh_x,mesh_y,m,k,tuple(grid),workers,host_tile,root_tile,final_roots,
                    reduction,fan_in,tid,placement,seed,tuple(slices),tasks,tuple(task_queues),reducers,
                    tuple(root_ranges),tuple(final_root_ranges))
