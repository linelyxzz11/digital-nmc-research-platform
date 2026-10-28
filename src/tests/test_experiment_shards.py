"""Multi-PC transport: deterministic manifest, resume and strict merge checks."""
from pathlib import Path
import runpy

import pytest


@pytest.fixture
def api():
    return runpy.run_path(str(Path(__file__).resolve().parents[2]/'tools/experiments/scalable_experiments.py'))


def prepared(api,tmp_path):
    path=tmp_path/'manifest.json'
    data=api['prepare'](path,profile='smoke',shards=3,shapes=[(5,7)],grids=[(1,2),(2,1)],placements=('compact',))
    return path,data


def finished(api,tmp_path):
    path,data=prepared(api,tmp_path)
    dirs=[tmp_path/f'pc-{i}' for i in range(3)]
    for i,directory in enumerate(dirs): api['run_shard'](path,i,directory)
    return path,data,dirs


def test_manifest_is_deterministic_and_partitions_cover_jobs_once(api,tmp_path):
    path,a=prepared(api,tmp_path)
    b=api['prepare'](tmp_path/'other.json',profile='smoke',shards=3,shapes=[(5,7)],grids=[(1,2),(2,1)],placements=('compact',))
    assert a['sha256']==b['sha256']
    groups=[{j['job_id'] for j in a['jobs'] if j['shard']==i} for i in range(3)]
    assert len(set.union(*groups))==sum(map(len,groups))==4
    assert api['load_manifest'](path)['source_sha256']==api['source_hashes']()


def test_shards_resume_without_repeating_completed_work_and_merge_correctly(api,tmp_path):
    path,data=prepared(api,tmp_path);dirs=[tmp_path/f'pc-{i}' for i in range(3)]
    first=api['run_shard'](path,0,dirs[0],max_jobs=1)
    second=api['run_shard'](path,0,dirs[0])
    third=api['run_shard'](path,0,dirs[0])
    assert first['executed']==1 and second['resumed']==1 and second['executed']==1
    assert third['resumed']==2 and third['executed']==0
    for i in (1,2): api['run_shard'](path,i,dirs[i])
    result=api['merge'](path,dirs,tmp_path/'merged')
    assert result['complete'] and result['jobs']==result['ok']==4 and result['infeasible']==0
    assert (tmp_path/'merged/results.csv').exists()


def test_missing_and_duplicate_jobs_cannot_be_published_as_complete(api,tmp_path):
    path,data,dirs=finished(api,tmp_path)
    with pytest.raises(ValueError,match='incomplete'): api['merge'](path,dirs[:1],tmp_path/'missing')
    with pytest.raises(ValueError,match='duplicate'): api['merge'](path,[*dirs,dirs[0]],tmp_path/'duplicate')


def test_mixed_commits_are_rejected_even_with_matching_source_fingerprints(api,tmp_path):
    path,data,dirs=finished(api,tmp_path)
    result_path=next(dirs[1].glob('*.result.json'))
    payload=api['verified'](result_path,'result_sha256');payload.pop('result_sha256')
    payload['commit']='different-revision'
    api['save'](result_path,api['seal'](payload,'result_sha256'))
    with pytest.raises(ValueError,match='Git revisions'): api['merge'](path,dirs,tmp_path/'mixed')
    with pytest.raises(ValueError,match='resume would mix'): api['run_shard'](path,1,dirs[1])


def test_corrupted_result_is_not_silently_skipped_on_resume(api,tmp_path):
    path,data=prepared(api,tmp_path);directory=tmp_path/'pc'
    api['run_shard'](path,0,directory,max_jobs=1)
    result=next(directory.glob('*.result.json'))
    result.write_text(result.read_text().replace('"golden_ok": true','"golden_ok": false'))
    with pytest.raises(ValueError,match='integrity'): api['run_shard'](path,0,directory)


def test_changed_model_and_invalid_shard_assignments_fail_before_execution(api,tmp_path):
    path,data=prepared(api,tmp_path)
    body=dict(data);body.pop('sha256');body['source_sha256']={}
    api['save'](path,api['seal'](body))
    with pytest.raises(ValueError,match='source mismatch'): api['run_shard'](path,0,tmp_path/'bad')
    path,data=prepared(api,tmp_path)
    body=dict(data);body.pop('sha256');body['jobs'][0]['shard']=2
    api['save'](path,api['seal'](body))
    with pytest.raises(ValueError,match='shard assignment'): api['load_manifest'](path)


def test_infeasible_jobs_are_preserved_as_results_not_dropped(api,tmp_path):
    path=tmp_path/'manifest.json'
    data=api['prepare'](path,profile='smoke',shards=1,shapes=[(256,1024)],grids=[(1,2)],placements=('compact',))
    api['run_shard'](path,0,tmp_path/'pc')
    merged=api['merge'](path,[tmp_path/'pc'],tmp_path/'merged')
    assert merged['jobs']==merged['infeasible']==2 and merged['ok']==0 and merged['complete']


def test_resealed_but_false_plan_and_numeric_results_are_rejected(api,tmp_path):
    path,data,dirs=finished(api,tmp_path)
    result_path=next(dirs[0].glob('*.result.json'))
    result=api['verified'](result_path,'result_sha256');result.pop('result_sha256')
    result['metrics']['y'][0]+=1
    api['save'](result_path,api['seal'](result,'result_sha256'))
    with pytest.raises(ValueError,match='independent merge golden'): api['merge'](path,dirs,tmp_path/'bad-y')
    body=dict(data);body.pop('sha256');body['jobs'][0]['plan_sha256']='false-plan'
    api['save'](path,api['seal'](body))
    with pytest.raises(ValueError,match='plan disagrees'): api['load_manifest'](path)
