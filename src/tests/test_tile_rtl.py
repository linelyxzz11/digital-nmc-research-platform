"""RTL co-simulation is optional locally, mandatory in the Linux CI job."""
from pathlib import Path
import runpy
import shutil

import pytest


@pytest.mark.skipif(any(not shutil.which(tool) for tool in ('iverilog','vvp','verilator')),
                    reason='install Icarus Verilog and Verilator for RTL co-simulation')
def test_single_tile_rtl_matches_python_and_preserves_sram_on_reset(tmp_path):
    runner=runpy.run_path(str(Path(__file__).resolve().parents[2]/'tools/rtl/run_tile_rtl.py'))
    result=runner['run_regression'](tmp_path/'tile_rtl',seeds=8,synthesis=bool(shutil.which('yosys')))
    assert result['passed']==result['cases']==47
    assert result['phases']==53
    hand=next(row for row in result['results'] if row['name']=='hand_5x3')['phases'][0]
    assert hand['cycles']==15 and hand['result_handshake_cycles']==[6,13]
