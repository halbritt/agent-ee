# Stage-one design intent

`PYTHONPATH=src python3 -m agent_ee.design_intent record.json --out NEW_DIRECTORY`
imports one UTF-8 JSON object offline with Python's standard library. Exit 0 means
bounded lint passed, 1 means findings leave intent unknown, and 2 means malformed
input or unavailable I/O. Existing directories are refused. An I/O failure can
leave a partial staging directory; discard it and use a new destination.

Required fields are `id` (nonempty string or nonnegative integer), `task`,
`input_nodes`, `output_nodes`, `input_voltage`, `output_voltage`, `components`,
and `sub_module_name`. Optional benchmark metadata fields are `level`, `type`,
`n_gt_components` and `gt_component_types`. Unknown fields and duplicate JSON
keys are refused. Nodes are comma-separated names or name lists. Components
are positive integer name/count objects, lists of bare part names (repetition
counts), or comma-and-whitespace separated names with optional `Nx ` counts.
A comma without following whitespace stays inside a name. No parts are selected.

Artifacts are deterministic and preserve the exact original JSON bytes in
`source-record.json` and exact task prose in `requirements.md`.
`requirements.json` adds one structured original requirement with an ID formed
from `R-intent-` plus the SHA-256 of the string form of the source record ID.
The ID stays stable for revisions of that record; merging records with identical
IDs requires the owner to supply distinct source identities first. This file
feeds dialog capture directly without changing the original prose. `intent.json`
records the source SHA-256, unchanged module name, numeric operating targets,
findings and explicit waivers. `component-expectation.json` records the required
multiset. `hardware-interface.stub.json` lists the required nets under a distinct
`agent-ee.interface-stub/1` schema with `complete: false`. It is rejected by the
existing hardware-interface contract validator. Native identities, terminals,
invariants and executable coverage must be supplied by the owner before scope/3
acceptance. These artifacts do not generate or change scopes or captured baselines.

Lint checks duplicate names within each interface, generic placeholder names,
explicit lexical part counts against exact component names, optional component
metadata counts, and nonzero signed numeric voltage syntax. Multiple voltages
remain unassigned targets: the importer does not guess rail-to-net mappings.
Zero, prose annotations, currents, adjustable rails and unparseable voltages yield
unknown. A voltage may be a JSON string or a JSON number with the same
meaning: a fraction or exponent number is never decoded as a binary float. Its
exact source token is the target text, so `1.0000000000000001`, `1e-400` and
`9007199254740993.0` keep their declared values. `raw` writes that same token
unchanged, and `volts` lists exact decimal text, removing only trailing fraction
zeros. Exponents beyond Python decimal limits yield unknown in either encoding.
Integer tokens over Python's 4300-digit conversion limit refuse the whole record. Prose parsing is deliberately limited to explicit counts followed by
exact declared part names; synonyms and semantic contradictions need dialog review.
A lint pass is not proof that all prose is consistent or that a circuit works.
`--out-justification TEXT` records an owner waiver for the exact `OUT` net only;
there is no global or ID-only implicit allowlist.

## Private real-corpus qualification

The independently implemented helper downloads no third-party code and commits no
third-party records. On an authorized outer host, run:

```sh
PYTHONPATH=src python3 scripts/qualify_intent_corpus.py /private/new-corpus --fetch
```

Then run the same command without `--fetch` in a networkless environment. It
requires the pinned source and MIT license hashes from Hugging Face revision
`baf1235ae6270419b8444ffe618632f17621a3f9`, imports 62 + 165 records, checks
id37's exact CAN nets/part/no findings, id196's transistor-count mismatch, all
20 known OUT-placeholder cases, deterministic outputs and component-encoding
agreement. Id15's actual gate-driver OUT pin receives a specific recorded
justification. Other OUT appearances remain conservative warnings, reported
separately. Exact input hashes bind importer bytes; JSON-array corpus records
are deterministically reserialized for the individual importer input and the
complete downloaded corpus retains its separate hash.

The source [dataset page](https://huggingface.co/datasets/Hzou9/PCBSchemaGen-Benchmarks)
declares MIT and has a pinned license. This is source/license qualification for
private importer testing, not an owner approval to republish data or proof of
upstream rights for every referenced design. Preserve private source files and
qualification output outside this public repository. Owned unit tests are distinct
from the real-corpus run and cannot substitute for it.
