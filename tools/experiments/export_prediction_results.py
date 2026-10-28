#!/usr/bin/env python3
"""Export validated prediction results, editable plots and a source snapshot."""
import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from tools.experiments.prediction_validation import check_case,fingerprint
from tools.experiments.scalable_experiments import verified,digest


def export(summary_path,replay_path,inputs,output):
    summary=verified(summary_path);development=verified(replay_path)
    version=fingerprint()
    if digest(summary['version'])!=digest(version) or digest(development['version'])!=digest(version):
        raise ValueError('published sources differ from evaluated predictor')
    fresh=[]
    for folder in inputs:
        for path in sorted(folder.glob('*.result.json')):
            record=verified(path,'result_sha256');check_case(record,version);fresh.append(record)
    if len(fresh)!=summary['fresh_cases'] or len({(r['m'],r['k']) for r in fresh})!=len(fresh):
        raise ValueError('missing/duplicate fresh records')
    output.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(summary_path,output/'summary.json')
    shutil.copyfile(summary_path.parent/'quality.csv',output/'quality.csv')
    raw=json.dumps(dict(fresh=fresh,development=development),sort_keys=True,separators=(',',':')).encode()
    (output/'records.json.gz').write_bytes(gzip.compress(raw,mtime=0))
    cases=fresh+development['cases'];errors=[];kernel=[]
    for case in cases:
        load={r['plan_sha256']:r['estimate']['input_cycles'] for r in case['policies']['noc-v04']['ranking']}
        for row in case['phase_errors']:
            actual_kernel=row['actual_cycles']-load[row['plan_sha256']]
            errors.append(dict(m=case['m'],k=case['k'],split=case['split'],**row,
                               actual_kernel_cycles=actual_kernel,kernel_relative_error=row['kernel_error']/actual_kernel))
    for split in sorted({r['split'] for r in errors}):
        for policy in ('noc-v03','noc-v04'):
            selected=[r for r in errors if r['split']==split and r['policy']==policy]
            kernel.append(dict(split=split,policy=policy,candidates=len(selected),
                kernel_mape=sum(abs(r['kernel_relative_error']) for r in selected)/len(selected),
                max_kernel_relative_error=max(abs(r['kernel_relative_error']) for r in selected)))
    (output/'kernel_errors.json').write_text(json.dumps(kernel,indent=2)+'\n')
    with (output/'phase_errors.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(errors[0]));writer.writeheader();writer.writerows(errors)
    # The original eight fresh cases were collected on one frozen revision.
    commits=sorted({r['commit'] for r in fresh})
    (output/'provenance.json').write_text(json.dumps(dict(frozen_commits=commits,
        source_sha256=version['source_sha256'],protocol=version['protocol'],
        protocol_source_sha256=version['protocol_source_sha256'],
        records_sha256=hashlib.sha256((output/'records.json.gz').read_bytes()).hexdigest(),
        export_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),indent=2)+'\n')
    files={ROOT/name for name in version['source_sha256']}
    files.add(ROOT/'ini/prediction_v0_4.json');files.add(Path(__file__).resolve())
    for folder in ('src/tests','src/rtl/nmc_tile','src/rtl/nmc_mesh'):
        files.update(p for p in (ROOT/folder).rglob('*') if p.suffix in ('.py','.sv'))
    for name in ('tools/rtl/run_mesh_rtl.py','tools/rtl/run_tile_rtl.py','tools/rtl/run_root_writeback_rtl.py',
                 '.github/workflows/verify.yml','.gitattributes','docs/spec/bounded_prediction_v0.4.md'):
        files.add(ROOT/name)
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w') as archive:
        for path in sorted(files):
            data=path.read_bytes();info=tarfile.TarInfo(path.relative_to(ROOT).as_posix())
            info.size=len(data);info.mode=0o644;info.mtime=0;archive.addfile(info,io.BytesIO(data))
    (output/'source_snapshot.tar.gz').write_bytes(gzip.compress(buffer.getvalue(),mtime=0))
    draw(summary,errors,output)
    print(f"Exported {len(fresh)} fresh cases, {len(development['cases'])} development replays; kernel errors: {kernel}")


def draw(summary,errors,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.labelsize':8,
        'svg.fonttype':'none','pdf.fonttype':42,'ps.fonttype':42,
        'axes.spines.top':False,'axes.spines.right':False})
    figures=output/'figure';figures.mkdir(exist_ok=True)
    def save_figure(fig,stem):
        for suffix in ('svg','pdf','png'):
            path=figures/f'{stem}.{suffix}';fig.savefig(path,dpi=240)
            if suffix=='svg': path.write_text('\n'.join(line.rstrip() for line in path.read_text().splitlines())+'\n')
        plt.close(fig)
    rows=[r for r in summary['results'] if r['split']=='fresh' and r['budget']==1]
    shapes=sorted({(r['m'],r['k']) for r in rows})
    costs={(r['m'],r['k'],r['policy']):r['cycles'] for r in rows}
    fig,ax=plt.subplots(figsize=(183/25.4,83/25.4),layout='constrained')
    ax.axhline(1,color='0.45',linestyle='--',linewidth=.8,label='Strong compact baseline')
    ratios=[]
    for policy,color,marker,offset in (('noc-v03','#D55E00','s',-.1),('noc-v04','#0072B2','o',.1)):
        values=[costs[m,k,policy]/costs[m,k,'unindp-compact'] for m,k in shapes];ratios+=values
        ax.scatter([i+offset for i in range(len(shapes))],values,c=color,marker=marker,s=27,label=policy)
    ax.set_xticks(range(len(shapes)),[f'{m}×{k}' for m,k in shapes],rotation=45,ha='right')
    ax.set_xlabel('Predeclared fresh GEMV shape (rows × reduction dimension)')
    ax.set_ylabel('Task cycles / strong baseline\n(lower is faster)');ax.set_ylim(0,max(ratios)*1.1)
    ax.legend(frameon=False,loc='lower left',bbox_to_anchor=(0,1.02),borderaxespad=0,ncol=3,fontsize=7)
    save_figure(fig,'fresh_mapping')
    fig,ax=plt.subplots(figsize=(183/25.4,83/25.4),layout='constrained')
    ax.axhline(0,color='0.45',linestyle='--',linewidth=.8)
    for policy,color,marker in (('noc-v03','#D55E00','x'),('noc-v04','#0072B2','o')):
        selected=[r for r in errors if r['split']=='fresh' and r['policy']==policy]
        ax.scatter([r['actual_cycles'] for r in selected],[100*r['relative_error'] for r in selected],
                   color=color,marker=marker,s=12,alpha=.55,label=policy)
    ax.set_xscale('log');ax.set_xlabel('Reference evaluator task cycles (log scale)')
    ax.set_ylabel('Signed task-cycle prediction error (%)')
    ax.legend(frameon=False,loc='lower left',bbox_to_anchor=(0,1.02),borderaxespad=0,ncol=2,fontsize=7)
    save_figure(fig,'fresh_prediction_error')
    evidence=dict(claim_anchor='bounded-prediction-repair',
        main_claim='The bounded surrogate repairs ranking and task-cycle prediction on predeclared fresh shapes; search overhead is reported separately.',
        material_passport=dict(summary='summary.json',records='records.json.gz',phases='phase_errors.csv'),
        mapping=dict(n_shapes=len(shapes),budget=1,baseline='unchanged UniNDP-adapted plus compact',
                     chart='individual paired shape ratios; lower is faster'),
        prediction=dict(n_candidates=len([r for r in errors if r['split']=='fresh' and r['policy']=='noc-v04']),
                        chart='signed error against Python evaluator, not RTL'),
        statistics='single deterministic run per candidate; controls may be equivalent; no confidence or significance claims',
        scope='same hardware and evaluator; raw numerical validation precedes export; prior 16 shapes are development only',
        exports=['fresh_mapping.svg','fresh_mapping.pdf','fresh_mapping.png',
                 'fresh_prediction_error.svg','fresh_prediction_error.pdf','fresh_prediction_error.png'])
    (figures/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',type=Path,required=True);parser.add_argument('--replay',type=Path,required=True)
    parser.add_argument('--inputs',type=Path,nargs='+',required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();export(args.summary,args.replay,args.inputs,args.output_dir)


if __name__=='__main__': main()
