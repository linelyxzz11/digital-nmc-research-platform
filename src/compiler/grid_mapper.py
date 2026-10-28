"""Multi-worker ROW/K/2D plans sharing the frozen TileTask and SRAM layout."""
from dataclasses import asdict, dataclass
import hashlib
import json
import random

from compiler.auto_mapper_v0_1 import MappingError, _resources
from simulator.functional import TileTask


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
class GridPlan:
    mesh_x: int
    mesh_y: int
    m: int
    k: int
    grid: tuple[int,int]
    workers: tuple[int,...]
    host_tile: int
    root_tile: int
    reduction: str
    fan_in: int
    tid: int
    placement: str
    seed: int
    slices: tuple[Slice,...]
    tasks: tuple[TileTask,...]
    reducers: tuple[Reducer,...]
    root_ranges: tuple[tuple[int,int,int],...]

    @property
    def tile_count(self): return self.mesh_x*self.mesh_y

    @property
    def root_mode(self):
        return 'REDUCE_PSUM' if self.reduction=='central' and self.grid[1]>1 else 'GATHER_FINAL'

    def to_dict(self):
        return dict(plan_version='0.2',**asdict(self))

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.to_dict(),sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def validate(self):
        expected=map_grid(self.m,self.k,mesh_x=self.mesh_x,mesh_y=self.mesh_y,grid=self.grid,
            workers=self.workers,root_tile=self.root_tile,host_tile=self.host_tile,
            reduction=self.reduction,fan_in=self.fan_in,placement=self.placement,seed=self.seed,tid=self.tid)
        if self!=expected: raise MappingError('plan tasks/ranges/reduction graph disagree with its specification')


def _bounds(size,parts):
    return [(i*size+parts-1)//parts for i in range(parts+1)]


def map_grid(m,k,*,mesh_x=8,mesh_y=8,grid=(4,8),workers=None,root_tile=0,
             host_tile=0,reduction='central',fan_in=4,placement='compact',seed=0,tid=1):
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
    if rows>m or cols>k: raise MappingError('grid would contain empty shards')
    if reduction not in ('central','tree') or fan_in<2:
        raise MappingError('choose central/tree reduction with fan_in>=2')
    if placement not in ('compact','spread','random'):
        raise MappingError('unknown placement')
    distance=lambda a,b:abs(a%mesh_x-b%mesh_x)+abs(a//mesh_x-b//mesh_x)
    available=[i for i in range(n) if i not in (host_tile,root_tile)]
    count=rows*cols
    if workers is None:
        if placement=='compact': available.sort(key=lambda i:(distance(i,root_tile),i))
        elif placement=='random': random.Random(seed).shuffle(available)
        else:
            selected=[];remaining=set(available)
            while remaining:
                choice=max(remaining,key=lambda i:(min(distance(i,s) for s in selected) if selected else distance(i,root_tile),-i))
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
    free=set(available)-set(workers);parents={};nodes=[];root_ranges=[]
    for row in range(rows):
        children=list(workers[row*cols:(row+1)*cols])
        if reduction=='tree':
            while len(children)>1:
                next_level=[]
                for offset in range(0,len(children),fan_in):
                    group=children[offset:offset+fan_in]
                    if len(group)==1: next_level.extend(group);continue
                    if not free: raise MappingError('reduction tree exceeds the same physical Tile budget')
                    node=min(free,key=lambda t:(sum(distance(t,c) for c in group)+distance(t,root_tile),t))
                    free.remove(node)
                    for child in group: parents[child]=node
                    nodes.append((node,tuple(group),rb[row],rb[row+1]));next_level.append(node)
                children=next_level
        for child in children: parents[child]=root_tile
        root_ranges.append((rb[row],rb[row+1],sum(1<<c for c in children)))
    reducers=tuple(Reducer(tile,children,start,end,parents[tile]) for tile,children,start,end in nodes)
    tasks=tuple(TileTask(tid,s.tile_id,s.row_end-s.row_start,s.k_end-s.k_start,
        global_row_base=s.row_start,result_kind='FINAL' if cols==1 else 'PSUM',
        result_dst_tile=parents[s.tile_id]) for s in slices)
    return GridPlan(mesh_x,mesh_y,m,k,tuple(grid),workers,host_tile,root_tile,reduction,fan_in,
                    tid,placement,seed,tuple(slices),tasks,reducers,tuple(root_ranges))
