"""Actual Root vector port: bandwidth, arbitration, reset and Python write edges."""
from pathlib import Path
import runpy
import shutil

import pytest


@pytest.mark.skipif(any(not shutil.which(tool) for tool in ('iverilog','vvp')),
                    reason='install Icarus Verilog for Root writeback RTL verification')
def test_root_writeback_rtl_matches_finite_endpoint_and_preserves_banks(tmp_path,monkeypatch):
    directory=Path(__file__).resolve().parents[2]/'tools/rtl'
    monkeypatch.syspath_prepend(str(directory))
    runner=runpy.run_path(str(directory/'run_root_writeback_rtl.py'))
    result=runner['run_regression'](tmp_path/'root_writeback_rtl',seeds=8)
    assert result['calibration_cases']==51
    assert result['requests']==218
    assert result['rejected']>=10
    assert result['burst']==dict(vectors=16,cycles=16,int32_words=64,bytes_per_cycle=16)
    assert result['compute_result_cycles']==[9,19] and result['compute_done_cycle']==23
    assert result['stalled_root_cycles']>30
