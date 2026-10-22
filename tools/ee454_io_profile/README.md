# AGENTEE-454 preparation I/O profile harness

Diagnostic tooling for the causal attribution of repeated datasheet reads in pod
production preparation. Nothing here is imported by `src/agent_ee`; all wrappers
are installed by the harness at runtime and every wrapped call still performs its
original read.

- `reconstruct.py` rebuilds the Rev-3 `complete-parts-manifest` capture root
  (327 parts, 316 named part records) from pinned fome-mc objects with `git show`,
  checking each file against the packet's `reconstruction.json` sha256.
- `profile_io.py` runs one qualification workload (`closure` =
  `parts_manifest.dependency_closure`, `qualify` = `parts_manifest.qualify_manifest`)
  in `plain`, `count` (bytes per call chain and per path, checked against
  `/proc/self/io` rchar) or `cprofile` mode.
- `count_calls.py` counts qualification passes and their callers inside existing
  unittest cases (in-process only; sandboxed steps run in other processes).
- `analyze.py` turns a `count` record into the attribution table and modelled,
  not measured, byte reductions for candidate repair shapes.

`evidence/` holds the records of the 2026-10-22 run on agent-ee-gate-c4. Third-party
capture files are never copied into this tree; only their paths and hashes appear.

```sh
python3 tools/ee454_io_profile/reconstruct.py --store ~/git/fome-mc \
  --commit 8c42f2ee3e7c5c77c51e21632a8900bfbef23726 --out /scratch/root --record /scratch/inputs.json
PYTHONPATH=src python3 tools/ee454_io_profile/profile_io.py /scratch/root \
  --workload closure --mode count --out /scratch/closure-count.json
python3 tools/ee454_io_profile/analyze.py /scratch/closure-count.json /scratch/root --out /scratch/attribution.json
```
