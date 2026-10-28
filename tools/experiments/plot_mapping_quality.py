#!/usr/bin/env python3
"""Plot every holdout case at equal exact-evaluation budgets; no fitted data."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--baseline',default='unindp-adapted')
    parser.add_argument('--stem',default='mapping_quality')
    args=parser.parse_args()
    report=json.loads(args.summary.read_text())
    rows=[row for row in report['results'] if row['split']=='holdout']
    shapes=sorted({(row['m'],row['k']) for row in rows})
    if not shapes:
        raise ValueError('no holdout data')
    lookup={(row['m'],row['k'],row['policy'],row['budget']):row['cycles'] for row in rows}
    values={budget:[lookup[(m,k,args.baseline,budget)]/lookup[(m,k,'noc',budget)]
                    for m,k in shapes] for budget in (1,4)}
    if any(value<=0 for series in values.values() for value in series):
        raise ValueError('nonpositive cycles')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.labelsize':8,
                         'svg.fonttype':'none','pdf.fonttype':42,'ps.fonttype':42,
                         'axes.spines.top':False,'axes.spines.right':False})
    fig,ax=plt.subplots(figsize=(183/25.4,83/25.4),layout='constrained')
    ax.axhline(1,color='0.45',linestyle='--',linewidth=0.8,label='Equal task cycles')
    for budget,color,marker,offset in ((1,'#0072B2','o',-0.1),(4,'#D55E00','s',0.1)):
        ax.scatter([index+offset for index in range(len(shapes))],values[budget],
                   c=color,marker=marker,s=27,label=f'{budget} exact evaluation'+('s' if budget>1 else ''))
    ax.set_xticks(range(len(shapes)),[f'{m}×{k}' for m,k in shapes],rotation=45,ha='right')
    ax.set_xlabel('Held-out GEMV shape (output rows × reduction dimension)')
    label='UniNDP-adapted' if args.baseline=='unindp-adapted' else 'UniNDP-adapted + compact'
    ax.set_ylabel('Task cycle ratio\n(baseline / communication-aware)')
    ax.set_ylim(0,max(1.15,max(values[1])*1.12))
    ax.legend(title=label+' baseline',title_fontsize=7,frameon=False,
              loc='lower left',bbox_to_anchor=(0,1.02),borderaxespad=0,fontsize=7,ncol=3)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    for suffix in ('svg','pdf','png'):
        fig.savefig(args.output_dir/f'{args.stem}.{suffix}',dpi=240)
    # A machine-readable figure argument/trace, not a significance claim.
    packet=dict(claim_anchor='mapping-budget-sensitivity',
        main_claim='Communication-aware ranking changes the limited-budget choice; compare all wins and regressions, and observe whether extra exact evaluations close the gap.',
        material_passport=dict(path=str(args.summary),sha256=hashlib.sha256(args.summary.read_bytes()).hexdigest()),
        n_shapes=len(shapes),shapes=shapes,ratios=values,baseline=args.baseline,
        units='dimensionless paired task-cycle ratios; higher is faster for communication-aware',
        statistics='deterministic one-seed model runs, no uncertainty bars or significance tests',
        source_data='quality.csv and summary.json; each dot is one held-out shape',
        figure_size_mm=[183,83],palette_roles={'budget1':'blue circle','budget4':'orange square'},
        exports=[f'{args.stem}.{suffix}' for suffix in ('svg','pdf','png')],
        limitations='UniNDP search-method SRAM adaptation; not the author DRAM backend; finite declared catalogue only')
    (args.output_dir/'figure_evidence.json').write_text(json.dumps(packet,indent=2)+'\n')
    print(f'{len(shapes)} holdout shapes plotted with paired budgets and editable text')


if __name__=='__main__':
    main()
