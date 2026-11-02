"""Reconstruct and audit the published Python records without rerunning simulation.

This restores recorded rankings, not new predictions or a new blind campaign.
Use summarize_validation_campaign.py on the restored directory to redraw figures.
"""
import argparse,gzip,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.experiments.scalable_experiments import verified,save,seal,digest
from tools.experiments.summarize_validation_campaign import audit
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results',type=Path,default=ROOT/'data/results/validation_20261031')
    p.add_argument('--protocol',type=Path,default=ROOT/'ini/experiments/validation_20261031/protocol_final.json')
    p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    protocol=verified(a.protocol);records=json.loads(gzip.decompress((a.results/'records.json.gz').read_bytes()))
    a.output_dir.mkdir(parents=True,exist_ok=True)
    for r in records:
        body=dict(r);expected=body.pop('sha256');assert digest(body)==expected
        assert r['protocol_sha256']==protocol['sha256']
        save(a.output_dir/(r['run_id']+'.json'),r)
        save(a.output_dir/(r['run_id']+'.ranking.json'),seal(dict(protocol_sha256=protocol['sha256'],policies=r['policies'])))
    for name in ['walltime.json','unsupported.json']:
        r=verified(a.results/name);save(a.output_dir/name,r)
    assert len(audit(a.output_dir,protocol))==23
    print('Audited 23 recorded shape/hardware cases; no new simulation or ranking was executed.')
if __name__=='__main__':main()
