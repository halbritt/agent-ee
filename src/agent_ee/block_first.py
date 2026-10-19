"""Capture and check independent sheets before publishing an integrated proposal.

This is a coordinator gate over ordinary scope/2 or scope/3 runs. It supplies no
reviewer judgment and never substitutes block evidence for fresh board checks.
"""
import argparse
import math
import os
import shutil
import time
from contextlib import ExitStack
from pathlib import Path

from .baseline import freeze_evidence, load_baseline, verify_evidence
from .candidate import capture, verify_snapshot
from .checks import run_checks
from .locking import reader_lock, writer_lock
from .native_netlist import read_native_inventory
from .reference_blocks import hierarchy_ports
from .review_run import tree_hashes
from .schematic_lint import load_sheets
from .storage import digest, read_json, write_json
from .symbol_library import children, field

REPORT = 'block-verification.json'
INTEGRATION = 'block-integration.json'


def _required(manifest):
    scope = manifest['scope']
    if scope['schema'] not in ('agent-ee.scope/2', 'agent-ee.scope/3'):
        raise ValueError('block-first requires an immutable scope/2 or scope/3 baseline')
    specs = scope['checks']
    if not any(s['kind'] == 'erc' for s in specs) or not any(
            s['kind'] == 'netlist_parity' and s['params']['predicate'] == 'topology'
            and s['params']['multiset'] == 'exactly' for s in specs):
        raise ValueError('block-first requires ERC and exact topology netlist_parity checks')


def _checks(run, manifest):
    checks = [read_json(run / 'checks' / (s['id'] + '.json')) for s in manifest['scope']['checks']]
    verify_evidence(run, checks, required=True)
    return checks


def _status(run, checks):
    if any(c['execution'] != 'ok' or c['outcome'] == 'unknown' for c in checks):
        return 'missing_coverage'
    if any(c['outcome'] == 'defect' for c in checks):
        return 'defect'
    by_id = {c['id']: c for c in checks}
    baseline = load_baseline(run)
    # Use scope/3's existing bound-coverage classification; informational
    # prerequisites cannot turn an acceptance check's info result into a pass.
    acceptance = baseline.get('coverage', {}).get('requirements', {}).values()
    if any(by_id[c]['outcome'] != 'pass' for req in acceptance for c in req['acceptance_checks']):
        return 'missing_coverage'
    if any(c['outcome'] != 'pass' for c in checks if c['kind'] in ('erc', 'netlist_parity')):
        return 'missing_coverage'
    return 'verified'


def _subject(run, manifest):
    sheets = load_sheets(run / 'inputs', manifest['scope']['schematic'])
    if len(sheets) != 2:
        raise ValueError('each block needs a root harness and one standalone child sheet')
    subject = sheets[1]
    ports = hierarchy_ports((run / 'inputs' / subject['file']).read_bytes())
    if not ports:
        raise ValueError('block requires explicit hierarchical boundary ports')
    _parent_ports(sheets[0]['tree'], subject, ports)
    return subject, ports


def _parent_ports(root, sheet, ports):
    nodes = [n for n in children(root, 'sheet') if field(n, 'uuid', 1) == [sheet['path'].strip('/')]]
    if len(nodes) != 1:
        raise ValueError('block needs one unambiguous parent sheet symbol')
    pins = children(nodes[0], 'pin')
    port_map = {p[1]: p[2] for p in pins if len(p) >= 3}
    if len(port_map) != len(pins) or port_map != ports:
        raise ValueError(sheet['file'] + ': parent sheet pins differ from the checked block interface')


def _block_report(run):
    manifest = verify_snapshot(run)
    _required(manifest)
    subject, ports = _subject(run, manifest)
    checks = _checks(run, manifest)
    for check in checks:
        if check['kind'] == 'netlist' and check['execution'] == 'ok':
            inventory = read_native_inventory(run / 'work' / check['id'] / 'design.xml')
            if any(c['sheet'] != subject['path'] for c in inventory['components'].values()):
                raise ValueError('harness must contain no physical components; virtual power flags are allowed')
    return dict(schema='agent-ee.block-verification/1', status=_status(run, checks),
                candidate_id=manifest['candidate_id'], baseline_id=load_baseline(run)['baseline_id'],
                sheet=subject['file'], sheet_sha256=subject['sha256'],
                ports=ports, files=manifest['files'], checks=checks)


