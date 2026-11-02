"""Audit measured records and regenerate six figure groups; never synthesize data."""
from __future__ import annotations
import argparse,csv,gzip,hashlib,io,json,math,statistics,sys,tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.experiments.scalable_experiments import verified,digest,save,seal
from tools.experiments.validation_campaign import independent_golden,workload,POLICIES,SHAPES,HARDWARE
from compiler.mapping_search import catalogue
def audit(directory,protocol):
    records=[];seen=set()
    expected={(p,m,k) for p in HARDWARE if p!='default' for m,k in protocol['sensitivity_shapes']}
    expected|={('default',m,k) for m,k in protocol['shapes']+protocol['application_shapes']}
    for path in directory.glob('*.json'):
        if path.name.endswith('.ranking.json') or path.name in ('walltime.json','unsupported.json'):continue
        r=verified(path);assert r['protocol_sha256']==protocol['sha256']
        identity=r['hardware_point'],r['m'],r['k']
        assert identity not in seen,'duplicate case';seen.add(identity)
        assert r['seed']==protocol['seed']
        W,x=workload(r['m'],r['k'],r['seed']);assert digest(dict(W=W,x=x))==r['workload_sha256']
        plans=catalogue(r['m'],r['k']);assert set(r['measured'])=={p.digest for p in plans.plans}
        assert r['attempted']==plans.attempted and r['feasible']==len(plans.plans)
        ranking=verified(directory/(r['run_id']+'.ranking.json'));assert ranking['policies']==r['policies']
        for plan in plans.plans:
            a=r['measured'][plan.digest]['metrics'];assert digest(r['plans'][plan.digest])==digest(plan.to_dict())
            assert a['y']==independent_golden(W,x) and a['transaction_done']==1
            assert a['tile_timing']==r['tile_timing'] and a['root_timing']==r['root_timing']
            assert a['plan_sha256']==plan.digest and a['cycles']==sum(a['phase_cycles'].values())
            assert a['network']['max_fifo_occupancy']<=4
            assert a['input_acks']==a['packet_counts']['MemWritePacket']==a['packet_counts']['MemAckPacket']
            assert a['packet_counts']['TransactionDonePacket']==1
            for endpoint in a['endpoint_stats'].values():
                assert endpoint['max_rx_slots']<=r['root_timing']['rx_queue_packets']
                assert endpoint['max_done_queue']<=r['root_timing']['done_queue_packets']
                assert endpoint['max_output_slots']<=1
        optimum=min(v['metrics']['cycles'] for v in r['measured'].values())
        assert len(r['quality'])==len(POLICIES)*len(protocol['budgets'])
        for q in r['quality']:
            if q['status']!='ok':continue
            prefix=r['policies'][q['policy']]['ranking'][:q['budget']]
            assert q['cycles']==min(r['measured'][p['plan_sha256']]['metrics']['cycles'] for p in prefix)
            assert q['oracle_cycles']==optimum and q['oracle_hit']==(q['cycles']==optimum)
        records.append(r)
    assert {(r['hardware_point'],r['m'],r['k']) for r in records}==expected,'missing/unknown cases'
    return records
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input-dir',type=Path,required=True);p.add_argument('--protocol',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    protocol=verified(a.protocol);records=audit(a.input_dir,protocol);o=a.output_dir;o.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(records,sort_keys=True).encode();(o/'records.json.gz').write_bytes(gzip.compress(raw,mtime=0))
    data=[r for r in records if r['hardware_point']=='default' and (r['m'],r['k']) in SHAPES]
    data.sort(key=lambda r:SHAPES.index((r['m'],r['k'])))
    def q(r,policy,b=1):return next(x for x in r['quality'] if x['policy']==policy and x['budget']==b)
    groups=[]
    for b in [1,2,4]:
        for policy in POLICIES:
            eligible=[r for r in data if q(r,policy,b)['status']=='ok' and q(r,'unindp-compact',b)['status']=='ok']
            ratios=[q(r,'unindp-compact',b)['cycles']/q(r,policy,b)['cycles'] for r in eligible]
            groups.append(dict(policy=policy,budget=b,cases=len(eligible),excluded=len(data)-len(eligible),
              geomean_speedup=math.exp(statistics.mean(map(math.log,ratios))),wins=sum(v>1 for v in ratios),ties=sum(v==1 for v in ratios),losses=sum(v<1 for v in ratios),
              worst_speedup=min(ratios),oracle_hits=sum(q(r,policy,b)['oracle_hit'] for r in eligible),max_regret=max(q(r,policy,b)['regret'] for r in eligible)))
    error_groups=[]
    for point in HARDWARE:
        for policy in ('noc-v03','noc-v04'):
            rows=[e for r in records if r['hardware_point']==point for e in r['phase_errors'] if e['policy']==policy]
            error_groups.append(dict(hardware_point=point,policy=policy,candidates=len(rows),**{f'{phase}_{metric}': (statistics.mean(abs(e[phase+'_relative']) for e in rows) if metric=='mape' else max(abs(e[phase+'_relative']) for e in rows)) for phase in ('total','input','kernel') for metric in ('mape','max')}))
    wall=verified(a.input_dir/'walltime.json');timings=[]
    for policy in POLICIES:
        samples=[s for s in wall['samples'] if not s['warmup'] and s['policy']==policy];assert len(samples)==5
        for b in [1,2,4]:
            vals=[s['budgets'][str(b)] for s in samples];quartiles=statistics.quantiles(vals,n=4,method='inclusive')
            timings.append(dict(policy=policy,budget=b,samples=5,median=statistics.median(vals),q1=quartiles[0],q3=quartiles[2],minimum=min(vals),maximum=max(vals)))
    summary=seal(dict(new_shapes=len(data),attempted=sum(r['attempted'] for r in data),feasible=sum(r['feasible'] for r in data),
      all_cases=len(records),groups=groups,error_groups=error_groups,timings=timings,protocol_sha256=protocol['sha256'],evidence_level='timed_python_evaluator',
      records_sha256=hashlib.sha256((o/'records.json.gz').read_bytes()).hexdigest()))
    save(o/'summary.json',summary)
    phase_rows=[]
    for r in records:
        for e in r['phase_errors']:
            actual=r['measured'][e['plan_sha256']]['metrics']
            estimate=next(v['estimate'] for v in r['policies'][e['policy']]['ranking'] if v['plan_sha256']==e['plan_sha256'])
            phase_rows.append(dict(run_id=r['run_id'],m=r['m'],k=r['k'],hardware_point=r['hardware_point'],**e,
              actual_total=actual['cycles'],predicted_total=estimate['cycles'],actual_input=actual['phase_cycles']['input_load'],
              predicted_input=estimate['input_cycles'],actual_kernel=actual['phase_cycles']['compute_and_gather']+actual['phase_cycles']['completion_drain'],predicted_kernel=estimate['compute_gather_cycles']))
    with (o/'phase_errors.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(phase_rows[0]));w.writeheader();w.writerows(phase_rows)
    rows=[dict(run_id=r['run_id'],m=r['m'],k=r['k'],hardware_point=r['hardware_point'],**v) for r in records for v in r['quality']]
    with (o/'quality.csv').open('w',newline='',encoding='utf-8') as f:
        fields=list(dict.fromkeys(k for r in rows for k in r));w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    import matplotlib;matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.size':10,'svg.fonttype':'none'})
    figs=o/'figure';figs.mkdir(exist_ok=True)
    scope='64 physical Tiles | resident weights | timed Python evaluator'
    def finish(fig,name):
        fig.text(.5,.005,scope,ha='center',fontsize=9);fig.tight_layout(rect=(0,.035,1,.95 if fig._suptitle else 1))
        for ext in ['png','svg']:fig.savefig(figs/(name+'.'+ext),dpi=150)
        plt.close(fig)
    x=np.arange(len(data));labels=[f"{r['m']}x{r['k']}" for r in data]
    fig,axes=plt.subplots(2,3,figsize=(18,8),sharex=True)
    fig.suptitle('New 12-shape validation / fixed hardware / ratio below 1 is faster',fontsize=13)
    for column,b in enumerate([1,2,4]):
        ax=axes[0,column]
        for pi,policy in enumerate(POLICIES[1:]):
            vals=[q(r,policy,b)['cycles']/q(r,'unindp-compact',b)['cycles'] if q(r,policy,b)['status']=='ok' else float('nan') for r in data]
            ax.plot(x,vals,marker=['o','s','^','D'][pi],label=policy)
        ax.set_yscale('log');ax.axhline(1,color='gray',ls='--');ax.set_xticks(x,labels,rotation=90);ax.set_title(f'All policies / budget {b}');ax.set_ylabel('cycles / compact baseline (log scale)')
        zoom=axes[1,column]
        for policy,marker in [('noc-v03','o'),('noc-v04','s')]:
            zoom.plot(x,[q(r,policy,b)['cycles']/q(r,'unindp-compact',b)['cycles'] for r in data],marker=marker,label=policy)
        zoom.axhline(1,color='gray',ls='--');zoom.set_xticks(x,labels,rotation=90);zoom.set_title(f'Primary policies / budget {b}');zoom.set_ylabel('cycles / compact baseline (linear zoom)')
    axes[0,-1].legend(fontsize=8,loc='upper left',bbox_to_anchor=(1.01,1));finish(fig,'01_latency_ratios')
    fig,ax=plt.subplots(figsize=(12,5));bottom=np.zeros(len(data))
    for phase in ['input_load','compute_and_gather','completion_drain']:
        vals=[r['measured'][q(r,'noc-v04')['plan_sha256']]['metrics']['phase_cycles'][phase] for r in data]
        ax.bar(x,vals,bottom=bottom,label=phase);bottom+=vals
    ax.set_xticks(x,labels,rotation=60);ax.set_ylabel('non-overlapping model cycles');ax.set_title('NoC v0.4 selected plan / budget 1 / new 12 shapes');ax.legend();finish(fig,'02_phases')
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    for policy in POLICIES:
        g=[v for v in groups if v['policy']==policy]
        axes[0].plot([v['budget'] for v in g],[v['max_regret'] for v in g],'-o',label=policy)
        axes[1].plot([v['budget'] for v in g],[v['oracle_hits']/v['cases'] for v in g],'-o',label=policy)
    axes[0].set_ylabel('maximum regret vs finite catalogue');axes[1].set_ylabel('top-K oracle hit fraction (ties accepted)')
    axes[0].set_yscale('symlog',linthresh=.01)
    axes[0].set_ylabel('maximum regret (symlog; linear within +/-0.01)')
    for ax in axes:ax.set_xlabel('exact evaluator budget');ax.set_xticks([1,2,4]);ax.set_title('New 12 shapes / n=12 per policy')
    axes[1].legend(fontsize=8,loc='upper left',bbox_to_anchor=(1.01,1));finish(fig,'03_budget_quality')
    fig,axes=plt.subplots(1,3,figsize=(18,5))
    for policy in POLICIES:
        t=[v for v in timings if v['policy']==policy];axes[0].errorbar([v['budget'] for v in t],[v['median'] for v in t],
          yerr=[[v['median']-v['q1'] for v in t],[v['q3']-v['median'] for v in t]],marker='o',label=policy)
    axes[0].set_xlabel('exact budget');axes[0].set_ylabel('search seconds: median / IQR');axes[0].set_title('81x81 only / 5 timed samples + 1 warmup');axes[0].legend(fontsize=8)
    base=np.zeros(5)
    for key,label in [('generation_seconds','generation'),('ranking','prediction incl. proxy'),('evaluation_seconds','4 numerical evaluations')]:
        vals=[]
        for policy in POLICIES:
            s=[v for v in wall['samples'] if not v['warmup'] and v['policy']==policy]
            vals.append(statistics.median(v[key]['ranking_seconds'] if key=='ranking' else sum(v[key]) if key=='evaluation_seconds' else v[key] for v in s))
        axes[1].bar(range(5),vals,bottom=base,label=label);base+=vals
    axes[1].set_xticks(range(5),POLICIES,rotation=30);axes[1].set_ylabel('median component seconds');axes[1].set_title('Proxy cost included in prediction; component medians');axes[1].legend(fontsize=8)
    shape=next(r for r in data if (r['m'],r['k'])==(81,81))
    for policy in POLICIES:
        t=[v for v in timings if v['policy']==policy]
        axes[2].plot([v['median'] for v in t],[q(shape,policy,v['budget'])['regret'] for v in t],'-o',label=policy)
    axes[2].set_xlabel('median total search seconds');axes[2].set_ylabel('regret vs finite catalogue');axes[2].set_title('81x81 quality vs search cost / budgets 1,2,4');axes[2].legend(fontsize=8)
    finish(fig,'04_search_cost')
    fig,axes=plt.subplots(1,3,figsize=(14,4))
    for ax,phase in zip(axes,['input','kernel','total']):
        for policy in ('noc-v03','noc-v04'):
            vals=[100*e[phase+'_relative'] for r in data for e in r['phase_errors'] if e['policy']==policy]
            ax.hist(vals,bins=30,alpha=.55,label=policy)
        ax.set_xlabel(f'{phase}: signed prediction error (%)');ax.set_ylabel('candidate count');ax.set_title('New 12 shapes / n=292 per predictor')
    axes[-1].legend();finish(fig,'05_prediction_errors')
    fig,axes=plt.subplots(1,2,figsize=(13,4))
    for ax,shape in zip(axes,protocol['sensitivity_shapes']):
        ss=[next(r for r in records if [r['m'],r['k']]==shape and r['hardware_point']==p) for p in HARDWARE]
        for policy in POLICIES:
            ax.plot(range(len(ss)),[q(r,policy)['cycles'] if q(r,policy)['status']=='ok' else float('nan') for r in ss],'-o',label=policy)
        ax.set_xticks(range(len(ss)),list(HARDWARE),rotation=30);ax.set_ylabel('selected model cycles / budget 1');ax.set_title(f'{shape[0]}x{shape[1]} / one variable per point')
    axes[-1].legend(fontsize=8);finish(fig,'06_sensitivity')
    print(json.dumps({k:summary[k] for k in ['new_shapes','attempted','feasible','all_cases']}))
if __name__=='__main__':main()
