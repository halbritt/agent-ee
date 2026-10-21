# Replay the GPU experiments

The scripts are isolated research programs, not router backends. Input geometry
and graphs are deliberately narrower than native PCB legality. See the report's
scope before interpreting a successful run. They do not write routed boards.

From the agent-ee checkout, validate saved evidence and run CPU pattern fixtures:

```sh
python3 docs/research/2026-10-21-gpu-pcb/verify.py
uv run --with numpy==2.5.4 python docs/research/2026-10-21-gpu-pcb/experiments/route_patterns.py --self-test
```

On a CUDA host, copy `experiments/` to a scratch directory outside the checkout
(the original was `~/experiments/agent510-gpu-pcb`). Run there to keep generated
libraries and new observations separate from the committed evidence. g++, uv and
a compatible NVIDIA driver are prerequisites. Create the isolated environment:

```sh
uv venv --python 3.12.15 venv
uv pip install --python venv/bin/python numpy==2.5.4 'cupy-cuda12x[ctk]==14.2.0' cuda-toolkit==12.9.2.0
g++ -O3 -std=c++17 -shared -fPIC placement_score.cpp -o placement_score.so
taskset -c 2 venv/bin/python placement_score.py --input placement-input.json --output placement-result.json
taskset -c 2 venv/bin/python placement_reference.py placement-input.json > placement-python-result.json
taskset -c 2 venv/bin/python placement_lazy.py placement-input.json > placement-lazy-result.json
taskset -c 2 venv/bin/python route_patterns.py native-input.npz --output patterns-native-result.json
taskset -c 2 venv/bin/python field_prefix.py native-input.npz --output prefix-result.json
```

The placement script includes the input upload and score download in
`gpu_transfer_wall`; it separately reports static upload/packing/initialization.
The pattern script compiles its C++ CPU library in a temporary directory and runs
synthetic CUDA correctness checks before timing. Pattern generation/host selection
are outside kernel timings and explicitly reported. GPU initialization/kernel
errors fail execution; there is no disguised CPU benchmark fallback. `--self-test`
is an explicitly CPU-only path. Do not run with Python `-O`, which disables the
experiment assertions. Prefix construction compares every output element.

All measurements used one pinned logical CPU and seven repetitions after two
warmups. GPU clocks were not locked and Windows background activity was not
eliminated. A new machine/session is a new measurement; do not overwrite historical
results or treat a changed median as a correctness failure.

For the original peecee-wsl run, work was wrapped in the fleet lease runner:

```sh
/home/halbritt/git/gpu-fleet/bin/gpu-fleet-run --model marker --job pcb-gpu-replay --timeout 180 -- \
  ssh peecee-wsl 'set -eu; cd ~/experiments/agent510-gpu-pcb; taskset -c 2 venv/bin/python route_patterns.py native-input.npz --output patterns-replay.json'
```

Here `marker` selects the already configured peecee GPU-only lease slot; no Marker
conversion or external model service is used. Check current slot and physical-GPU
occupancy first. GPU-fleet records leases by slot; this is not proof of exclusive
physical-GPU ownership. Job names must identify the new run. On this WSL host
`nvidia-smi` is `/usr/lib/wsl/lib/nvidia-smi`. The owner authorized unloading the
resident Ollama model during the original work; no global service was disabled.

## Rebuild inputs, optional

Replaying the committed input needs no KiCad installation. Regenerating placement
data requires the retained iteration-7 `evidence/placement-probe-pads.json` and
`evidence/placement-plan.json` listed in `placement-input.json`. The exporter has
`--agent-ee-repo`, `--kicad-route-repo`, `--source-root`, `--output-dir` options;
run `--help` for defaults. The board dimensions and U23 target are this experiment's
fixture, not a general board exporter.

Native-map generation requires an installed owned `grid_router` extension and
the kicad-route numeric environment. The original interpreter was:

```sh
/home/halbritt/git/kicad-route/.router-generations/84b30dc286f44df6b465ae8d40aa1363/venv/bin/python \
  docs/research/2026-10-21-gpu-pcb/experiments/export_inputs.py routing-native \
  --source-root docs/research/2026-10-21-gpu-pcb/experiments \
  --output-dir /tmp/pcb-gpu-replay
```

This writes `agent510-gpu-route-native-input.npz` and its JSON sidecar. Rename both
to a common stem if desired. The generator verifies the loaded binary against an
enclosing generation manifest when present, records observed Python revisions,
dirty state and source hashes, and distinguishes binary build claims. A source
change is a new experiment. The saved original metadata predates these additional
exporter fields; its compiled provenance is qualified in `results/run-manifest.json`.

No regenerated NPZ byte hash is expected to match merely because its arrays match:
ZIP metadata can differ. Compare all arrays and semantic metadata, then hash the
new immutable artifact. The pad-only fallback is retained for audit, not needed
to reproduce the native-map conclusions.
