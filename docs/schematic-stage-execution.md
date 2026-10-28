# Native children of the schematic annotation stage

The receipted `forward-annotate` step runs the fixed
`agent_ee.schematic_stage annotate` entrypoint from the captured read-only
`/tool/agent-ee` source. `stage_receipts._run_bound_step` still creates its
ordinary networkless Sandbox intent, observes/rechecks source mounts, captures
immutable inputs and adopts only declared outputs from a completed receipt.
No executor, adapter default, isolation policy or receipt rule changes here.

Only this entrypoint constructs the private forward-annotation stage context.
It checks the kernel mount table for the expected tmpfs-root filesystem view,
read-only candidate/tool/application/system mounts, writable `/work`, absence
of unexpected host mounts and execution from the captured tool path. A regular
host/container root or incomplete view refuses before stage output creation.
There is no environment switch, CLI host-execution option or error fallback.
The fixed receipt launcher supplies PID/IPC/UTS and network namespaces; the
mount check is an additional entrypoint guard, not independent authentication
of namespace ancestry against a malicious host owner.

Inside that existing boundary, netlist export uses `native_run.Host` with output
`/work/out/netlist/design.xml`. Host here is a supervised process group inside
the outer sandbox, with the existing timeout, status, log, interrupt and cleanup
semantics. It does not create another namespace or reach the machine host.
Facts, fresh replan, apply and independent saved-board facts likewise run in
fresh supervised Python children. Their argv names concrete distinct private
input/output paths under `/work/out`, instead of remapping each scratch to
`/candidate` and `/work`. Private input file inventories are compared across
these children. The outer candidate remains read-only; original byte bindings,
fresh-plan equality, saved verification, final hash checks and atomic publication
remain in force. The native writer and independent reader are separate processes.

Standalone `forward_annotation` plan/apply/board-facts and direct
`schematic_stage.run` calls retain Sandbox defaults and their existing virtual
paths. They do not infer confinement from environment variables or automatically
retry a failed sandbox as Host. The stage context is a private implementation
contract, not a supported general execution adapter.

The custom-pad library probe has a separate hard-coded mount/path protocol.
Confined annotation returns failed/unknown with
`native_custom_pad_stage_pending` before entering that probe. Qualifying custom
pads needs a separately sandboxed library step (or another independently reviewed
narrow bridge); standalone custom-pad indexing remains unchanged. This repair
therefore does not qualify every annotation input supported by standalone tools.
Later passive-ratings extraction also retains its own Sandbox calls and is
outside this annotation repair.

CPU tests intercept all process effects. They establish path composition,
controls/refusals and status handling, not native confinement or board acceptance.
After independent source review, a separately admitted focused native gate must
exercise real annotation, regeneration and hierarchy, retain source/output
identities and outer receipt/mount/network facts, and show that writing the
immutable candidate and reading an absent host-only sentinel fail. No full
unchanged build rerun is justified by the previous nested-namespace refusal.
