#!/usr/bin/python3
"""Summarize measured comparison execution; engineering grades remain an explicit assessment."""
import argparse
import hashlib
import json
import statistics
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def summarize(root, grades):
    expectations=read(root/'expectations.json')
    expected={c['case']:c for c in expectations['cases']}
    rows=[]
    for harness in ('claude','codex','agy'):
        for case in expected.values():
            for trial in range(1,3 if case['split']=='development' else 2):
                run=root/'runs'/harness/f"{case['case']}-{trial}"
                for role in ('circuit','physical'):
                    key=f"{harness}/{case['case']}-{trial}/{role}"
                    record=run/'review-executions'/f'{role}.json'
                    execution=read(record) if record.exists() else {'execution':'not_run'}
                    review=run/'reviews'/f'{role}.json'
                    row={'key':key,'expected':case['expected'],'split':case['split'],
                        'execution':execution['execution'],'duration_s':execution.get('duration_s'),
                        'usage':execution.get('usage',execution.get('model_usage')),'grade':'ungraded'}
                    if review.exists():
                        raw=review.read_bytes();row['review_sha256']=hashlib.sha256(raw).hexdigest()
                        row['findings']=[{k:f[k] for k in ('id','assessment','severity','title','evidence')} for f in json.loads(raw)['findings']]
                    if key in grades:
                        grade=grades[key]
                        if grade.get('review_sha256') != row.get('review_sha256'):
                            raise ValueError(f'stale manual grade: {key}')
                        row.update(grade=grade['grade'],grade_reason=grade['reason'])
                    rows.append(row)
    summaries={}
    for harness in ('claude','codex','agy'):
        subset=[r for r in rows if r['key'].startswith(harness+'/')]
        times=[r['duration_s'] for r in subset if r['duration_s'] is not None]
        summaries[harness]={'planned_judgments':len(subset),
            'executions':{s:sum(r['execution']==s for r in subset) for s in sorted({r['execution'] for r in subset})},
            'grades':{g:sum(r['grade']==g for r in subset) for g in sorted({r['grade'] for r in subset})},
            'median_duration_s':statistics.median(times) if times else None,
            'sum_duration_s':sum(times)}
    return {'schema':'agent-ee.comparison-summary/1','expectations_sha256':hashlib.sha256((root/'expectations.json').read_bytes()).hexdigest(),
        'interpretation':'Execution counts and explicit manually checked grades; no automatic engineering oracle or model ranking.',
        'models':summaries,'judgments':rows}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('comparison');p.add_argument('--grades');p.add_argument('--out',required=True);a=p.parse_args()
    grades=read(Path(a.grades)) if a.grades else {}
    Path(a.out).write_text(json.dumps(summarize(Path(a.comparison),grades),indent=2)+'\n')
