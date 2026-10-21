"""Immutable row/column occupancy and cost-prefix construction, int64 exact.
No route search or copper legality beyond the supplied scalar grid.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path
import numpy as np
import cupy as cp


def build(xp, blocked, costs):
    return (xp.cumsum(blocked, axis=0, dtype=xp.int64),
            xp.cumsum(blocked, axis=1, dtype=xp.int64),
            xp.cumsum(costs, axis=0, dtype=xp.int64),
            xp.cumsum(costs, axis=1, dtype=xp.int64))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('input',type=Path); ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    with np.load(args.input,allow_pickle=False) as z:
        b=np.ascontiguousarray(z['blocked']); c=np.ascontiguousarray(z['costs'])
    assert b.dtype==np.uint8 and c.dtype==np.int32 and b.shape==c.shape
    db,dc=cp.asarray(b),cp.asarray(c)
    cp.cuda.Stream.null.synchronize()
    expected=build(np,b,c)
    start=time.perf_counter(); got=build(cp,db,dc); cp.cuda.Stream.null.synchronize()
    cold_ms=(time.perf_counter()-start)*1000
    for g,e in zip(got,expected):np.testing.assert_array_equal(g.get(),e)
    rows=[]
    for i in range(9):
        sample={}
        for arm in (['cpu','gpu'] if i%2==0 else ['gpu','cpu']):
            t=time.perf_counter()
            if arm=='cpu':
                out=build(np,b,c); sample['cpu_ms']=(time.perf_counter()-t)*1000
                for o,e in zip(out,expected):np.testing.assert_array_equal(o,e)
            else:
                db.set(b);dc.set(c);cp.cuda.Stream.null.synchronize(); t1=time.perf_counter()
                out=build(cp,db,dc);cp.cuda.Stream.null.synchronize();t2=time.perf_counter()
                host=[o.get() for o in out];t3=time.perf_counter()
                sample.update(h2d_ms=(t1-t)*1000,resident_ms=(t2-t1)*1000,d2h_ms=(t3-t2)*1000,total_ms=(t3-t)*1000)
                for o,e in zip(host,expected):np.testing.assert_array_equal(o,e)
        if i>=2:rows.append(sample)
    result={'schema':'agent510.grid-prefix/1','input_sha256':hashlib.sha256(args.input.read_bytes()).hexdigest(),
            'shape':list(b.shape),'gpu':cp.cuda.runtime.getDeviceProperties(0)['name'].decode(),
            'output_bytes':sum(a.nbytes for a in expected),'cold_first_build_ms':cold_ms,
            'all_elements_equal':True,'samples':rows,'medians_ms':{k:float(np.median([r[k] for r in rows])) for k in rows[0]},
            'limitations':'Four full-grid prefix arrays rebuilt. Existing engine uses sparse incremental cache updates; no claim this replaces them faster. NumPy compiled cumsum is CPU baseline. Timings include output allocation from warm allocators.'}
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result['medians_ms']))

if __name__=='__main__':main()
