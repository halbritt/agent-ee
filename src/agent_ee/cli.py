"""Command parser: parse arguments, call the owning modules and print. The run record lives in run_record."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import run_record
from .storage import digest, read_json

DOCTOR_PROBES = {"kicad-cli": ["kicad-cli", "version"], "ngspice": ["ngspice", "--version"],
    "bwrap": ["bwrap", "--version"], "python3": ["/usr/bin/python3", "--version"],
    "pcbnew": ["/usr/bin/python3", "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"],
    "claude": ["claude", "--version"]}
HARNESS_PROBES = ("claude",)


def doctor(checks_only=False, router_checkout=None):
    """Probe versions and hash the probed binaries; real runs establish integration."""
    checks, skipped = {}, {}
    probes = dict(DOCTOR_PROBES)
    # Package entry points may run in a venv that cannot see system pcbnew.
    probes['python3'] = [sys.executable, '--version']
    probes['pcbnew'] = [sys.executable, '-c', 'import pcbnew; print(pcbnew.GetBuildVersion())']
    from .placement_rust_runtime import resolve_executable
    from .placement_runtime import PlanningProcessError
    try:
        planner = resolve_executable()
    except (OSError, PlanningProcessError) as exc:
        checks["agent-ee-placement"] = {"error": str(exc)}
    else:
        probes["agent-ee-placement"] = [str(planner), "--version"]
    if shutil.which("pdftotext"):
        probes["pdftotext"] = ["pdftotext", "-v"]
    for name, command in probes.items():
        if checks_only and name in HARNESS_PROBES:
            skipped[name] = "not probed with --checks-only; reviews and adjudication still require it"
            continue
        try:
            p = subprocess.run(command, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks[name] = {"error": str(exc)}
            continue
        record = {"path": command[0] if name == "agent-ee-placement" else shutil.which(command[0]), "exit_code": p.returncode,
                  "version_output": p.stdout.strip(), "diagnostics": p.stderr.strip()}
        # Bind the version/import probe to the binary used by this interpreter.
        try:
            record["binary"] = run_record.pcbnew_extension(python=sys.executable)[0] if name == "pcbnew" else record["path"]
            record["sha256"] = digest(record["binary"]) if record["binary"] else None
        except (OSError, subprocess.TimeoutExpired) as exc:
            record.update(sha256=None, hash_error=str(exc))
        record.setdefault("binary", None)
        if name == "agent-ee-placement":
            receipt = Path(command[0]).with_name(Path(command[0]).name + '.json')
            if receipt.is_file():
                try:
                    record["build_receipt"] = {"path": str(receipt), "sha256": digest(receipt)}
                    declared = read_json(receipt)
                    if (not isinstance(declared, dict)
                            or declared.get("schema") != "agent-ee.placement-engine-install/1"
                            or declared.get("binary_sha256") != record.get("sha256")):
                        raise ValueError("Rust executable differs from its build receipt")
                except (OSError, ValueError) as exc:
                    record["error"] = str(exc)
        if name == "kicad-cli":
            # Which ERC JSON coordinate correction the erc check will apply (AGENTEE-66).
            from .kicad_quirks import erc_coordinate_regime
            record["erc_json_coordinates"] = erc_coordinate_regime(
                record["version_output"] if p.returncode == 0 else None)
        checks[name] = record
    # Optional installed analyzers are tools, not core package dependencies.
    analyzer = os.environ.get("AGENT_EE_KICAD_HAPPY")
    if analyzer:
        from .kicad_happy import installation
        try:
            installed = installation(Path(analyzer).expanduser().resolve())
            checks["kicad-happy"] = {"path": analyzer, "exit_code": 0, **installed}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checks["kicad-happy"] = {"path": analyzer, "error": str(exc)}
    if router_checkout is not None:
        from .routing_adapter import preflight
        try:
            router = preflight(router_checkout)
            checks['kicad-route'] = {**router, 'exit_code': 1 if router['problems'] else 0,
                                    'sha256': router['executable_sha256']}
        except (OSError, ValueError) as exc:
            checks['kicad-route'] = {'path': str(router_checkout), 'error': str(exc)}
    report = {"prerequisites": checks,
        "runtime": {"executable": sys.executable, "version": sys.version,
                    "prefix": sys.prefix, "base_prefix": sys.base_prefix,
                    "system_python": '/usr/bin/python3',
                    "uses_system_python": (Path(sys.executable).resolve() == Path('/usr/bin/python3').resolve()
                                           and sys.prefix == sys.base_prefix),
                    "launcher": sys.argv[0], "cli_source": __file__},
        "note": "Presence/version probes do not establish engineering integration. Run a declared scope."}
    if skipped:
        report["skipped"] = skipped
    print(json.dumps(report, indent=2))
    return 0 if all(c.get("exit_code") == 0 and c.get("sha256") and not c.get("error") for c in checks.values()) else 1


def view3d(source, out, *, port=0, no_browser=False, export_only=False):
    """Build a separate preview and keep its loopback server alive until Ctrl-C."""
    if not 0 <= port <= 65535:
        raise ValueError("viewer port must be between 0 and 65535")
    from .viewer import build_viewer, serve_viewer

    print("Exporting the board with KiCad…", flush=True)
    page = build_viewer(source, out)
    print(f"Viewer bundle: {page.parent}", flush=True)
    if export_only:
        return 0
    import webbrowser

    with serve_viewer(page.parent, port=port) as server:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        print(f"3D PCB viewer: {url}\nPress Ctrl-C to stop.", flush=True)
        if not no_browser:
            try:
                if not webbrowser.open(url):
                    print("Open the URL above in your browser.", file=sys.stderr)
            except (OSError, webbrowser.Error) as exc:
                print(f"Could not open a browser: {exc}. Open the URL above.", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def render3d(args):
    """Render a complete native three-view bundle without starting a browser."""
    from .render3d import render_board

    try:
        manifest = render_board(args.source, args.out, width=args.width,
            height=args.height, quality=args.quality, jobs=args.jobs,
            timeout=args.timeout, models=args.models, fetch_models=args.fetch_models,
            progress=lambda message: print(message, flush=True))
    except KeyboardInterrupt:
        print("agent-ee: rendering interrupted; inspect the retained output logs", file=sys.stderr)
        return 130
    print(f"3D render manifest: {manifest}", flush=True)
    return 0


def print_runtime_observations(observations):
    for change in observations:
        print(f"observation: unused harness runtime {change['name']} changed since capture; "
              "this run never invoked it, so integrity still holds")


# Commands whose whole path takes a caller's host fingerprints (run_record.pinned).
HOST_COMMANDS = frozenset({'review', 'resume', 'inspect', 'verify', 'adjudicate', 'close-correction', 'campaign',
                           'verify-campaign', 'campaign-feedback', 'bench-review', 'bench-status', 'bench-recover',
                           'bench-launch', 'bench-new-attempt', 'diagnose', 'evidence-request', 'evidence-replay',
                           'evidence-graduation'})


def main(argv=None, *, host=None):
    """Run one command. host, when given, holds the caller's policy and runtime fingerprints (run_record.pinned)."""
    from . import placement_job
    from . import parts_stage
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    placement_parser = sub.add_parser('place', help='place, route and independently check a native board')
    placement_job.configure_parser(placement_parser)
    parts_parser = sub.add_parser('parts', help='compile selected parts into circuit intent')
    parts_stage.configure_parser(parts_parser)
    from . import build_workflow
    build_parser = sub.add_parser('build', help='run a captured engineering design through native manufacture checks')
    build_workflow.configure_parser(build_parser)
    scaffold_parser = sub.add_parser('scaffold', help='create an empty native candidate from captured planning inputs')
    scaffold_parser.add_argument('envelope')
    scaffold_parser.add_argument('parts')
    planning = scaffold_parser.add_mutually_exclusive_group(required=True)
    planning.add_argument('--scope', help='stage-one scope/2 or scope/3 with captured project rules')
    planning.add_argument('--plan', help='agent-ee.scaffold-plan/1; derive checks and project rules')
    scaffold_parser.add_argument('--intent', help='captured bound connectivity intent for native sheet identity')
    scaffold_parser.add_argument('--layout', help='captured emitter layout; required with --intent')
    scaffold_parser.add_argument('--out', required=True)
    catalog = sub.add_parser('capture-catalog', help='prepare a pinned local catalog; no upstream authentication')
    catalog.add_argument('manifest')
    catalog.add_argument('--source-root', required=True)
    catalog.add_argument('--out', required=True)
    viewer = sub.add_parser("view3d", help="open a local 3D preview of a KiCad board or captured review")
    viewer.add_argument("source", help=".kicad_pcb file or retained review directory")
    viewer.add_argument("--out", required=True, help="new directory for the exported viewer bundle")
    viewer.add_argument("--port", type=int, default=0, help="loopback HTTP port (default: choose a free port)")
    viewer.add_argument("--no-browser", action="store_true", help="print the URL without opening a browser")
    viewer.add_argument("--export-only", action="store_true", help="build the bundle without starting a server")
    renders = sub.add_parser("render3d", help="ray trace top, bottom and isometric PCB images with native KiCad")
    renders.add_argument("source", help=".kicad_pcb file or retained review directory")
    renders.add_argument("--out", required=True, help="new directory for images, snapshots, logs and manifest")
    renders.add_argument("--width", type=int, default=4096, help="requested pixel width (default: 4096)")
    renders.add_argument("--height", type=int, default=3072, help="requested pixel height (default: 3072)")
    renders.add_argument("--quality", choices=("basic", "high"), default="high", help="native ray-tracing quality (default: high)")
    renders.add_argument("--jobs", type=int, choices=(1, 2, 3), default=3, help="concurrent views (default: 3)")
    renders.add_argument("--timeout", type=float, default=1200, help="maximum seconds per view (default: 1200)")
    renders.add_argument("--models", help="local stock-model directory; maps KiCad model-library variables")
    renders.add_argument("--fetch-models", action="store_true", help="fetch referenced stock models into --models from pinned official KiCad sources")
    request = sub.add_parser("evidence-request", help="export an inert request from a retained concern")
    request.add_argument("run")
    request.add_argument("--finding", required=True)
    request.add_argument("--spec", required=True)
    request.add_argument("--script")
    request.add_argument("--out", required=True)
    replay = sub.add_parser("evidence-replay", help="networkless nonauthoritative Python experiment")
    replay.add_argument("request")
    replay.add_argument("--out", required=True)
    graduation = sub.add_parser("evidence-graduation", help="export an inert unaccepted check proposal")
    graduation.add_argument("request")
    graduation.add_argument("--proposal", required=True)
    graduation.add_argument("--replay")
    graduation.add_argument("--out", required=True)
    operation = sub.add_parser("verify-evidence-operation", help="verify standalone operation bytes, not acceptance")
    operation.add_argument("path")
    doctor_parser = sub.add_parser("doctor", help="inspect installed prerequisites; real runs establish integration")
    doctor_parser.add_argument("--checks-only", action="store_true",
                               help="probe only the native check toolchain, not reviewer harnesses")
    doctor_parser.add_argument('--router-checkout', type=Path,
                               help='also verify the pinned routing checkout and installed backend')
    bench = sub.add_parser("bench-review", help="capture bench evidence and rerun calibrated checks/reviews")
    bench.add_argument("run")
    bench.add_argument("report")
    bench.add_argument("--out", required=True)
    bench.add_argument("--max-seconds", type=float, default=1200)
    bench.add_argument("--operation-id", help="opt into durable bench-transaction/1")
    for command in ('bench-status', 'bench-recover', 'bench-launch'):
        transaction = sub.add_parser(command, help='inspect, reconcile, or explicitly launch a prepared bench transaction')
        transaction.add_argument('path')
    feedback = sub.add_parser("campaign-feedback", help="ingest bench evidence and continue a campaign in a new model epoch")
    feedback.add_argument("campaign")
    feedback.add_argument("report")
    feedback.add_argument("--out", required=True)
    feedback.add_argument("--max-seconds", type=float, default=1200)
    feedback.add_argument("--operation-id", help="bounded durable feedback; no automatic repair continuation")
    attempt = sub.add_parser('bench-new-attempt', help='explicitly authorize a fresh review after uncertain execution')
    attempt.add_argument('path')
    attempt.add_argument('--attempt-id', required=True)
    attempt.add_argument('--out', required=True)
    diagnosis = sub.add_parser("diagnose", help="read-only advisory run or campaign diagnosis")
    diagnosis.add_argument("path")
    diagnosis.add_argument("--json", action="store_true", dest="machine")
    diagnosis.add_argument("--snapshot-only", action="store_true")
    campaign_verify = sub.add_parser("verify-campaign", help="verify all candidate history links and convergence state")
    campaign_verify.add_argument("campaign")
    campaign = sub.add_parser("campaign", help="run a bounded single-writer implementation/review campaign")
    campaign.add_argument("config")
    campaign.add_argument("--out", required=True)
    review = sub.add_parser("review", help="capture and review a fresh candidate")
    review.add_argument("scope")
    review.add_argument("--out", required=True)
    review.add_argument("--max-seconds", type=float, default=1200)
    review.add_argument("--checks-only", action="store_true")
    review.add_argument("--profile", help="explicit agent-ee.profile/1 JSON file")
    review.add_argument("--roles", help="comma-separated profile roles for a targeted, always-incomplete review run")
    review.add_argument("--routing-receipt", help="routing adapter receipt.json whose routed board is the reviewed "
                        "board; verified and bound into the RF review (otherwise its evidence is missing)")
    for cmd in ("inspect", "verify"):
        p = sub.add_parser(cmd)
        p.add_argument("run")
        if cmd == "verify":
            p.add_argument("--snapshot-only", action="store_true")
    resume = sub.add_parser("resume", help="preserve old run; recapture/rerun all evidence into new run")
    resume.add_argument("run")
    resume.add_argument("--out", required=True)
    resume.add_argument("--max-seconds", type=float, default=1200)
    decision = sub.add_parser("adjudicate")
    decision.add_argument("run")
    decision.add_argument("decisions")
    closure = sub.add_parser("close-correction")
    closure.add_argument("previous")
    closure.add_argument("current")
    closure.add_argument("--finding", required=True)
    closure.add_argument("--checks", required=True)
    closure.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if host is not None and args.command not in HOST_COMMANDS:
        # A supplied host is honoured end to end or refused, never silently dropped.
        print(f"agent-ee: {args.command} does not take supplied host fingerprints", file=sys.stderr)
        return 2
    try:
        if args.command == 'place':
            return placement_job.execute(args, placement_parser)
        if args.command == 'parts':
            return parts_stage.execute(args)
        if args.command == 'build':
            return build_workflow.execute(args)
        if args.command == 'scaffold':
            from .scaffold import scaffold
            receipt = scaffold(args.envelope, args.parts, args.out, scope_path=args.scope, plan_path=args.plan,
                               intent_path=args.intent, layout_path=args.layout)
            print(receipt['candidate_id'])
            return 0
        if args.command == 'capture-catalog':
            from .catalog import capture_catalog, strict_json
            capture_catalog(strict_json(Path(args.manifest).read_text(encoding='utf-8')), args.source_root, args.out)
            print(digest(Path(args.out) / 'manifest.json'))
            return 0
        if args.command == "render3d":
            return render3d(args)
        if args.command == "view3d":
            return view3d(args.source, args.out, port=args.port,
                          no_browser=args.no_browser, export_only=args.export_only)
        if args.command == "evidence-request":
            from .evidence_requests import export_request
            record = export_request(args.run, args.finding, read_json(args.spec), args.out, script=args.script,
                                    host=host)
            print(record["request_id"])
            return 0
        if args.command == "evidence-replay":
            from .replay import replay
            receipt = replay(args.request, args.out, host=host)
            print(json.dumps(receipt, indent=2))
            return 0 if receipt["status"] == "completed" else 130 if receipt["execution"] == "interrupted" else 1
        if args.command == "evidence-graduation":
            from .evidence_requests import export_graduation
            export_graduation(args.request, read_json(args.proposal), args.out, replay_dir=args.replay, host=host)
            return 0
        if args.command == "verify-evidence-operation":
            from .evidence_requests import verify_operation
            print(verify_operation(args.path)["operation_id"])
            return 0
        if args.command == "diagnose":
            from .diagnostics import diagnose, render_diagnosis
            report = diagnose(args.path, current_source=not args.snapshot_only, host=host)
            print(json.dumps(report, indent=2) if args.machine else render_diagnosis(report))
            return 0
        if args.command == "doctor":
            return doctor(checks_only=args.checks_only, router_checkout=args.router_checkout)
        if args.command in ('bench-status', 'bench-recover', 'bench-launch'):
            from .bench_transactions import recover, status
            value = (status(args.path, host=host) if args.command == 'bench-status' else
                     recover(args.path, launch_review=args.command == 'bench-launch', host=host))
            print(json.dumps(value, indent=2))
            return 0 if value['disposition'] in ('completed', 'ready_for_explicit_review') else 1
        if args.command == "bench-review":
            if args.operation_id:
                from .bench_transactions import transact
                value = transact(args.run, args.report, args.out, args.max_seconds, operation_id=args.operation_id,
                                 host=host)
                print(json.dumps(value, indent=2))
                return 0 if (value.get('current_review') or {}).get('outcome') == 'criteria_satisfied' else 1
            from .empirical import bench_review
            return bench_review(args.run, args.report, args.out, args.max_seconds, host=host)
        if args.command == 'bench-new-attempt':
            from .bench_transactions import new_attempt
            value = new_attempt(args.path, args.attempt_id, args.out, host=host)
            print(json.dumps(value, indent=2))
            return 0 if value['disposition'] == 'completed' else 1
        if args.command == "campaign-feedback":
            if args.operation_id:
                from .bench_transactions import campaign_transaction
                value = campaign_transaction(args.campaign, args.report, args.out, args.max_seconds,
                                             operation_id=args.operation_id, host=host)
                print(json.dumps(value, indent=2))
                return 0 if (value.get('current_review') or {}).get('outcome') == 'criteria_satisfied' else 1
            from .campaign import campaign_feedback
            return campaign_feedback(args.campaign, args.report, args.out, args.max_seconds, host=host)
        if args.command == "verify-campaign":
            from .campaign import verify_campaign
            observations = []
            state = verify_campaign(args.campaign, observations=observations, host=host)
            print(state["state"])
            artifacts = state.get("fab_artifacts")
            if artifacts:
                from .fab_package import rotation_blocker
                run = state["history"][-1]["run"]
                rotation = artifacts.get("rotation") or {}
                print(f"fab_rotation {rotation.get('status')} policy {rotation.get('policy_sha256')}")
                obligations = rotation.get("preview_obligations")
                if obligations:
                    # AGENTEE-151: recomputed operator obligations, before the artifact hash lines.
                    print(f"fab_preview_obligations {len(obligations['designators'])} policy "
                          f"{obligations['policy_sha256']}: {obligations['action']}")
                    for entry in obligations["designators"]:
                        print(f"fab_preview_obligation {entry['designator']} {entry['side']}")
                blocker = rotation_blocker(rotation.get("status"), obligations, rotation.get("policy_sha256"))
                if blocker:
                    print(blocker)
                print(f"fab_artifacts {artifacts['artifacts_id']} candidate {artifacts['candidate_id']}")
                for rel, sha in artifacts["files"].items():
                    print(f"{sha}  {run}/{rel}")
            print_runtime_observations(observations)
            return 0
        if args.command == "campaign":
            from .campaign import run_campaign
            return run_campaign(args.config, args.out, host=host)
        if args.command == "review":
            routing_context = None
            if args.routing_receipt:
                # Anchored at invocation; the receipt itself is re-verified by the routing adapter.
                receipt = Path(args.routing_receipt).resolve()
                routing_context = {"receipt": {"path": str(receipt), "sha256": digest(receipt)}}
            return run_record.run_review(args.scope, args.out, args.max_seconds, checks_only=args.checks_only, profile=args.profile,
                              review_roles=args.roles.split(",") if args.roles else None,
                              routing_context=routing_context, host=host)
        if args.command == "inspect":
            print(json.dumps(run_record.inspect(args.run, host=host), indent=2))
        elif args.command == "verify":
            observations = []
            result = run_record.verify(args.run, current_source=not args.snapshot_only, observations=observations,
                                       host=host)
            print(f"fresh {result['outcome']}: {result['candidate_id']}")
            print_runtime_observations(observations)
        elif args.command == "resume":
            old = read_json(Path(args.run) / "candidate.json")
            return run_record.run_review(old["scope_path"], args.out, args.max_seconds, previous=str(Path(args.run).resolve()), profile=old.get("profile_path"), host=host)
        elif args.command == "adjudicate":
            run_record.apply_decisions(args.run, args.decisions, host=host)
        elif args.command == "close-correction":
            event = run_record.link_correction(args.previous, args.current, args.finding, args.checks.split(","),
                                               args.reason, host=host)
            print(json.dumps(event, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        from .bench_transactions import TransactionError
        if isinstance(exc, TransactionError):
            print(json.dumps(exc.diagnostic, indent=2), file=sys.stderr)
        elif args.command in ('bench-status', 'bench-recover', 'bench-launch', 'bench-new-attempt') or (
                args.command in ('bench-review', 'campaign-feedback') and args.operation_id):
            print(json.dumps({'schema': 'agent-ee.bench-recovery/1', 'disposition': 'unverified',
                              'reason': f'{type(exc).__name__}: {exc}',
                              'operator_actions': ['preserve retained evidence; diagnose before recovery']}, indent=2), file=sys.stderr)
        else:
            print(f"agent-ee: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
