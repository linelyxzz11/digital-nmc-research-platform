#!/usr/bin/env python3
"""Reproduce author UniNDP metrics; native hardware results, not a Mesh port."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
FIELDS=('name','operator','optimal_latency','optimal_commands','optimal_pu_dram_access',
        'optimal_host_dram_access','optimal_row_changes','baseline_latency',
        'baseline_commands','baseline_pu_dram_access','baseline_host_dram_access','baseline_row_changes')


def run(author_root,output,dependency_dir=None):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    clone=Path(tempfile.mkdtemp(prefix='unindp-native-'))/'repo'
    subprocess.run(['git','clone','--no-hardlinks','--single-branch',str(Path(author_root).resolve()),str(clone)],check=True,capture_output=True)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    if dependency_dir: env['PYTHONPATH']=str(Path(dependency_dir).resolve())
    version_code='import importlib.metadata as m,json;print(json.dumps({n:m.version(n) for n in ("numpy","PyYAML","tqdm","xlrd","xlsxwriter")}))'
    versions=json.loads(subprocess.check_output([sys.executable,'-B','-c',version_code],env=env,text=True))
    records=[]
    for m,k in ((4096,4096),(11008,4096),(4096,11008)):
        name=f'mvm_{m}x{k}'
        args=['compile.py','-A','aim','-W','mm','-S','1',str(k),str(m),'1',
              '-Q','-K','30','-WS','comparison','-O',name,'-N',name]
        started=time.perf_counter()
        process=subprocess.run([sys.executable,'-B',*args],cwd=clone,env=env,
                               text=True,capture_output=True,timeout=120)
        elapsed=time.perf_counter()-started
        (output/f'{name}.log').write_text(process.stdout+process.stderr)
        if process.returncode: raise RuntimeError((process.stdout+process.stderr)[-3000:])
        text=(clone/'comparison'/name/'csv'/f'_{name}.csv').read_text()
        parsed=list(csv.reader(io.StringIO(text)))
        assert len(parsed)==1 and len(parsed[0])==len(FIELDS)
        row={field:value if i<2 else int(value) for i,(field,value) in enumerate(zip(FIELDS,parsed[0]))}
        assert row['name']==name and row['optimal_latency']>0 and row['baseline_latency']>0
        row.update(m=m,k=k,speedup=row['baseline_latency']/row['optimal_latency'],
                   compiler_wall_seconds=elapsed,command=args)
        records.append(row)
        print(name,f"speedup={row['speedup']:.4f}",flush=True)
    payload=dict(schema_version=1,
        scope='Native author AiM configuration, 16-bit data width; no same-hardware comparison or GEMV arithmetic validation is claimed',
        author_repository='https://github.com/godfather991/UniNDP',
        author_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=clone,text=True).strip(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        author_config_sha256=hashlib.sha256((clone/'config/gddr6-aim.yaml').read_bytes()).hexdigest(),
        python=sys.version,platform=platform.platform(),dependencies=versions,
        latency_unit='author simulator native units; compare ratios within the identical author configuration',
        results=records)
    (output/'results.json').write_text(json.dumps(payload,indent=2)+'\n')
    with (output/'results.csv').open('w',newline='') as target:
        writer=csv.DictWriter(target,fieldnames=[f for f in records[0] if f!='command'])
        writer.writeheader();writer.writerows({k:v for k,v in r.items() if k!='command'} for r in records)
    return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--author-root',type=Path,default=ROOT/'lib/UniNDP')
    parser.add_argument('--dependency-dir',type=Path)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'dump/unindp_native')
    args=parser.parse_args()
    run(args.author_root,args.output_dir,args.dependency_dir)


if __name__=='__main__': main()
