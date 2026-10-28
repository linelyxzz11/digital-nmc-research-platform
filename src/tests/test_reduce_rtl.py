"""Clock-for-clock comparison of the new endpoint with bounded TimedRoot."""
import csv
from pathlib import Path
import shutil
import subprocess

import pytest

from simulator.communication.scalable import PacketCodec
from simulator.communication.v0_1 import Flit,FlitType
from simulator.functional import Demo0Tile
from simulator.system.scalable import GridGatherConfig
from simulator.system.root_config import RootTiming
from simulator.system.timed_endpoint import TimedRoot


@pytest.mark.skipif(any(not shutil.which(tool) for tool in ('iverilog','vvp')),
                    reason='install Icarus for endpoint RTL verification')
def test_reduce_rtl_matches_every_python_service_edge_and_rejects_malformed_packets(tmp_path):
    root=Path(__file__).resolve().parents[2]
    executable=tmp_path/'reduce.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_nmc_reduce','-o',str(executable),
                    str(root/'src/rtl/nmc_mesh/nmc_reduce.sv'),
                    str(root/'src/rtl/nmc_mesh/tb_nmc_reduce.sv')],check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(executable),f'+CASE_DIR={tmp_path}'],check=True,
                          capture_output=True,text=True,timeout=20)
    assert 'REDUCE RTL PASS: 5 malformed cases' in result.stdout
    mask=(1<<31)|(1<<63)
    config=GridGatherConfig(9,3,0,mask,5,5,'REDUCE_PSUM',64,((5,10,mask),))
    model=TimedRoot(config,Demo0Tile(3,tile_count=64),RootTiming())
    states={'IDLE':0,'READ':1,'ADD':2,'UPDATE':3,'WRITE_RESULT':4,'PACKET_DONE':5,'CREATE_DONE':6}
    blocked=False
    with (tmp_path/'trace.csv').open() as stream:
        rows=list(csv.DictReader(stream))
    for index,row in enumerate(rows):
        assert int(row['cycle'])==index
        assert int(row['state'])==states[model.stage],(index,row,model.stage)
        assert int(row['queued'])==len(model.rx)
        assert bool(int(row['done_valid']))==bool(model.done_fifo)
        request=model.write_request()
        assert bool(int(row['write_valid']))==(request is not None)
        if request is not None:
            assert int(row['address'],16)==request.address
            assert int(row['mask'],16)==request.lane_mask
            assert int(row['data'],16)==request.packed_data
            blocked |= not bool(int(row['write_ready']))
        model.step(write_ready=bool(int(row['write_ready'])),done_ready=bool(int(row['done_ready'])))
        if int(row['rx_valid']) and int(row['rx_ready']):
            packet=PacketCodec(64).unpack([Flit(FlitType.HEAD,int(row['header'],16)),
                                         Flit(FlitType.TAIL,int(row['payload'],16))])
            model.accept(packet)
    assert blocked and model.idle()
    assert model.values=={5:-2147483644,6:-1,7:-4,8:1,9:-1}
    assert model.stats()['max_rx_slots']==2
