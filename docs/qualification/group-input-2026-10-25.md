# Exact group input and replay — 2026-10-25

Status: qualified through the normal public job and build producer, with all
required consumer cases passing. Router correction integrated at
`68dda6edc61d23fbe23a7751e131228eb304842e`. The acquisition release `ef2609e2` and geometric
group-matching baseline `c19804e4` remain unchanged.

The native `-DQ0` probe exposed an input defect: the public CLI interpreted an
exact net name as an option. Separately, replay converted a nested group list
into one string. Both failures are retained, including two failing tests before
the correction. These were invocation defects, not failed meander geometry.

`--length-match-group-json` now accepts one JSON array per group, using the
existing destination and validation. It can be repeated or mixed with legacy
syntax in order. Canonical command replay and normal `router.length_match`
serialization use that form. No new request schema, matcher, rule severity or
routing algorithm was added. The requested group and final board remain bound
to independent native path verification.

The router's actual native case routes `-DQ0` and `--help`, retains exactly those
members, meets the 0.1 mm requirement and passes original native DRC with zero
opens. Legacy/mixed input, exact names with brackets/spaces/quotes, group order
and corpus reconstruction survive replay. Seven malformed or invalid JSON-group
cases refuse before routing.

All 119 focused group/interface cases have passing evidence with zero skips.
The first corrected run exposed two test setup mistakes: the corpus entry lacked
its required backend field, and a test assumed the backend member list preserved
input order. The test now checks exact membership while independently checking
public group order. One interface case initially lacked the local Freerouting
runtime; verified 2.5.0 was restored. Only those three cases were rerun, passing
in 27.94 seconds. Initial failures and corrections remain retained in
`build/group-json/router-{red,green-v1,corrected-v2}.{log,xml}` and the source-bound
`router-gate-closure.json`.

The normal public job passes on a four-lane native input named `-DQ0`, `--help`,
`DQ*` and `/sheet/DQ[3]`, with four independently checked saved paths and zero
opens/native errors. Inputs remain unchanged, and changed-group resume refuses
without replacing the checkpoint. Its final board SHA-256 is
`bf65dc9dbb764cbc603983f1083d74deb2db535f26e6bbb5d1b58529ef561eac`.
The standalone board retains eight baseline missing-library-context warnings;
this is not a manufacturing acceptance claim. The actual build producer includes
the native libraries and passes with zero findings, board SHA-256
`4b7644e4c7a8e60e37a416314c24346f4b7399edf10d8c67b3a6b2b3844ca4ca`.

All 998 required consumer cases in 52 modules pass with zero skips. The six
public/native group cases pass in 110.0 seconds. The clean image is
`agent-ee:exec-cd0837f0024b4fe7`, ID
`sha256:9466f5f3773f1561cddc4d7550ee27d86eaeb305feabb28e18d919c2c230489a`.
Its build passes 103 placement-engine tests; KiCad is 10.0.7 and the router
reports 0.23.1. Container `c25952829a79` exits 0, captured directly in
`gate-container-exit.json`. Per-case rosters, tested source and final native
artifacts are bound in `consumer-gate-closure.json`. The first unused aggregation
script assumed no standalone library warnings; it was corrected after reading
the actual report. Those warnings remain visible and the complete build is
graded separately; no authored rule or required check was weakened.

The prior matched stock geometry evidence remains in
[group matching](group-matching-2026-10-25.md): the engine and geometric controls
are unchanged. No new speed, reliability or broad superiority claim follows.
Propagation-delay matching and the broader goal remain unfinished.

Reproduce the normal job after installing the qualified pin, using a fresh
output directory:

```sh
ae-ci-exec . -- env AGENT_EE_LENGTH_EVIDENCE=/opt/agent-ee/build/group-input-reproduction \
  python3 scripts/strict_suite.py --jobs 1 test_placement_job_length_groups
```

Replay the captured exact-name request in the supported environment:

```sh
ae-ci-exec . -- bin/agent-ee place run \
  build/group-json/public/option-inputs/job.json --out build/group-input-replay
```

The accepted acquisition package remains unchanged. Supplier preview and physical
validation remain unperformed and explicit in that release.
