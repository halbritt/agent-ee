# Pinned wideband reference

See `COVERAGE.md` and `docs/fome-wbo1.md` for checks and outstanding coverage.
`blocks/wbo1` is the rung-2 source; `blocks/wbo2` is the golden adaptation target.
Shared rights: `fixtures/fome-mc/LICENSE-NOTICE.md` (AGENTEE-105 supplies it).

Both captures came from the committed fome-mc object tree at
`df304f3321296f7e517e0d2e2aa52e1380d52993`. The dirty checkout HEAD was never
captured. Every source blob's SHA-256 is in its block manifest.

Reproduce these two channels through the existing capture API, using their
committed requests and generated supplements. `NEW_BLOCKS` is a new destination
parent. The source sheets come from pinned Git objects; the full inventory and
planning documents stay outside the tracked fixture. Requirement and contract
citations identify the external documents by repository, commit, path, blob ID
and SHA-256, with a section locator. No planning-document bytes are captured.

```python
from pathlib import Path
from agent_ee.reference_blocks import capture_block
from agent_ee.storage import read_json

templates = Path("fixtures/fome-mc/wbo1/blocks")
destination = Path("NEW_BLOCKS")
destination.mkdir()
for name in ("wbo1", "wbo2"):
    original = templates / name
    request = read_json(original / "block.json")
    supplements = {
        path: (original / path).read_bytes()
        for path in request["files"]
        if path not in request["source"]["paths"] and path != "block-contract.json"
    }
    capture_block(request, Path.home() / "git/fome-mc",
                  request["source"]["commit"], destination / name,
                  supplements=supplements)
```

Compare each published directory byte for byte with the committed template.
Run this twice inside `bwrap --unshare-net` to check offline reproducibility.
Validation checks external citations in the local pinned Git object store;
missing repositories or objects leave those citations unverified.
No source run is attached: `reference_blocks.validate` must exit 1 with
`status: unverified`. No owner evidence store is created.
