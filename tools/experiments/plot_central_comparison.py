#!/usr/bin/env python3
"""Plot measured comparison.csv; no simulated values are generated here."""
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    args = parser.parse_args()
    source = args.input_dir / 'comparison.csv'
    with source.open(newline='') as f:
        rows = list(csv.DictReader(f))
    shapes = list(dict.fromkeys((int(r['m']), int(r['k'])) for r in rows))
    def select(shape, node, bandwidth):
        matches = [r for r in rows if (int(r['m']), int(r['k'])) == shape
                   and int(r['central_tile']) == node
                   and float(r['raw_link_bytes_per_cycle']) == bandwidth]
        if len(matches) != 1:
            raise ValueError(f'Expected one case for {shape}, {node}, {bandwidth}')
        return matches[0]
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'svg.fonttype': 'none', 'axes.spines.top': False,
                         'axes.spines.right': False, 'savefig.dpi': 180})
    out = args.input_dir / 'figures'
    out.mkdir(exist_ok=True)
    x = np.arange(len(shapes))
    fig, ax = plt.subplots(figsize=(7.2, 4.4), layout='constrained')
    for offset, node, field, label, color, hatch in [
        (-1, 0, 'central_cycles', 'Central: corner (node 0)', '#4477AA', '//'),
        (0, 36, 'central_cycles', 'Central: interior (node 36)', '#66AADD', '..'),
        (1, 36, 'nmc_cycles', 'NMC: local weight reads', '#228833', '')]:
        values = [int(select(s, node, 16)[field]) for s in shapes]
        bars = ax.bar(x + offset * .25, values, .24, label=label,
                      color=color, hatch=hatch, edgecolor='white')
        ax.bar_label(bars, fmt='%d', fontsize=8, padding=3)
    ax.set_xticks(x, [f'{m} × {k}' for m, k in shapes])
    ax.set_xlabel('GEMV shape (M × K)')
    ax.set_ylabel('Task cycles (lower is better)')
    ax.set_ylim(0, max(int(r['central_cycles']) for r in rows
                      if float(r['raw_link_bytes_per_cycle']) == 16) * 1.28)
    ax.legend(frameon=False, loc='upper left', fontsize=9)
    ax.set_title('Matched 128 MACs; 16 B/cycle raw directed-link bandwidth')
    ax.set_axisbelow(True); ax.grid(axis='y', alpha=.18)
    for ext in ('svg', 'png'):
        fig.savefig(out / f'task_cycles.{ext}')
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(7.2, 4.4), layout='constrained')
    for shape, color, marker in zip(shapes, ['#4477AA', '#228833', '#AA3377'], ['o', 's', '^']):
        bandwidths = [4, 8, 16]
        values = [float(select(shape, 36, b)['speedup']) for b in bandwidths]
        ax.plot(bandwidths, values, color=color, marker=marker,
                label=f'{shape[0]} × {shape[1]}', linewidth=1.8)
    ax.axhline(1, color='#555555', linestyle='--', linewidth=1, label='Equal cycles')
    ax.set_xticks([4, 8, 16]); ax.set_ylim(bottom=0)
    ax.set_xlabel('Raw directed-link bandwidth (B/core cycle)')
    ax.set_ylabel('Central cycles / NMC cycles')
    ax.set_title('Single central NI at node 36; resident weights, ROW mapping')
    ax.legend(frameon=False); ax.grid(alpha=.18)
    for ext in ('svg', 'png'):
        fig.savefig(out / f'bandwidth_sensitivity.{ext}')
    plt.close(fig)
    for svg in out.glob('*.svg'):
        svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n')
    (out / 'provenance.json').write_text(json.dumps({
        'source': '../comparison.csv',
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope': 'Deterministic Python architecture simulation; not RTL or PPA; no error bars.'
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()
