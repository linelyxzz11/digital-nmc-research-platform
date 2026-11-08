"""Sequential per-Tile task queues built from the calibrated Compute model."""

from simulator.functional import TileTask
from simulator.functional.demo0_model import CycleSignals
from simulator.system.tile_config import TileComputeConfig
from simulator.system.tile_timing import PortScheduledCompute


class QueuedCompute:
    """Run single-destination TileTasks in order and expose one Worker Done."""

    def __init__(self, tile, tasks, timing=TileComputeConfig(), *,
                 task_switch_cycles=1):
        tasks=tuple(tasks)
        if (not tasks or any(not isinstance(task,TileTask) for task in tasks) or
            any(task.tile_id!=tile.physical_tile_id for task in tasks) or
            len({task.tid for task in tasks})!=1):
            raise ValueError('a task queue needs same-Tile/same-TID TileTasks')
        if not isinstance(timing,TileComputeConfig):
            raise ValueError('timing must be TileComputeConfig')
        if type(task_switch_cycles) is not int or task_switch_cycles<1:
            raise ValueError('task_switch_cycles must be a positive integer')
        self.tile,self.tasks,self.timing=tile,tasks,timing
        self.task_switch_cycles=task_switch_cycles
        self._index=0
        self._current=self._new_compute(tasks[0])
        self._switch_remaining=0
        self._switch_cycles=0
        self._completed=0
        self._child_stats=[]
        self.finished=False

    def _new_compute(self,task):
        return PortScheduledCompute(self.tile,task,self.timing.memory,
            operand_slots=self.timing.operand_slots,
            mac_interval=self.timing.mac_interval)

    @property
    def current_task(self):
        return self._current.task if self._current is not None else None

    def signals(self):
        if self._current is None:
            return CycleSignals(False,None,False,None)
        raw=self._current.signals()
        intermediate=raw.local_done_valid and self._index+1<len(self.tasks)
        return CycleSignals(raw.result_valid,raw.result_payload,
            raw.local_done_valid and not intermediate,
            raw.local_done_payload if raw.local_done_valid and not intermediate else None)

    def _archive_current(self):
        self._child_stats.append(self._current.stats())
        self._completed+=1
        self._current=None

    def step(self,*,result_ready,local_done_ready=True):
        before=self.signals()
        if self.finished:
            return before
        if self._current is None:
            if self._switch_remaining:
                self._switch_remaining-=1
                self._switch_cycles+=1
                if self._switch_remaining==0:
                    self._index+=1
                    self._current=self._new_compute(self.tasks[self._index])
            return before
        raw=self._current.signals()
        intermediate=raw.local_done_valid and self._index+1<len(self.tasks)
        if intermediate:
            self._current.step(result_ready=result_ready,local_done_ready=True)
            self._archive_current()
            self._switch_remaining=self.task_switch_cycles
            return before
        self._current.step(result_ready=result_ready,local_done_ready=local_done_ready)
        if raw.local_done_valid and local_done_ready:
            self._archive_current()
            self.finished=True
        return before

    def stats(self):
        children=list(self._child_stats)
        if self._current is not None:
            children.append(self._current.stats())
        aggregate={}
        stage_cycles={}
        for stats in children:
            for key,value in stats.items():
                if key=='stage_cycles':
                    for stage,cycles in value.items():
                        stage_cycles[stage]=stage_cycles.get(stage,0)+cycles
                elif key=='lane_slot_utilization':
                    continue
                elif key.startswith('max_'):
                    aggregate[key]=max(aggregate.get(key,0),value)
                else:
                    aggregate[key]=aggregate.get(key,0)+value
        elapsed=aggregate.get('local_elapsed_cycles',0)+self._switch_cycles
        aggregate['local_elapsed_cycles']=elapsed
        aggregate['lane_slot_utilization']=(
            aggregate.get('useful_mac_ops',0)/(4*elapsed) if elapsed else 0)
        aggregate['stage_cycles']=stage_cycles
        aggregate['subtasks']=len(self.tasks)
        aggregate['completed_subtasks']=self._completed
        aggregate['task_switch_cycles']=self._switch_cycles
        return aggregate
