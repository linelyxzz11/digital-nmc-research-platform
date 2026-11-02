"""Independently audit saved RTL traces and produce paired, measured figures."""
from __future__ import annotations
import argparse,csv,hashlib,json,math,sys
from collections import Counter,defaultdict
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.rtl.run_mesh_rtl import golden_gemv,int32_wrap,json_hash,inputs_for,expected_results
from compiler.grid_mapper import map_grid
from simulator.communication import MemAckPacket,TransactionDonePacket,ResultPacket
from simulator.communication.scalable import PacketCodec
from simulator.communication.v0_1 import Flit,FlitType

def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def csvrows(path):
    with Path(path).open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def audit_case(folder,item,record):
    data=load(folder/'workload.json');W,x=data['W'],data['x'];gold=golden_gemv(W,x)
    assert data['sha256']==json_hash(dict(W=W,x=x))==record['workload_sha256']
    assert data['golden']==gold and len(W)==item['m'] and len(x)==item['k']
    assert all(-128<=v<=127 for row in W for v in row) and all(-128<=v<=127 for v in x)
    plan=map_grid(item['m'],item['k'],grid=item['grid'],placement=item['placement'],reduction=item['reduction'])
    assert plan.digest==record['plan_sha256']==json_hash(load(folder/'plan.json'))
    memory=csvrows(folder/'memory.csv');assert [int(r['index']) for r in memory]==list(range(plan.m))
    assert [int32_wrap(int(r['data'],16)) for r in memory]==gold
    phase=record['rtl_phase_cycles'][-1]['phase'];codec=PacketCodec(64);partial=defaultdict(list);packets=[];events=[]
    for r in csvrows(folder/'packets.csv'):
        if int(r['phase'])!=phase:continue
        node=int(r['node']);f=Flit(FlitType(int(r['type'])),int(r['data'],16));partial[node].append(f)
        if f.type in (FlitType.TAIL,FlitType.HEADTAIL):
            p=codec.unpack(partial[node]);partial[node]=[];assert p.dst_tile==node
            packets.append(p);events.append((int(r['cycle']),p))
    assert not any(partial.values())
    inputs=inputs_for(plan,x);mask=0
    for _,_,bits in plan.root_ranges:mask|=bits
    expected=inputs+[MemAckPacket(p.dst_tile,0,1,p.sequence) for p in inputs]+expected_results(plan,W,x)+[TransactionDonePacket(0,0,1,plan.m,mask)]
    normalize=lambda p:json.dumps(dict(type=type(p).__name__,**asdict(p)),sort_keys=True)
    assert Counter(map(normalize,packets))==Counter(map(normalize,expected))
    done=[c for c,p in events if isinstance(p,TransactionDonePacket)];assert len(done)==1
    assert all(c<=done[0] for c,p in events if isinstance(p,ResultPacket))
    covered={};last_write=-1
    for r in csvrows(folder/'root_writes.csv'):
        if int(r['phase'])!=phase or int(r['node'])!=0:continue
        last_write=max(last_write,int(r['cycle']));base=(int(r['address'],16)-0x2000)//4
        value=int(r['data'],16);mask=int(r['mask'],16)
        for lane in range(4):
            if not mask&(1<<lane):continue
            index=base+lane;assert index not in covered
            covered[index]=int32_wrap((value>>(32*lane))&0xffffffff)
    assert covered==dict(enumerate(gold)) and last_write<done[0]
    observations=[{k:int(v) for k,v in r.items()} for r in csvrows(folder/'observations.csv') if int(r['phase'])==phase]
    assert len(observations)==64 and {r['node'] for r in observations}==set(range(64))
    assert all(r['max_router']<=4 and r['max_ni']<=4 and r['max_rx']<=2 for r in observations)
    assert all(v>=0 for r in observations for v in r.values())
    model=load(folder/'python_model.json');assert model['y']==gold and model['plan_sha256']==plan.digest
    rtl=record['rtl_phase_cycles'][-1];assert rtl['acks']==len(inputs) and rtl['dones']==1 and not rtl['aborted']
    assert all(r['cycles']==rtl['input_cycles']+rtl['kernel_cycles'] for r in observations)
    assert sum(r['link_flits'] for r in observations)==model['network']['flit_hops']
    counts=Counter(type(p).__name__ for p in packets)
    root=next(r for r in observations if r['node']==0)
    row=dict(name=record['name'],m=plan.m,k=plan.k,policy=item.get('policy','boundary'),seed=item.get('workload_seed'),
      grid=str(tuple(plan.grid)),workers=len(plan.workers),reducers=len(plan.reducers),physical_tiles=64,
      plan_sha256=plan.digest,workload_sha256=data['sha256'],
      rtl_input=rtl['input_cycles'],rtl_kernel=rtl['kernel_cycles'],rtl_total=rtl['input_cycles']+rtl['kernel_cycles'],
      python_input=model['phase_cycles']['input_load'],python_kernel=record['model_kernel_cycles'],python_total=model['cycles'],
      input_requests=counts['MemWritePacket'],acks=counts['MemAckPacket'],result_packets=counts['ResultPacket'],done=counts['TransactionDonePacket'],
      result_psum=sum(isinstance(p,ResultPacket) and p.kind=='PSUM' for p in packets),
      result_final=sum(isinstance(p,ResultPacket) and p.kind=='FINAL' for p in packets),
      rtl_link_flits=sum(r['link_flits'] for r in observations),ni_stall_tile_cycles=sum(r['ni_stall_cycles'] for r in observations),
      router_blocked_port_cycles=sum(r['router_blocked_port_cycles'] for r in observations),
      endpoint_service_tile_cycles=sum(r['endpoint_service_cycles'] for r in observations),
      root_service_cycles=root['endpoint_service_cycles'],root_write_stall_cycles=sum(r['root_write_stall_cycles'] for r in observations),
      mac_group_steps=sum(r['mac_group_steps'] for r in observations),
      max_router=max(r['max_router'] for r in observations),max_ni=max(r['max_ni'] for r in observations),max_rx=max(r['max_rx'] for r in observations),
      evidence_level='integrated_rtl',numeric_protocol_and_resource_audit=True,source_folder=str(folder))
    for phase_name in ('input','kernel','total'):
        delta=row['rtl_'+phase_name]-row['python_'+phase_name]
        row[phase_name+'_delta']=delta;row[phase_name+'_relative_vs_python']=delta/row['python_'+phase_name]
    return row

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input-dir',type=Path,required=True);p.add_argument('--manifest',type=Path,default=ROOT/'ini/rtl_gemv_20261031.json');p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    manifest=load(a.manifest);items={c['name']:c for c in manifest['cases']};found={};source_hashes={}
    for label in ['core_161_unindp-compact','core_161_noc-v04','core_251_unindp-compact','core_251_noc-v04','boundary']:
        result=load(a.input_dir/label/'results.json');assert result['passed']==result['cases']
        for name,value in result['source_sha256'].items():
            normalized=name.replace('\\','/');current=hashlib.sha256((ROOT/normalized).read_bytes()).hexdigest()
            assert current==value,('source changed since measurement',normalized)
            source_hashes[normalized]=value
        for r in result['results']:
            assert r['name'] not in found
            found[r['name']]=audit_case(a.input_dir/label/r['name'],items[r['name']],r)
    assert set(found)==set(items)
    rows=list(found.values());primary=[r for r in rows if r['seed']==20261031];paired=[]
    for m,k in [(161,97),(251,127)]:
        for seed in [20261031,20261101]:
            baseline=next(r for r in rows if (r['m'],r['k'],r['seed'],r['policy'])==(m,k,seed,'unindp-compact'))
            noc=next(r for r in rows if (r['m'],r['k'],r['seed'],r['policy'])==(m,k,seed,'noc-v04'))
            assert baseline['workload_sha256']==noc['workload_sha256']
            paired.append(dict(m=m,k=k,seed=seed,S_total=baseline['rtl_total']/noc['rtl_total'],S_kernel=baseline['rtl_kernel']/noc['rtl_kernel'],
              total_reduction_percent=100*(baseline['rtl_total']-noc['rtl_total'])/baseline['rtl_total'],
              kernel_reduction_percent=100*(baseline['rtl_kernel']-noc['rtl_kernel'])/baseline['rtl_kernel'],
              input_saved=baseline['rtl_input']-noc['rtl_input'],kernel_cost=noc['rtl_kernel']-baseline['rtl_kernel'],total_saved=baseline['rtl_total']-noc['rtl_total']))
    for m,k in [(161,97),(251,127)]:
        for policy in ('unindp-compact','noc-v04'):
            selected=[r for r in rows if (r['m'],r['k'],r['policy'])==(m,k,policy)]
            assert len({(r['rtl_input'],r['rtl_kernel'],r['rtl_total']) for r in selected})==1
    a.output_dir.mkdir(parents=True,exist_ok=True)
    for name,data in [('comparison.csv',rows),('paired.csv',paired)]:
        with (a.output_dir/name).open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    summary=dict(rows=rows,paired=paired,cases=len(rows),core_executions=8,boundary_executions=2,primary_cases=4,source_sha256=source_hashes,
      manifest_sha256=hashlib.sha256(a.manifest.read_bytes()).hexdigest(),scope='actual Icarus 64-Tile RTL; offline fixed mappings; setup/weight preload excluded; kernel includes completion; seeds are correctness repetitions')
    (a.output_dir/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    draw(primary,[r for r in paired if r['seed']==20261031],a.output_dir)
    print(json.dumps(dict(cases=len(rows),paired=paired),indent=2))

def draw(rows,paired,out):
    import matplotlib;matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.size':10,'svg.fonttype':'none'})
    order=[next(r for r in rows if (r['m'],r['policy'])==(m,p)) for m in [161,251] for p in ['unindp-compact','noc-v04']]
    labels=[f"{r['m']}x{r['k']}\n{'Baseline (32)' if r['workers']==32 else 'v0.4 (16)'}" for r in order]
    figdir=out/'figure';figdir.mkdir(exist_ok=True)
    footer='64 physical Tiles | actual RTL cycles | seed 20261031 | weight/config setup excluded'
    def save(fig,name):
        fig.text(.5,.012,footer,ha='center',fontsize=9);fig.tight_layout(rect=(0,.065,1,1))
        for ext in ['png','svg']:fig.savefig(figdir/(name+'.'+ext),dpi=160)
        plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,5));x=np.arange(2);w=.34
    for i,policy in enumerate(['unindp-compact','noc-v04']):
        values=[next(r['rtl_total'] for r in rows if r['m']==m and r['policy']==policy) for m in [161,251]]
        bars=ax.bar(x+(i-.5)*w,values,w,label='Baseline / 32 workers' if i==0 else 'NoC v0.4 / 16 workers');ax.bar_label(bars,padding=3)
    ax.set_xticks(x,['161x97','251x127']);ax.set_ylabel('Measured total cycles');ax.set_title('Paired GEMV latency on the real 8x8 RTL mesh');ax.legend(loc='upper left');ax.set_ylim(0,max(r['rtl_total'] for r in rows)*1.32)
    for i,r in enumerate(paired):ax.text(i,max(v['rtl_total'] for v in rows if v['m']==r['m'])*1.09,f"Speedup {r['S_total']:.4f}x / cycles -{r['total_reduction_percent']:.2f}%",ha='center')
    save(fig,'01_rtl_paired_latency')
    fig,ax=plt.subplots(figsize=(10,5));x=np.arange(4)
    ax.bar(x,[r['rtl_input'] for r in order],label='Input MEM_WRITE / SRAM / ACK')
    ax.bar(x,[r['rtl_kernel'] for r in order],bottom=[r['rtl_input'] for r in order],label='Kernel + reduction + completion')
    ax.set_xticks(x,labels);ax.set_ylabel('Measured non-overlapping RTL cycles');ax.set_title('Measured input and kernel windows (drain not separately split)');ax.legend();save(fig,'02_rtl_phases')
    fig,axes=plt.subplots(1,2,figsize=(13,5));x=np.arange(4)
    for ax,phase in zip(axes,['input','kernel']):
        for i,system in enumerate(['python','rtl']):
            bars=ax.bar(x+(i-.5)*.36,[r[system+'_'+phase] for r in order],.36,label='Timed Python evaluator' if i==0 else 'Actual RTL');ax.bar_label(bars,padding=2,fontsize=8)
        ax.set_xticks(x,labels,fontsize=8);ax.set_ylabel('Cycles');ax.set_title('Input: different service/driver contract' if phase=='input' else 'Matched kernel + drain: registered RTL pipeline');ax.legend(fontsize=8);ax.set_ylim(0,max(r['rtl_'+phase] for r in rows)*1.3)
    save(fig,'03_python_vs_rtl')
    fig,axes=plt.subplots(1,3,figsize=(16,5));x=np.arange(4)
    for ax,key,title,unit in zip(axes,['rtl_link_flits','result_packets','router_blocked_port_cycles'],['Measured directional-link traffic','Measured ejected RESULT / PSUM packets','Measured Router output backpressure'],['Link-flit transfers','Packets','Blocked port-cycles (overlapping)']):
        bars=ax.bar(x,[r[key] for r in order],color=['#4c78a8','#f58518']*2);ax.bar_label(bars,padding=3,fontsize=8)
        ax.set_xticks(x,labels,fontsize=8);ax.set_title(title);ax.set_ylabel(unit);ax.set_ylim(0,max(r[key] for r in order)*1.25+1)
    save(fig,'04_rtl_communication')
if __name__=='__main__':main()
