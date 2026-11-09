"""Independent beat timelines, finite reservations and integrated gateway tests."""
import random
import pytest
from simulator.system.central_comparison import StreamPacket, run_comparison
from simulator.system.central_fabric import CentralFabric


def test_128_byte_packet_needs_nine_beats_and_one_propagation_cycle():
    f=CentralFabric((36,),hub=36,depth=1)
    p=StreamPacket(1,36,2,1,0,bytes(128))
    received=[]
    assert f.submit('in',36,p)
    assert not f.submit('in',36,p)
    for cycle in range(9):
        f.tick(cycle,ready=lambda *args: True,deliver=lambda *args: received.append(args))
        assert not received
        assert not f.can_submit('in',36)
    f.tick(9,ready=lambda *args: True,deliver=lambda *args: received.append(args))
    assert received==[('in',36,p)]
    assert f.beats['in'][36]==9 and f.idle()


def test_real_two_port_throughput_and_shared_fabric_bottleneck():
    finishes={}
    for lanes in (1,4):
        f=CentralFabric((36,37),hub=36,lanes=lanes)
        for node in f.entries:
            assert f.submit('in',node,StreamPacket(1,node,2,node,0,bytes(128)))
        received=[]
        for cycle in range(30):
            f.tick(cycle,ready=lambda *args: True,
                   deliver=lambda d,n,p: received.append((cycle,n)))
            if f.idle():break
        finishes[lanes]=received
        assert f.max_cycle_beats['in']==min(lanes,2)
    assert finishes[4]==[(9,36),(10,37)]
    assert finishes[1]==[(17,36),(19,37)]


def test_destination_slot_reservation_survives_inflight_packets():
    f=CentralFabric((36,37),hub=36)
    for node in f.entries:
        f.submit('in',node,StreamPacket(0,node,1,1,node,bytes(16)))
    sink=[];received=[]
    for cycle in range(12):
        if cycle==5:sink.clear()
        def deliver(d,n,p):
            sink.append(p);received.append((cycle,p))
        f.tick(cycle,ready=lambda d,n,p,reserved: len(sink)+reserved<1,deliver=deliver)
        assert len(sink)+f.reserved[('in',1,1)]<=1
    assert [cycle for cycle,_ in received]==[2,8]
    assert f.destination_wait['in']>0
    assert f.idle()


@pytest.mark.parametrize('entries',[(36,),(36,37),(36,37,44,45)])
@pytest.mark.parametrize('lanes',[1,4])
@pytest.mark.parametrize('interval',[1,3])
def test_gateway_system_signed_tails_and_backpressure(entries,lanes,interval):
    rng=random.Random(20261109)
    w=[[rng.randrange(-128,128) for _ in range(67)] for _ in range(13)]
    x=[rng.randrange(-128,128) for _ in range(67)]
    r=run_comparison(w,x,architecture='central',central_tile=36,workers=(1,2,8,9),
                     central_entries=entries,fabric_lanes=lanes,gateway_queue_packets=1,
                     weight_packet_bytes=128,prefetch_packets=1,
                     link_interval=interval,sink_interval=11)
    assert r['y']==[sum(a*b for a,b in zip(row,x)) for row in w]
    assert r['useful_mac_ops']==13*67
    assert r['max_gateway_rx_packets']<=1 and r['max_gateway_tx_packets']<=1
    assert r['max_weight_reserved_bytes']<=128
    assert r['max_ni_flits']<=4 and r['router_max_flits']<=4
    f=r['fabric'];wire=r['network_injected_flits']
    assert sum(f['transferred_flits']['in'].values())==wire['input']+wire['weight']
    assert sum(f['transferred_flits']['out'].values())==wire['credit']+wire['result']
    assert sum(r['entry_rx_flits'].values())==wire['input']+wire['weight']
    assert all(v<=min(lanes,len(entries)) for v in f['max_flits_per_cycle'].values())
    assert set(r['entry_for_worker'].values())==set(entries)
    assert r['total_cycles']>=r['host_output_ready_cycles']


def test_gateway_validation_rejects_resource_collisions():
    for entries in [(36,36),(36,0),(36,1),(37,36),(36,37,44)]:
        with pytest.raises(ValueError):
            run_comparison([[1]],[1],architecture='central',central_tile=36,
                           workers=(1,),central_entries=entries)
    with pytest.raises(ValueError):
        run_comparison([[1]],[1],architecture='nmc',workers=(1,),
                       central_tile=36,central_entries=(36,))
    with pytest.raises(ValueError):
        CentralFabric((36,),hub=36,lanes=5)
