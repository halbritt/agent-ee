# agent-ee manual

This manual describes the current agent-ee worktree (package version 0.1.0),
including scope/2 campaigns and empirical feedback. Package version alone does
not identify a compatible evidence policy; recorded Python/runtime hashes do. The implementation is a Python CLI and an
agent discovery skill. Native KiCad files are the design authority. Review
outputs apply to a captured candidate and declared criteria, not to an entire
product's hardware qualification.

| Task | Read |
| --- | --- |
| Understand the five closed-loop invariants and migration | [Closed-loop overview](closed-loop.md) |
| Declare stage gates and interpret absent design content | [Staged campaigns](staged-campaign.md) |
| Configure single-writer repair and interpret convergence stops | [Campaign guide](campaign-guide.md) |
| Bind a simulator/parser or combine cross-domain margins | [Measurement adapters](measurement-adapters.md) |
| Qualify an unloaded RC tolerance envelope | [Passive corners](passive-corners.md) |
| Request missing concern evidence or export a proposed check | [Evidence requests](evidence-requests.md) |
| Ingest bench samples and calibrate subsequent verification | [Bench feedback](bench-feedback.md) |
| Install, authenticate, upgrade or remove links | [Installation](installation.md) |
| Build the pinned image, run checks in CI or use the GitHub Action | [Container image and CI action](container.md) |
| Run a review, investigate findings and verify a repair | [Operator guide](operator-guide.md) |
| Look up a command, flag, exit code or work budget | [CLI reference](cli-reference.md) |
| Select inputs, libraries, reviewers and packet contents | [Scope reference](scope-reference.md) |
| Capture offline catalog data and select declared part intervals | [Catalog](catalog.md), [part selection](part-selection.md) |
| Check assigned footprints against catalog package names | [Package consistency](package-consistency.md) |
