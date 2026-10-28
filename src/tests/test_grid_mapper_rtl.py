"""Feed new multi-worker Mapper leaves into the existing calibrated Tile RTL."""
from dataclasses import replace
from pathlib import Path
import runpy
import shutil

import pytest

from compiler.grid_mapper import map_grid


@pytest.mark.skipif(any(not shutil.which(tool) for tool in ('iverilog','vvp','verilator')),
                    reason='install RTL simulators for the Mapper-to-RTL bridge')
def test_grid_mapper_leaf_tasks_match_existing_tile_rtl(tmp_path):
    root=Path(__file__).resolve().parents[2]
    runner=runpy.run_path(str(root/'tools/rtl/run_tile_rtl.py'))
    plan=map_grid(13,37,grid=(3,4),reduction='tree',placement='spread')
    matrix=[[((i*17+k*13)%256)-128 for k in range(37)] for i in range(13)]
    vector=[127-(k%255) for k in range(37)]
    template=runner['cases'](0)[0]
    cases=[]
    for index in (0,5,11):
        shard=plan.slices[index];task=plan.tasks[index]
        # RTL v0.1's physical-ID validator covers 0..3. This test remaps only
        # endpoint identities; arithmetic, layout, global index and timing stay.
        cases.append(dict(template,name=f'grid_leaf_{index}',
            matrix=[row[shard.k_start:shard.k_end] for row in matrix[shard.row_start:shard.row_end]],
            vector=vector[shard.k_start:shard.k_end],
            task=replace(task,tile_id=1,result_dst_tile=3)))
    result=runner['run_regression'](tmp_path/'grid_mapper_rtl',seeds=0,synthesis=False,case_list=cases)
    assert result['passed']==result['cases']==result['phases']==3
    assert all(r['phases'][0]['cycle_trace_match'] and r['phases'][0]['sram_readback_match'] for r in result['results'])