def _timeout(seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('timeout must be finite and positive')
    return time.monotonic() + seconds


def verify_block(scope, out, *, timeout_s=900):
    """Retain a fresh block capture and native checks, including failed coverage.

    A crashed worker leaves the initial missing-coverage record; an existing
    destination is never reused. Independent callers may use separate outputs.
    """
    deadline = _timeout(timeout_s)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    with writer_lock(out):
        report = dict(schema='agent-ee.block-verification/1', status='missing_coverage',
                      reason='worker did not finish')
        write_json(out / REPORT, report)
        try:
            manifest = capture(scope, out)
            _required(manifest)
            _subject(out, manifest)
            checks = run_checks(out, deadline)
            freeze_evidence(out, checks)
            report = _block_report(out)
        except (OSError, ValueError) as exc:
            report.update(status='missing_coverage', reason=str(exc))
        except KeyboardInterrupt:
            report.update(status='missing_coverage', reason='worker interrupted')
            write_json(out / REPORT, report)
            raise
        write_json(out / REPORT, report)
        return report


def _bound_blocks(run, manifest, blocks):
    """Recompute sealed evidence; compare all dependencies, not a saved status."""
    inputs = run / 'inputs'
    sheets = load_sheets(inputs, manifest['scope']['schematic'])
    targets = [s['file'] for s in sheets[1:]]
    if not targets or len(targets) != len(set(targets)) or set(targets) != set(blocks):
        raise ValueError('every child sheet needs exactly one block run; no repeated sheets')
    if any(s['path'].count('/') != 2 for s in sheets[1:]):
        raise ValueError('block-first integration requires direct standalone child sheets')
    root = sheets[0]['tree']
    bound = {}
    for sheet in sheets[1:]:
        target = sheet['file']
        try:
            bound[target] = _bind_block(root, sheet, manifest, Path(blocks[target]))
        except (OSError, ValueError) as exc:
            bound[target] = dict(status='missing_coverage', reason=str(exc))
    return bound


def _bind_block(root, sheet, manifest, block):
    target = sheet['file']
    report = _block_report(block)
    if read_json(block / REPORT) != report:
        raise ValueError('block worker did not finish or its report changed')
    if report['status'] != 'verified':
        return dict(status=report['status'], candidate_id=report['candidate_id'])
    if target != report['sheet']:
        raise ValueError('block capture must use the delivered sheet path')
    # Contracts, parts, golden netlists and baseline sources must all retain
    # their captured identities. An interface edit always needs a new run.
    changed = sorted(p for p, sha in report['files'].items() if manifest['files'].get(p) != sha)
    if changed:
        raise ValueError('block inputs changed; re-check required: ' + ', '.join(changed))
    _parent_ports(root, sheet, report['ports'])
    return dict(status='verified', candidate_id=report['candidate_id'],
                baseline_id=report['baseline_id'], sheet_sha256=report['sheet_sha256'],
                evidence_sha256=digest(block / 'work/verification/evidence.json'))


def integrate(scope, blocks, out, *, timeout_s=900):
    """Publish a checked hierarchical proposal only after every block is covered.

    The sole design writer authors the proposed root and children (e.g. with
    block_adaptation). This gate captures that proposal, verifies exact child
    evidence and pin interfaces, runs fresh integrated checks, then publishes
    the captured tree at out/design. It never edits the writer's source tree.
    """
    deadline = _timeout(timeout_s)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    with ExitStack() as locks:
        locks.enter_context(writer_lock(out))
        report = dict(schema='agent-ee.block-integration/1', status='missing_coverage',
                      reason='integration did not finish', blocks={}, recheck_required=[])
        write_json(out / INTEGRATION, report)
        try:
            for path in sorted({str(Path(p).resolve()) for p in blocks.values()}):
                locks.enter_context(reader_lock(path))
            manifest = capture(scope, out)
            _required(manifest)
            report['candidate_id'] = manifest['candidate_id']
            bound = _bound_blocks(out, manifest, blocks)
            report['blocks'] = bound
            report['recheck_required'] = sorted(p for p, b in bound.items() if b['status'] != 'verified')
            if any(b['status'] != 'verified' for b in bound.values()):
                report['status'] = ('defect' if any(b['status'] == 'defect' for b in bound.values())
                                    else 'missing_coverage')
                report['reason'] = 'integration refused: block defect or missing coverage'
            else:
                checks = run_checks(out, deadline)
                freeze_evidence(out, checks)
                verify_snapshot(out)
                _checks(out, manifest)
                if _bound_blocks(out, manifest, blocks) != bound:
                    raise ValueError('block evidence changed during integration')
                report.update(status=_status(out, checks), checks=checks)
                report.pop('reason', None)
                if report['status'] == 'verified':
                    shutil.copytree(out / 'inputs', out / 'pending-design')
                    verify_snapshot(out)
                    if tree_hashes(out / 'pending-design') != manifest['files']:
                        raise ValueError('published design differs from the checked capture')
                    os.rename(out / 'pending-design', out / 'design')
        except (OSError, ValueError) as exc:
            report.update(status='missing_coverage', reason=str(exc))
        except KeyboardInterrupt:
            report.update(status='missing_coverage', reason='integration interrupted')
            write_json(out / INTEGRATION, report)
            raise
        write_json(out / INTEGRATION, report)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('verify', 'integrate'))
    parser.add_argument('--scope', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--block', action='append', default=[], metavar='SHEET=RUN')
    parser.add_argument('--timeout-s', type=float, default=900)
    args = parser.parse_args(argv)
    blocks = {}
    for binding in args.block:
        sheet, sep, run = binding.partition('=')
        if not sep or not sheet or not run or sheet in blocks:
            parser.error('--block requires a unique SHEET=RUN')
        blocks[sheet] = run
    if args.command == 'verify' and blocks:
        parser.error('--block applies only to integrate')
    report = (verify_block(args.scope, args.out, timeout_s=args.timeout_s) if args.command == 'verify'
              else integrate(args.scope, blocks, args.out, timeout_s=args.timeout_s))
    print(report['status'])
    return 0 if report['status'] == 'verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
