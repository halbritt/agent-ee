# AGENTEE-454 repair measurement (R1 + R3 + R4)

Records of the phase-2 before/after run with the phase-1 harness (`profile_io.py`
from b74f3c40) on the reconstructed Rev-3 complete parts manifest (fome-mc
8c42f2ee3e7c5c77c51e21632a8900bfbef23726; `repair-inputs.json` is that rebuild,
identity 23055f79179aab9b13c9b2f409b18b196629b0d173ca7ccb8e817e22c36e7e91, equal to
phase-1 `inputs.json`). Host peecee-wsl, Python 3.14.4, native, `nice -n 10`.

- `before-*`: `src/` of c7fc1824 (git archive). `after-*`: `src/` of the AGENTEE-454
  repair commit (byte-identical copy, checked with `diff -r`).
- This WSL kernel has no `/proc/self/io`, so `rchar_bytes` is 0 in every record; the
  harness was patched in scratch to tolerate that. Logical bytes are the harness's
  `attributed_bytes` (wrapped `Path.read_bytes`/`read_text` and `storage._hash_stream`),
  which phase-1 measured at 99.998% of rchar on c4.
- `compare-*`: `storage.identity` of the `dependency_closure` map and of the full
  `qualify_manifest` result, before and after; both equal.

| Workload | Before bytes | After bytes | Before wall | After wall |
|---|---:|---:|---:|---:|
| `dependency_closure` (count) | 31,248,160,693 | 128,074,107 | 21.3 s | 3.0 s |
| `dependency_closure` (plain) | n/a | n/a | 19.8 s | 2.6 s |
| `qualify_manifest` (count) | 31,376,303,522 | 128,117,988 | 25.0 s | 7.9 s |

Distinct inputs read: 406 paths / 128,072,143 bytes (closure), 407 (qualify). Peak RSS of
a closure pass rises from 54 MB to 160 MB (the call holds its verified bytes);
`qualify_manifest` already held every closure member and stays at about 168 MB. Per
pass on this subset only; no production-run gain is measured or claimed.
