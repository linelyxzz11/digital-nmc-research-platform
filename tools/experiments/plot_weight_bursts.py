#!/usr/bin/env python3
"""Plot measured burst-size sensitivity and encoded traffic from sweep CSV."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-dir',type=Path,required=True)
    a=p.parse_args(); source=a.input_dir/'comparison.csv'
    with source.open(newline='') as f:
        rows=list(csv.DictReader(f))
    selected=[r for r in rows if float(r['raw_link_bytes_per_cycle'])==16]
    shapes=list(dict.fromkeys((int(r['m']),int(r['k'])) for r in selected))
    out=a.input_dir/'figures';out.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'svg.fonttype':'none',
                         'svg.hashsalt':'nmc-weight-bursts','axes.spines.top':False,
                         'axes.spines.right':False,'savefig.dpi':180})
    def save(fig,name):
        fig.savefig(out/(name+'.png'))
        fig.savefig(out/(name+'.svg'),metadata={'Date':None})
        plt.close(fig)
        svg=out/(name+'.svg')
        svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    fig,ax=plt.subplots(figsize=(7.2,4.4),layout='constrained')
    for shape,color,marker in zip(shapes,['#4477AA','#228833','#AA3377'],['o','s','^']):
        cases=sorted((r for r in selected if (int(r['m']),int(r['k']))==shape),
                     key=lambda r:int(r['weight_packet_bytes']))
        ax.plot([int(r['weight_packet_bytes']) for r in cases],
                [float(r['speedup']) for r in cases],color=color,marker=marker,
                label=f'{shape[0]} × {shape[1]}',linewidth=1.8)
    ax.axhline(1,color='#555555',linestyle='--',linewidth=1,label='Equal task cycles')
    ax.set_xticks([16,32,64,128]);ax.set_ylim(bottom=0)
    ax.set_xlabel('Weight payload per packet (B)')
    ax.set_ylabel('Central task cycles / NMC task cycles')
    ax.set_title('Fixed 128 B prefetch per cluster; one central NI')
    ax.text(.98,.02,'Raw link: 16 B/core cycle; Python model',
            transform=ax.transAxes,ha='right',fontsize=9)
    ax.grid(alpha=.18);ax.legend(frameon=False)
    save(fig,'speedup_vs_burst')
    traffic=sorted((r for r in selected if (int(r['m']),int(r['k']))==(256,512)),
                   key=lambda r:int(r['weight_packet_bytes']))
    if not traffic:
        raise ValueError('Traffic panel requires 256×512 at 16 B/core cycle')
    fig,ax=plt.subplots(figsize=(7.2,4.4),layout='constrained')
    x=np.arange(len(traffic))
    payload=np.array([int(r['weight_bytes'])/1024 for r in traffic])
    headers=np.array([(int(r['weight_flits'])*16-int(r['weight_bytes']))/1024 for r in traffic])
    credit=np.array([int(r['credit_flits'])*16/1024 for r in traffic])
    ax.bar(x,payload,.6,color='#228833',label='Weight payload')
    ax.bar(x,headers,.6,bottom=payload,color='#4477AA',hatch='//',edgecolor='white',label='Weight headers / padding')
    bars=ax.bar(x,credit,.6,bottom=payload+headers,color='#CCBB44',hatch='..',edgecolor='white',label='Credit flits')
    ax.bar_label(bars,labels=[f'{v:.0f}' for v in payload+headers+credit],padding=3,fontsize=9)
    ax.set_xticks(x,[r['weight_packet_bytes'] for r in traffic])
    ax.set_ylim(0,max(payload+headers+credit)*1.35)
    ax.set_xlabel('Weight payload per packet (B)')
    ax.set_ylabel('Injected weight + credit traffic (KiB)')
    ax.set_title('256 × 512: fixed weight bytes, reduced packet overhead')
    ax.legend(frameon=False,fontsize=9);ax.set_axisbelow(True);ax.grid(axis='y',alpha=.18)
    save(fig,'encoded_traffic')
    (out/'provenance.json').write_text(json.dumps(dict(
        source='../comparison.csv',source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Deterministic Python model; no RTL or energy claim; fixed seed, no error bars.'),indent=2)+'\n')


if __name__=='__main__':main()
