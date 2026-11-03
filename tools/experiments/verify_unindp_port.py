#!/usr/bin/env python3
"""Check the flat partition adapter against actual, pinned UniNDP methods.

Only the fixed commit's Partition and Mapping methods are executed. A hardware
shim replaces imports of numpy/pandas/yaml; no DRAM predictor is substituted
and no author source is copied into this repository.
"""
import argparse
import ast
import contextlib
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from compiler.unindp_adapter import AUTHOR_COMMIT, AUTHOR_URL, partitions


def author_functions(root):
    class Hardware:
        def __init__(self, require_power_of_2):
            self.channel_num = self.rank_num = self.device_num = 1
            self.de_pu_num_list = [16,32]
            self.limit_div_to_power_of_two = require_power_of_2
        def powerof2(self, value):
            return not self.limit_div_to_power_of_two or value & (value-1) == 0
    levels = SimpleNamespace(DE=1,RA=2)
    namespace = dict(HW_info=Hardware,SimConfig=SimpleNamespace(pu_level=1),
                     LEVEL=levels,mul=math.prod)
    hashes = {}
    for path, name, methods in (
        ('midend/partition.py','Partition',{'__init__','get_partition_space_mm','choose_from_partition_space_mm'}),
        ('midend/mapping.py','Mapping',{'__init__','assign_hw'})):
        source = subprocess.check_output(['git','show',f'{AUTHOR_COMMIT}:{path}'],cwd=root)
        hashes[path] = hashlib.sha256(source).hexdigest()
        tree = ast.parse(source)
        original = next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name == name)
        original.body = [node for node in original.body if isinstance(node,ast.FunctionDef) and node.name in methods]
        module = ast.Module(body=[original],type_ignores=[])
        exec(compile(module,f'UniNDP@{AUTHOR_COMMIT}/{path}','exec'),namespace)
    return namespace, hashes


def verify(author_root, shapes):
    namespace, hashes = author_functions(author_root)
    output = []
    for m,k in shapes:
        original = namespace['Partition'](True)
        with contextlib.redirect_stdout(io.StringIO()):
            enumerated = original.get_partition_space_mm([1,k,m,1])
        pruned = original.choose_from_partition_space_mm(enumerated)
        expected = set()
        for _,budget,(_,_,_,pu) in pruned:
            if pu[0]!=1 or pu[3]!=1 or pu[1]>k or pu[2]>m:
                continue  # GridPlan forbids empty shards.
            hw = ((1,1,1,1),)*3+(pu,)
            ids = tuple(namespace['Mapping'](True).assign_hw(hw)[3])
            expected.add((budget,(pu[2],pu[1]),pu,ids))
        adapted = {(p.budget,p.grid,p.author_pu_partition,p.author_pu_ids) for p in partitions(m,k)}
        if adapted != expected:
            raise AssertionError(f'adapter disagrees with author: {m}x{k}: {adapted ^ expected}')
        output.append(dict(m=m,k=k,matched=True,decisions=len(expected),
                           grids=sorted({grid for _,grid,_,_ in expected})))
    return dict(author_url=AUTHOR_URL,author_commit=AUTHOR_COMMIT,source_sha256=hashes,
                scope='power-of-two flat-PU partition/full-utilization pruning/logical assignment; SRAM layout/predictor/backend are adaptations',
                cases=output,all_matched=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--author-root',type=Path,default=ROOT/'lib/UniNDP')
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    shapes = [(32,128),(128,32),(64,256),(256,64),(31,127),(17,257),(1,4),(5,3)]
    report = verify(args.author_root,shapes)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(f'{len(shapes)} shapes: partition and logical assignment agree with pinned UniNDP')


if __name__ == '__main__':
    main()
