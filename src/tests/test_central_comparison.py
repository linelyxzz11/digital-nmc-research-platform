"""Numeric, flow-control and resource-contract checks for central vs NMC."""
import random
import pytest
from simulator.system.central_comparison import run_comparison, StreamPacket, StreamCodec


def workload(m, k):
    rng = random.Random(1701)
    return [[rng.randrange(-128,128) for _ in range(k)] for _ in range(m)], [rng.randrange(-128,128) for _ in range(k)]


@pytest.mark.parametrize('architecture', ['central','nmc'])
@pytest.mark.parametrize('node', [0,36])
@pytest.mark.parametrize('interval', [1,3])
def test_signed_tail_output_arrives_at_host(architecture,node,interval):
    w,x=workload(13,19)
    r=run_comparison(w,x,architecture=architecture,workers=(1,2,8,9),
                     central_tile=node,link_interval=interval,prefetch_packets=1,sink_interval=7)
    assert r['y']==[sum(a*b for a,b in zip(row,x)) for row in w]
    assert r['useful_mac_ops']==13*19
    assert r['network_payload_bytes']['result']==13*4
    assert r['network_payload_bytes']['input']==4*19
    assert r['router_max_flits']<=4
    assert r['max_input_rx_packets']<=2 and r['max_result_rx_packets']<=2
    assert r['total_cycles']>=r['host_output_ready_cycles']
    assert r['total_cycles']==r['input_ready_cycle']+r['kernel_to_host_cycles']
    if architecture=='central':
        assert r['network_payload_bytes']['weight']==r['weight_bank_read_bytes']
        assert r['max_weight_outstanding_packets']<=1
    else:
        assert r['network_payload_bytes'].get('weight',0)==0


def test_prefetch_window_changes_performance_not_arithmetic():
    w,x=workload(16,64)
    common=dict(architecture='central',workers=(7,56),central_tile=0,link_interval=2)
    small=run_comparison(w,x,prefetch_packets=1,**common)
    large=run_comparison(w,x,prefetch_packets=8,**common)
    assert small['y']==large['y']
    assert large['total_cycles'] < small['total_cycles']
    assert large['max_weight_outstanding_packets']>1


def test_nmc_ignores_unused_central_endpoint_location():
    w,x=workload(8,16)
    a=run_comparison(w,x,architecture='nmc',workers=(1,2),central_tile=0)
    b=run_comparison(w,x,architecture='nmc',workers=(1,2),central_tile=36)
    assert a['total_cycles']==b['total_cycles'] and a['flit_hops']==b['flit_hops']


def test_stream_codec_preserves_signed_bytes_and_addresses():
    codec=StreamCodec()
    for kind in range(1,5):
        p=StreamPacket(63,36,kind,62,4092,b'' if kind==4 else bytes(range(240,256)))
        assert codec.unpack(codec.pack(p))==p
    with pytest.raises(ValueError):
        codec.pack(StreamPacket(64,0,1,1,0,b'a'))


def test_capacity_and_input_errors_are_not_silently_truncated():
    with pytest.raises(ValueError):
        run_comparison([[128]],[1],architecture='nmc',workers=(1,))
    with pytest.raises(ValueError):
        run_comparison([[1]],[1],architecture='nmc',workers=(0,))
    with pytest.raises(ValueError):
        run_comparison([[1]*512 for _ in range(16)],[1]*512,architecture='central',workers=(1,))
