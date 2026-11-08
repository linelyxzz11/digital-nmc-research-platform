#!/usr/bin/env python3
"""Plot measured independent-entry scaling and bounded internal-fabric control."""
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
    a=p.parse_args();source=a.input_dir/'comparison.csv'
    with source.open(newline='') as f:rows=list(csv.DictReader(f))
    def get(m,k,name):
        selected=[r for r in rows if (int(r['m']),int(r['k']),r['model'],float(r['raw_link_bytes_per_cycle']))==(m,k,name,16)]
        if len(selected)!=1:raise ValueError(f'Expected one record: {m},{k},{name}')
        return selected[0]
    out=a.input_dir/'figures';out.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'svg.fonttype':'none',
        'svg.hashsalt':'nmc-central-entries','axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':180})
    def save(fig,name):
        fig.savefig(out/(name+'.png'))
        fig.savefig(out/(name+'.svg'),metadata={'Date':None});plt.close(fig)
        svg=out/(name+'.svg');svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    fig,ax=plt.subplots(figsize=(7.2,4.4),layout='constrained')
    for shape,color,marker in zip([(128,128),(128,512),(256,512)],['#4477AA','#228833','#AA3377'],['o','s','^']):
        ratios=[float(get(*shape,f'entry{n}_fabric64')['central_over_nmc']) for n in (1,2,4)]
        ax.plot([1,2,4],ratios,color=color,marker=marker,linewidth=1.8,label=f'{shape[0]} × {shape[1]}')
    ax.axhline(1,color='#555555',linestyle='--',linewidth=1,label='Equal task cycles')
    ax.set_xticks([1,2,4]);ax.set_ylim(bottom=0)
    ax.set_xlabel('Independent NoC gateway count')
    ax.set_ylabel('Central task cycles / NMC task cycles')
    ax.set_title('Same 128 MACs; fixed 64 B/cycle internal fabric')
    ax.text(.98,.02,'128 B packets / prefetch; raw NoC link 16 B/cycle',transform=ax.transAxes,ha='right',fontsize=9)
    ax.legend(frameon=False);ax.grid(alpha=.18)
    save(fig,'entry_scaling')
    fig,ax=plt.subplots(figsize=(7.2,4.4),layout='constrained')
    x=np.arange(2)
    for dx,width,color,hatch in [(-.18,64,'#4477AA','//'),(.18,16,'#CCBB44','..')]:
        values=[int(get(256,512,f'entry{n}_fabric{width}')['total_cycles']) for n in (2,4)]
        bars=ax.bar(x+dx,values,.35,label=f'Internal fabric {width} B/cycle',color=color,hatch=hatch,edgecolor='white')
        ax.bar_label(bars,padding=3,fontsize=9)
    nmc=int(get(256,512,'nmc')['total_cycles']);one=int(get(256,512,'entry1_fabric64')['total_cycles'])
    ax.axhline(one,color='#777777',linestyle=':',label=f'One gateway: {one:,} cycles')
    ax.axhline(nmc,color='#228833',linestyle='--',label=f'NMC: {nmc:,} cycles')
    ax.set_ylim(0,one*1.4)
    ax.set_xticks(x,['2 gateways','4 gateways'])
    ax.set_ylabel('Task cycles (lower is better)')
    ax.set_title('256 × 512: more gateways need sufficient internal bandwidth')
    ax.legend(frameon=False,fontsize=9,loc='upper right');ax.set_axisbelow(True);ax.grid(axis='y',alpha=.18)
    save(fig,'fabric_bottleneck')
    (out/'provenance.json').write_text(json.dumps(dict(source='../comparison.csv',
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Deterministic Python model; no RTL/PPA/energy claim.'),indent=2)+'\n')


if __name__=='__main__':main()
