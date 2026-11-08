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


@pytest.mark.parametrize('burst', [16, 32, 64, 128])
@pytest.mark.parametrize('interval', [1, 3])
def test_weight_bursts_keep_fixed_byte_budget_with_partial_tail(burst, interval):
    w,x=workload(16,67)  # Each worker reads 268 B: partial final packet at every size.
    r=run_comparison(w,x,architecture='central',workers=(1,2,8,9),
                     central_tile=36,weight_packet_bytes=burst,
                     prefetch_packets=128//burst,link_interval=interval,sink_interval=7)
    assert r['y']==[sum(a*b for a,b in zip(row,x)) for row in w]
    packets=4*((268+burst-1)//burst)
    assert r['network_injected_flits']['credit']==packets
    assert r['network_injected_flits']['weight']==packets+4*((268+15)//16)
    assert r['network_payload_bytes']['weight']==16*67
    assert r['prefetch_bytes_per_cluster']==128
    assert r['max_weight_reserved_bytes']<=128
    assert r['max_ni_flits']<=4 and r['router_max_flits']<=4
    assert r['source_tx_capacity_bytes']==128
    assert r['source_assembly_capacity_bytes']==128
    if burst>=64:
        assert r['max_serializer_pending_flits']>0


def test_long_packet_serializer_survives_blocked_destination_without_interleaving():
    from simulator.system.central_comparison import StreamInjector
    from simulator.communication.v0_1 import Mesh2x2
    codec=StreamCodec()
    mesh=Mesh2x2(codec=codec,ni_depth=4,fifo_depth=4)
    sender=StreamInjector(mesh,codec)
    first=StreamPacket(1,0,2,1,0,bytes(range(128)))
    second=StreamPacket(1,0,2,1,128,bytes(range(64)))
    assert sender.submit(first)
    assert not sender.submit(second)
    sent_second=False
    for cycle in range(200):
        mesh.ejection_ready[0]=cycle>=50
        sender.tick()
        if not sent_second:
            sent_second=sender.submit(second)
        mesh.step()
        assert all(len(q)<=4 for q in mesh.ni_injection)
        if cycle<50:
            assert not mesh.received_packets[0]
        if sent_second and sender.idle() and mesh.idle():
            break
    else:
        pytest.fail('long packets did not drain after backpressure was released')
    assert mesh.received_packets[0]==[first,second]
    assert mesh.max_occupancy<=4


def test_long_codec_rejects_truncation_and_wrong_body_type():
    from simulator.communication.v0_1 import Flit,FlitType
    codec=StreamCodec()
    for size in (20,32,64,128):
        p=StreamPacket(63,36,2,62,4092,bytes(range(size)))
        flits=codec.pack(p)
        assert codec.unpack(flits)==p
        with pytest.raises(ValueError):
            codec.unpack(flits[:-1])
        wrong=list(flits);wrong[1]=Flit(FlitType.HEAD,wrong[1].data)
        with pytest.raises(ValueError):
            codec.unpack(wrong)
    with pytest.raises(ValueError):
        codec.pack(StreamPacket(1,0,2,1,0,bytes(129)))
    with pytest.raises(ValueError):
        run_comparison([[1]],[1],architecture='central',workers=(1,),weight_packet_bytes=48)
