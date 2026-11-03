"""Exercise actual 64-node RTL, including hierarchy, pressure and reset."""
from pathlib import Path
import runpy
import shutil

import pytest


@pytest.mark.skipif(any(not shutil.which(tool) for tool in ('iverilog','vvp','verilator')),
                    reason='install RTL simulators for integrated Mesh verification')
def test_integrated_64_tile_rtl(tmp_path):
    root = Path(__file__).resolve().parents[2]
    runner = runpy.run_path(str(root/'tools/rtl/run_mesh_rtl.py'))
    result = runner['run_regression'](tmp_path/'mesh_rtl')
    assert result['passed']==result['cases']==9
    assert all(row['numeric_match'] and row['wire_packets_match'] and row['transaction_done']==1
               for row in result['results'])
    assert any(row['reset_replayed'] for row in result['results'])
    assert max(row['active_compute'] for row in result['results'])==32
