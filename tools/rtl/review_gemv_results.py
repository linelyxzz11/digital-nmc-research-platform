"""Audit compact published evidence; this does not execute RTL or replay raw Flits."""
import argparse,gzip,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.rtl.run_mesh_rtl import prepare,golden_gemv
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,default=ROOT/'data/results/rtl_gemv_20261031/evidence.json.gz');p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    rows=json.loads(gzip.decompress(a.evidence.read_bytes()));seen=set()
    for e in rows:
        c=e['case'];r=e['result'];assert c['name'] not in seen;seen.add(c['name'])
        plan,W,x,_=prepare(c,a.output_dir/c['name']);g=golden_gemv(W,x)
        assert plan.digest==r['plan_sha256'] and plan.tile_count==64
        assert e['golden_check']['golden']==e['golden_check']['actual']==g
        assert e['golden_check']['root_commits_exact'] and r['wire_packets_match'] and r['transaction_done']==1
        assert len(r['observations'])==64 and all(o['max_router']<=4 and o['max_ni']<=4 and o['max_rx']<=2 for o in r['observations'])
    assert len(seen)==10
    print('10 published RTL execution summaries agree with seeded workloads/Golden and plans. No new RTL run or raw packet replay was performed.')
if __name__=='__main__':main()
