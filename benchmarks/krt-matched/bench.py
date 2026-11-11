#!/usr/bin/env python3
"""Matched agent-ee vs stock KRT benchmark (AGENTEE-628). See README.md.

Every rule here is generic: nothing is keyed on a board name. Stdlib only,
plus KiCad's pcbnew for board edits and measurements.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RUNS = Path(os.environ.get('KRT_BENCH_RUNS', Path.home() / '.local/share/agent-ee/krt-matched-20261109'))
KRT = Path(os.environ.get('KRT_BENCH_KRT', Path.home() / '.cache/agent-ee-krt-bench/KiCadRoutingTools'))
KRT_PY = str(KRT / '.venv-bench/bin/python')
STAGE = REPO / 'build/krt-matched'            # gitignored; the CI image mounts REPO at /opt/agent-ee
IN_IMAGE = Path('/opt/agent-ee/build/krt-matched')
BUDGET_S = int(os.environ.get('KRT_BENCH_BUDGET_S', 900))
LOCK_PREFIXES = ('J', 'P', 'CN', 'CON', 'USB', 'H', 'MH', 'SW')
# design_compile.py defaults. Since AGENTEE-637 they hold no step count: the engine sizes the
# seeds to the job budget. SEARCH_CAPPED is the earlier default, kept for a matched rerun.
SEARCH = {'seeds': [{'seed': i, 'start': 'clustered'} for i in range(4)], 'shortlist': 4}
SEARCH_CAPPED = dict(SEARCH, steps_per_seed=100000)
# Secondary setting, the one documented in docs/benchmarks/placement-job-2026-10-24.md.
SEARCH_B = {'seeds': [{'seed': i, 'start': 'scattered' if i < 6 else 'clustered'} for i in range(12)],
            'steps_per_seed': 10000, 'shortlist': 6}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')


def companions(board):
    """The project and custom-rule files beside a board."""
    return [p for p in (Path(board).with_suffix(s) for s in ('.kicad_pro', '.kicad_dru')) if p.is_file()]


def copy_rules(rules_board, to_board):
    """Put the normalized board's own rule files beside to_board, byte for byte."""
    for src in companions(rules_board):
        shutil.copyfile(src, Path(to_board).with_suffix(src.suffix))


def run(argv, log, *, cwd=None, timeout=None, env=None):
    """Run argv in its own process group; kill the group at timeout. Returns a record."""
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with open(log, 'w') as out:
        proc = subprocess.Popen([str(a) for a in argv], cwd=cwd, env=env, stdout=out,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        timed_out = False
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                code = proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                code = proc.wait()
    return {'argv': [str(a) for a in argv], 'exit_code': code, 'timed_out': timed_out,
            'wall_s': round(time.monotonic() - start, 1), 'log': str(log)}


# ---- board edits (pcbnew) -------------------------------------------------------------

def prefix(ref):
    return re.match(r'[A-Za-z_#*]*', ref).group(0).upper()


def netted(fp):
    return any(p.GetNetCode() > 0 for p in fp.Pads())


def edit_board(src, dst, *, strip=False, lock=None):
    """Save src to dst. strip removes tracks, vias and teardrops; lock is None, 'all' or 'rule'."""
    import pcbnew
    board = pcbnew.LoadBoard(str(src))
    for fp in board.GetFootprints():                         # before any removal: see below
        if lock == 'all':
            fp.SetLocked(True)
        elif lock == 'rule':
            fp.SetLocked(fp.GetReference() in json.loads(os.environ['KRT_BENCH_LOCKED']))
    if strip:                                                # board.Remove() breaks the SWIG wrappers
        teardrops = [z for z in board.Zones() if z.IsTeardropArea()]   # copper that belongs to tracks
        for item in list(board.GetTracks()) + teardrops:
            board.RemoveNative(item)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    pcbnew.SaveBoard(str(dst), board)


def remove_pours(src, dst):
    import pcbnew
    board = pcbnew.LoadBoard(str(src))
    for zone in [z for z in board.Zones() if not z.GetIsRuleArea()]:
        board.RemoveNative(zone)         # board.Remove(zone) crashes pcbnew 10.0.8 here
    pcbnew.SaveBoard(str(dst), board)


def state_fab_rules(norm):
    """Write the stated fab rules (fab-rules.json) into the project file pcbnew made for a board without one."""
    import pcbnew
    layers = pcbnew.LoadBoard(str(norm)).GetCopperLayerCount()
    tier = '1-2' if layers <= 2 else '3+'
    values = json.loads((Path(__file__).parent / 'fab-rules.json').read_text())['by_copper_layers'][tier]
    project_path = norm.with_suffix('.kicad_pro')
    project = json.loads(project_path.read_text())
    rules = project['board']['design_settings']['rules']
    rules.update(min_clearance=values['clearance'], min_track_width=values['track_width'],
                 min_via_diameter=values['via_diameter'], min_through_hole_diameter=values['via_drill'],
                 min_hole_to_hole=values['hole_to_hole'], min_copper_edge_clearance=values['board_edge'])
    default = [c for c in project['net_settings']['classes'] if c['name'] == 'Default']
    if len(default) != 1:
        raise SystemExit('the normalized project has no single Default net class')
    default[0].update(clearance=values['clearance'], track_width=values['track_width'],
                      via_diameter=values['via_diameter'], via_drill=values['via_drill'])
    project_path.write_text(json.dumps(project, indent=2) + '\n')
    return {'copper_layers': layers, 'tier': tier, 'values': values}


def lock_set(norm_board):
    """Placement-arm locks: source locks, un-netted footprints, connector/hole/switch prefixes."""
    import pcbnew
    board = pcbnew.LoadBoard(str(norm_board))
    return sorted({fp.GetReference() for fp in board.GetFootprints()
                   if fp.IsLocked() or not netted(fp) or prefix(fp.GetReference()) in LOCK_PREFIXES})


def groups_for(board_path):
    """One group per schematic sheet; anchor is the member with the most pads.

    The rows hold name, anchor and refs only: this is the file KRT's placer reads, and its
    reader refuses a key it does not know (placement/grouping.py, GROUP_KEYS)."""
    import pcbnew
    board = pcbnew.LoadBoard(str(board_path))
    sheets = {}
    for fp in board.GetFootprints():
        if netted(fp):
            sheets.setdefault(fp.GetSheetname() or 'board', []).append(fp)
    groups = []
    for index, (sheet, fps) in enumerate(sorted(sheets.items())):
        fps.sort(key=lambda f: (-len(list(f.Pads())), f.GetReference()))
        groups.append({'name': f'g{index}_' + re.sub(r'\W+', '_', sheet).strip('_'),
                       'anchor': fps[0].GetReference(), 'refs': sorted(f.GetReference() for f in fps)})
    return {'schema': 1, 'groups': groups}


def sheet_groups(groups):
    """The groups file as agent-ee reads it: every row states that it was derived from a sheet.

    groups_for builds its rows mechanically, so no engineer declared them. For agent-ee such
    a group is membership context and applies no anchor pull (AGENTEE-637); the report of each
    job says which groups pull. This is a second file: the one KRT reads is never given the key."""
    if 'groups' not in groups:
        return groups
    return dict(groups, groups=[dict(row, origin='sheet') for row in groups['groups']])


def intent_for(board_path):
    """Minimal agent-ee intent: the schema line and a fixed pose per locked, named footprint."""
    import pcbnew
    board = pcbnew.LoadBoard(str(board_path))
    rows, seen = [], {}
    for fp in sorted(board.GetFootprints(), key=lambda f: (f.GetReference(), f.GetPosition().x, f.GetPosition().y)):
        ref = fp.GetReference()
        if not fp.IsLocked() or not ref:                     # intent cannot name an empty reference;
            continue                                         # the native lock still fixes that footprint
        seen[ref] = seen.get(ref, 0) + 1
        pos = fp.GetPosition()
        rows.append({'ref': ref if seen[ref] == 1 else f'{ref}~{seen[ref]}',
                     'x': pos.x / 1e6, 'y': pos.y / 1e6, 'side': 'B' if fp.IsFlipped() else 'F'})
    return {'schema': 'agent-ee.placement-intent/1', 'fixed_poses': rows}


def copper(board_path):
    import pcbnew
    board = pcbnew.LoadBoard(str(board_path))
    vias = length = segments = 0
    for item in board.GetTracks():
        if item.Type() == pcbnew.PCB_VIA_T:
            vias += 1
        else:
            segments += 1
            length += item.GetLength()
    return {'vias': vias, 'track_segments': segments, 'track_length_mm': round(length / 1e6, 2)}


# ---- scoring ---------------------------------------------------------------------------

def score(board, rules_board, out_dir):
    """Native DRC of board beside the normalized board's own rules, plus copper measurements."""
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    scored = out_dir / Path(rules_board).name
    shutil.copyfile(board, scored)
    copy_rules(rules_board, scored)
    report = out_dir / 'drc.json'
    proc = subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--refill-zones',
                           '--output', str(report), str(scored)], capture_output=True, text=True)
    result = {'scored_board': str(scored), 'board_sha256': sha256(board), 'drc_exit_code': proc.returncode}
    if not report.is_file():
        return dict(result, drc_error=(proc.stdout + proc.stderr)[-2000:])
    drc = json.loads(report.read_text())
    errors, warnings = {}, 0
    for row in drc.get('violations', []):
        if row.get('severity') == 'error':
            errors[row['type']] = errors.get(row['type'], 0) + 1
        else:
            warnings += 1
    result.update(unconnected=len(drc.get('unconnected_items', [])), drc_errors=sum(errors.values()),
                  drc_errors_by_type=dict(sorted(errors.items())), drc_warnings=warnings,
                  kicad_version=drc.get('kicad_version'), **copper(scored))
    return result


def findings(report):
    """The error findings of a native DRC report, each as (type, its items' descriptions).

    The descriptions name the items (pad, footprint, zone, layer) without a position, so a
    finding of the unrouted input is recognised again after the parts moved."""
    drc = json.loads(Path(report).read_text())
    return sorted((row['type'], tuple(sorted(item['description'] for item in row.get('items', []))))
                  for row in drc.get('violations', []) if row.get('severity') == 'error')


def introduced(report, input_report):
    """The error findings of report that the unrouted input does not already have, by type."""
    left = findings(input_report)
    new = {}
    for finding in findings(report):
        if finding in left:
            left.remove(finding)
        else:
            new[finding[0]] = new.get(finding[0], 0) + 1
    return dict(sorted(new.items()))


def verdict(score_, input_report):
    """A board passes when nothing is left open and it has no error the unrouted input lacks.

    The score is native DRC against the board's own rule files, never the tool's."""
    if not score_ or 'unconnected' not in score_:
        return {'passes': False, 'why': 'no scored board'}
    new = introduced(Path(score_['scored_board']).parent / 'drc.json', input_report)
    count = sum(new.values())
    why = [f"{score_['unconnected']} open"] * bool(score_['unconnected']) + [f'{count} introduced errors'] * bool(count)
    return {'passes': not why, 'why': ', '.join(why), 'open': score_['unconnected'],
            'introduced_errors': count, 'introduced_by_type': new}


def input_report(root, name, suffix=''):
    """The DRC report of the unrouted routing input, scored now when an earlier step did not."""
    report = root / 'score' / f'baseline-route-in{suffix}' / 'drc.json'
    if not report.is_file():
        norm = root / 'norm' / f'{name}.kicad_pcb'
        score(root / f'route-in{suffix}' / norm.name, norm, report.parent)
    return report


def rules_origin(root):
    """Whether the board's rules are its source project's, stated fab rules, or KiCad's defaults."""
    try:
        record = json.loads((root / 'prep.json').read_text())
    except (OSError, ValueError):
        return 'unknown'
    if record.get('stated_fab_rules'):
        return f"stated fab rules ({record['stated_fab_rules']['tier']} layers, fab-rules.json)"
    own = record.get('source_has_project_file')
    if own is None:
        source = Path(record.get('source', ''))
        if not source.is_file():
            return 'unknown (source not present)'
        own = source.with_suffix('.kicad_pro').is_file()
    return 'source project file' if own else 'KiCad defaults (the source has no project file)'


def tail(path, n=40):
    try:
        return Path(path).read_text(errors='replace').splitlines()[-n:]
    except OSError:
        return []


# ---- preparation -----------------------------------------------------------------------

def prep(args):
    name = args.name
    root = RUNS / 'boards' / name
    norm = root / 'norm' / f'{name}.kicad_pcb'
    norm.parent.mkdir(parents=True, exist_ok=True)
    src = Path(args.source)
    shutil.copyfile(src, norm)
    for other in companions(src):
        shutil.copyfile(other, norm.with_suffix(other.suffix))
    edit_board(norm, norm)                                   # normalize: load and save in this KiCad
    if not norm.with_suffix('.kicad_pro').is_file():
        raise SystemExit('pcbnew wrote no project file beside the normalized board')
    stated = None
    if args.fab_rules:                                       # only for a board that has no rules of its own
        if src.with_suffix('.kicad_pro').is_file():
            raise SystemExit('the source has its own project file; its rules are not replaced')
        stated = state_fab_rules(norm)
    variants = {'': norm}
    if args.no_pours:                                        # the preregistered zone fallback
        bare = root / 'norm-nopours' / norm.name
        bare.parent.mkdir(parents=True, exist_ok=True)
        remove_pours(norm, bare)
        copy_rules(norm, bare)
        variants = {'-nopours': bare}
    for suffix, base in variants.items():
        route_in = root / f'route-in{suffix}' / norm.name
        edit_board(base, route_in, strip=True, lock='all')
        copy_rules(norm, route_in)
        locked = lock_set(base)
        os.environ['KRT_BENCH_LOCKED'] = json.dumps(locked)
        fixed = root / f'place-in{suffix}' / 'locked' / norm.name
        edit_board(route_in, fixed, lock='rule')
        place_in = root / f'place-in{suffix}' / norm.name
        made = run([KRT_PY, '-X', 'utf8', KRT / '.claude/skills/plan-pcb-placement/scripts/make_unplaced.py',
                    fixed, place_in, '--keep-locked'], place_in.parent / 'make_unplaced.log', cwd=KRT)
        copy_rules(norm, place_in)
        write_json(place_in.parent / 'groups.json', groups_for(place_in) if place_in.is_file() else {})
        write_json(root / f'prep{suffix}.json', {
            'source': str(src), 'source_sha256': sha256(src), 'normalized_sha256': sha256(norm),
            'source_has_project_file': src.with_suffix('.kicad_pro').is_file(), 'stated_fab_rules': stated,
            'route_in': str(route_in), 'route_in_sha256': sha256(route_in), 'locked_refs': locked,
            'place_in': str(place_in), 'make_unplaced': made,
            'place_in_sha256': sha256(place_in) if place_in.is_file() else None})
    if not args.no_pours and copper(norm)['track_segments']:
        write_json(root / 'results/reference-as-designed.json',
                   {'board': name, 'arm': 'reference', 'tool': 'as-designed', 'score': score(norm, norm, root / 'score/reference')})
    print(root)


def reuse(args):
    """Copy the prepared inputs of an earlier run, so a rerun uses the very same boards.

    Both tool inputs (route-in, place-in) must still match the hashes their prep record
    names. The normalized board only supplies the rule files and is recorded as found: a
    later `prep --no-pours` of the same board saved it again."""
    old = Path(args.source).expanduser() / 'boards' / args.name
    root = RUNS / 'boards' / args.name
    for name in ('norm', 'route-in', 'place-in', 'prep.json'):
        if (root / name).exists():
            raise SystemExit(f'{root / name} exists; a rerun never overwrites prepared inputs')
        if (old / name).is_dir():
            shutil.copytree(old / name, root / name)
        else:
            root.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old / name, root / name)
    record = json.loads((root / 'prep.json').read_text())
    norm = root / 'norm' / f'{args.name}.kicad_pcb'
    checks = {root / 'route-in' / norm.name: record['route_in_sha256'],
              root / 'place-in' / norm.name: record['place_in_sha256']}
    wrong = [str(path) for path, expected in checks.items() if sha256(path) != expected]
    if wrong:
        raise SystemExit('prepared inputs differ from their prep record: ' + ', '.join(wrong))
    write_json(root / 'reused-from.json', {
        'run': str(old), 'sha256': {str(k.relative_to(root)): v for k, v in checks.items()},
        'normalized_sha256': sha256(norm), 'normalized_as_prep_record': sha256(norm) == record['normalized_sha256'],
        'rule_files_sha256': {p.name: sha256(p) for p in companions(norm)}})
    print(root)


def checkout_version():
    """The agent-ee commit this harness runs from, and whether the tree differs from it."""
    def git(*argv):
        return subprocess.run(['git', '-C', str(REPO), *argv], capture_output=True, text=True).stdout.strip()
    return {'commit': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain', '--untracked-files=no'))}


# ---- the two tools ---------------------------------------------------------------------

KRT_HELD = ['--escalation', 'board', '--no-fix-drc-settings']   # never below the board's declared floors


def krt_route(board_in, out_dir, rules_board, options=()):
    """Stock KRT, README default: route.py IN OUT. options adds a labelled variant's flags."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = out_dir / 'in' / Path(board_in).name
    staged.parent.mkdir(exist_ok=True)
    shutil.copyfile(board_in, staged)
    copy_rules(rules_board, staged)
    out = out_dir / 'routed.kicad_pcb'
    record = run([KRT_PY, '-X', 'utf8', 'py_router/route.py', staged, out, *options], out_dir / 'route.log',
                 cwd=KRT, timeout=BUDGET_S)
    record['output'] = str(out) if out.is_file() else None
    return record


def agent_ee_job(board_in, rules_board, groups, tag, out_dir, search=SEARCH):
    """agent-ee place run in the CI image (ae-ci-exec), where its router checkout lives."""
    stage = STAGE / tag
    if stage.exists():
        shutil.rmtree(stage)
    inputs = stage / 'inputs'
    inputs.mkdir(parents=True)
    board = inputs / Path(board_in).name
    shutil.copyfile(board_in, board)
    copy_rules(rules_board, board)
    write_json(inputs / 'groups.json', sheet_groups(groups))
    write_json(inputs / 'intent.json', intent_for(board))
    names = {'board': board.name, 'project': board.with_suffix('.kicad_pro').name,
             'intent': 'intent.json', 'groups': 'groups.json'}
    if board.with_suffix('.kicad_dru').is_file():
        names['rules'] = board.with_suffix('.kicad_dru').name
    config = {'schema': 'agent-ee.placement-job-config/1', 'budget_s': BUDGET_S,
              'inputs': {key: {'path': str(IN_IMAGE / tag / 'inputs' / value), 'sha256': sha256(inputs / value)}
                         for key, value in names.items()},
              'search': search, 'router': {'checkout': '/opt/kicad-route'}}
    write_json(stage / 'config.json', config)
    record = run(['ae-ci-exec', REPO, '--', 'python3', '-m', 'agent_ee.cli', 'place', 'run',
                  IN_IMAGE / tag / 'config.json', '--out', IN_IMAGE / tag / 'job'],
                 stage / 'run.log', cwd=REPO, timeout=BUDGET_S + 900)   # margin: image and admission
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(stage, out_dir, symlinks=True)
    shutil.rmtree(stage)
    record['log'] = str(out_dir / 'run.log')
    record['agent_ee'] = checkout_version()
    image = re.search(r'ae-ci-exec: image (sha256:[0-9a-f]+)', Path(record['log']).read_text(errors='replace'))
    record['ci_image'] = image.group(1) if image else None
    report_path = out_dir / 'job/job-report.json'
    if report_path.is_file():
        report = json.loads(report_path.read_text())
        final = report.get('final') or {}
        record.update(job_status=report.get('status'), job_validity=report.get('validity'),
                      job_elapsed_s=report.get('elapsed_s'), job_phases=report.get('phases'),
                      job_reason=report.get('reason') or report.get('refusal'),
                      job_eligibility=final.get('eligibility'), routed_candidate=report.get('routed_candidate'),
                      router_identity=report.get('router_identity'), job_preflight=report.get('preflight'))
        # How the search used its time and which placement the job offers (AGENTEE-637).
        engine = report.get('engine') or {}
        record.update(job_search=report.get('search'), job_placement=report.get('placement'),
                      job_groups=report.get('groups'),               # origin and anchor pull of each group
                      engine_budget=engine.get('budget'),
                      engine_seeds=[{k: row.get(k) for k in ('seed', 'status', 'limited_by', 'steps', 'steps_per_s',
                                                             'anneal_s', 'elapsed_s', 'feasible', 'best_cost',
                                                             'least_hard_count')}
                                    for row in engine.get('seeds') or []])
        if final.get('board'):
            local = out_dir / Path(final['board']).relative_to(IN_IMAGE / tag)
            record['output'] = str(local) if local.is_file() else None
        record.update(job_opens=final.get('opens'), job_errors=final.get('errors'),
                      job_capture={k: v for k, v in (json.loads((out_dir / 'job/capture.json').read_text())
                                                     if (out_dir / 'job/capture.json').is_file() else {}).items()
                                   if k in ('routing_only', 'placement_model', 'outline', 'pour_zones', 'keepout_rule_areas')})
        # The placement the job chose, also when its own route produced no board: the checked
        # incumbent, else the best diagnostic candidate (which the engine or native DRC rejected).
        for kind in ('incumbent', 'best-diagnostic'):
            pointer = out_dir / 'job' / f'{kind}.json'
            if pointer.is_file() and not record.get('placement'):
                placed = out_dir / Path(json.loads(pointer.read_text())['board']).relative_to(IN_IMAGE / tag)
                if placed.is_file():
                    record.update(placement=str(placed), placement_kind=kind)
    record.setdefault('output', None)
    record.setdefault('placement', None)
    logs = sorted(out_dir.glob('job/route-*/out/runs/*/*/*/work/krt.log'))
    if logs and not record['output']:                        # why the router failed, verbatim
        record['router_log_tail'] = tail(logs[-1], 12)
    return record


def direct_router(board_in, rules_board, tag, out_dir):
    """Secondary only: kicad-route run with the adapter's base flags, in the CI image."""
    stage = STAGE / tag
    if stage.exists():
        shutil.rmtree(stage)
    (stage / 'inputs').mkdir(parents=True)
    board = stage / 'inputs' / Path(board_in).name
    shutil.copyfile(board_in, board)
    copy_rules(rules_board, board)
    record = run(['ae-ci-exec', REPO, '--', 'python3', 'benchmarks/krt-matched/direct_route.py',
                  IN_IMAGE / tag / 'inputs' / board.name, IN_IMAGE / tag / 'job', BUDGET_S],
                 stage / 'run.log', cwd=REPO, timeout=BUDGET_S + 900)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(stage, out_dir, symlinks=True)
    shutil.rmtree(stage)
    record.update(log=str(out_dir / 'run.log'), output=None)
    report = out_dir / 'job/direct-route.json'
    if report.is_file():
        direct = json.loads(report.read_text())
        record.update(job_elapsed_s=direct['elapsed_s'], router_exit=direct['exit_code'], routing=direct.get('routing'),
                      job_status=f"router exit {direct['exit_code']}; validation {direct.get('validation')}")
        if direct.get('board'):
            record['output'] = str(out_dir / Path(direct['board']).relative_to(IN_IMAGE / tag))
    logs = sorted(out_dir.glob('job/out/runs/*/*/*/work/krt.log'))
    if logs and not record['output']:
        record['router_log_tail'] = tail(logs[-1], 12)
    return record


def result(root, board, arm, tool, record, rules_board, extra=None):
    key = f'{arm}-{tool}'
    data = {'board': board, 'arm': arm, 'tool': tool, 'budget_s': BUDGET_S, 'run': record, **(extra or {})}
    if record.get('output'):
        data['score'] = score(record['output'], rules_board, root / 'score' / key)
    else:
        data['score'] = None
        data['log_tail'] = tail(record['log'])
    write_json(root / 'results' / f'{key}.json', data)
    print(key, json.dumps({k: (data['score'] or {}).get(k) for k in ('unconnected', 'drc_errors', 'vias', 'track_length_mm')}),
          record.get('exit_code'), record.get('wall_s'), flush=True)
    return data


def route_arm(args):
    name, suffix = args.name, '-nopours' if args.no_pours else ''
    root = RUNS / 'boards' / name
    norm = root / 'norm' / f'{name}.kicad_pcb'
    route_in = root / f'route-in{suffix}' / norm.name
    arm = 'route' + suffix
    if 'krt' in args.tools:
        result(root, name, arm, 'krt', krt_route(route_in, root / 'runs' / f'{arm}-krt', norm), norm)
    if 'krt-held' in args.tools:                             # secondary: KRT told to keep the board's floors
        result(root, name, arm, 'krt-held', krt_route(route_in, root / 'runs' / f'{arm}-krt-held', norm, KRT_HELD), norm)
    if 'agent-ee' in args.tools:
        record = agent_ee_job(route_in, norm, groups_for(route_in), f'{name}-{arm}', root / 'runs' / f'{arm}-agent-ee')
        done = result(root, name, arm, 'agent-ee', record, norm)
        if not done['score'] and record.get('job_status') == 'refused':
            direct = direct_router(route_in, norm, f'{name}-{arm}-direct', root / 'runs' / f'{arm}-kicad-route-direct')
            result(root, name, arm, 'kicad-route-direct', direct, norm)


def krt_place(place_in, groups_path, out_dir):
    """KRT's documented chain: check_floorplan --emit-intent, then place_simple default seeds."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    steps = [run([KRT_PY, '-X', 'utf8', 'py_tools/check_floorplan.py', place_in, '--emit-intent', out_dir / 'intent.json'],
                 out_dir / 'emit-intent.log', cwd=KRT, timeout=BUDGET_S)]
    if (out_dir / 'intent.json').is_file():
        left = max(1, BUDGET_S - (time.monotonic() - start))
        steps.append(run([KRT_PY, '-X', 'utf8', 'py_placer/place_simple.py', place_in, out_dir / 's{seed}.kicad_pcb',
                          '--groups', groups_path, '--intent', out_dir / 'intent.json', '--jobs', '1',
                          '--json', out_dir / 's{seed}.json'], out_dir / 'place.log', cwd=KRT, timeout=left))
    record = dict(steps[-1], steps=steps, wall_s=round(time.monotonic() - start, 1), output=None, kept_seed=None)
    judge = out_dir / 'judge.json'
    order = []
    if judge.is_file():
        named = json.loads(judge.read_text())
        record['judge'] = {key: named.get(key) for key in ('judge', 'illegal', 'illegal_why', 'refused')}
        order = [int(seed) for seed in named.get('judge', [])]
        record['kept_by'] = 'first seed named in judge.json'
    if not order:                                            # fallback: lowest reported cost
        costs = []
        for path in out_dir.glob('s*.json'):
            try:
                costs.append((json.loads(path.read_text())['final']['cost'], int(path.stem[1:])))
            except (KeyError, ValueError, TypeError):
                pass
        order = [seed for _, seed in sorted(costs)]
        record['kept_by'] = 'lowest final.cost (no usable judge.json)'
    for seed in order:
        if (out_dir / f's{seed}.kicad_pcb').is_file():
            record.update(output=str(out_dir / f's{seed}.kicad_pcb'), kept_seed=seed)
            break
    return record


def place_arm(args):
    name, suffix = args.name, '-nopours' if args.no_pours else ''
    root = RUNS / 'boards' / name
    norm = root / 'norm' / f'{name}.kicad_pcb'
    place_in = root / f'place-in{suffix}' / norm.name
    groups_path = place_in.parent / 'groups.json'
    arm = ('place' + suffix + ('-searchB' if args.search_b else '') + ('-capped' if args.capped else '')
           + (f'-seeds{args.seed_offset}' if args.seed_offset else ''))
    search = SEARCH_B if args.search_b else SEARCH_CAPPED if args.capped else SEARCH
    if args.seed_offset:                                     # spread: the same search on other seeds
        search = dict(search, seeds=[dict(row, seed=row['seed'] + args.seed_offset) for row in search['seeds']])
    placed = {}
    if 'krt' in args.tools and arm == 'place' + suffix:      # the variants change only agent-ee's side
        record = krt_place(place_in, groups_path, root / 'runs' / f'{arm}-krt')
        write_json(root / 'results' / f'{arm}-krt-placement.json', {'board': name, 'run': record})
        placed['krt'] = record
    if 'agent-ee' in args.tools:
        record = agent_ee_job(place_in, norm, json.loads(groups_path.read_text()), f'{name}-{arm}',
                              root / 'runs' / f'{arm}-agent-ee', search)
        result(root, name, arm, 'agent-ee-own-route', record, norm)     # agent-ee's own routed result
        placed['agent-ee'] = record
    for tool, record in placed.items():                      # same stock KRT router on both placements
        key = f'{arm}-{tool}-then-krt-route'
        # agent-ee's routed board when it has one (as in the first run), else the placement it chose.
        source = record.get('output') or record.get('placement')
        if not source:
            write_json(root / 'results' / f'{key}.json',
                       {'board': name, 'arm': arm, 'tool': f'{tool}-then-krt-route', 'score': None,
                        'placement': record, 'log_tail': tail(record['log'])})
            print(key, 'no placement', flush=True)
            continue
        bare = root / 'runs' / key / 'placement' / norm.name
        edit_board(source, bare, strip=True)
        routed = krt_route(bare, root / 'runs' / key, norm)
        result(root, name, arm, f'{tool}-then-krt-route', routed, norm,
               {'placement': {k: record.get(k) for k in ('wall_s', 'exit_code', 'timed_out', 'kept_seed', 'job_status',
                                                         'job_elapsed_s', 'job_validity', 'placement_kind')},
                'placement_only_score': score(bare, norm, root / 'score' / f'{key}-placement')})


def krt_spread(args):
    """Route every seed KRT's placer wrote (not only the kept one) with stock KRT: the seed spread."""
    name, suffix = args.name, '-nopours' if args.no_pours else ''
    root = RUNS / 'boards' / name
    norm = root / 'norm' / f'{name}.kicad_pcb'
    for placed in sorted((root / 'runs' / f'place{suffix}-krt').glob('s*.kicad_pcb'), key=lambda p: int(p.stem[1:])):
        key = f'place{suffix}-krt-seed{placed.stem[1:]}-then-krt-route'
        bare = root / 'runs' / key / 'placement' / norm.name
        edit_board(placed, bare, strip=True)
        result(root, name, f'place{suffix}-spread', f'krt-seed{placed.stem[1:]}-then-krt-route',
               krt_route(bare, root / 'runs' / key, norm), norm)


def baseline(args):
    """Score the unrouted routing-arm input, so pre-existing findings can be told from new ones."""
    root = RUNS / 'boards' / args.name
    norm = root / 'norm' / f'{args.name}.kicad_pcb'
    for suffix in ('', '-nopours'):
        board = root / f'route-in{suffix}' / norm.name
        if board.is_file():
            write_json(root / 'results' / f'baseline-route-in{suffix}.json',
                       {'board': args.name, 'arm': 'route' + suffix, 'tool': 'unrouted input',
                        'score': score(board, norm, root / 'score' / f'baseline-route-in{suffix}')})


def rescore(_args):
    """Add the score under the project file the tool itself wrote beside its output, where one exists.

    Stock KRT rewrites project rules (for example minimum track width) to match what it
    routed; the primary score always uses the board's own rules."""
    for path in sorted(RUNS.glob('boards/*/results/*.json')):
        data = json.loads(path.read_text())
        output = (data.get('run') or {}).get('output')
        if not output or 'score_tool_rules' in data or not Path(output).with_suffix('.kicad_pro').is_file():
            continue
        if 'krt' not in data.get('tool', '') or data['tool'].startswith('agent-ee-own'):
            continue
        data['score_tool_rules'] = score(output, output, path.parents[1] / 'score' / (path.stem + '-tool-rules'))
        write_json(path, data)


def summary(_args):
    rescore(_args)
    listed = json.loads((Path(__file__).parent / 'boards.json').read_text())
    chosen, stated = set(listed['boards']), set(listed.get('fab_rules_boards', {}))
    rows, passing, passing_stated = [], {}, {}
    for path in sorted(RUNS.glob('boards/*/results/*.json')):
        data = json.loads(path.read_text())
        if 'arm' not in data or data.get('tool') == 'unrouted input':
            continue
        root = path.parents[1]
        s, r = data.get('score') or {}, data.get('run') or {}
        suffix = '-nopours' if 'nopours' in data['arm'] else ''
        v = verdict(s, input_report(root, data['board'], suffix))
        counted = passing if data['board'] in chosen else passing_stated if data['board'] in stated else None
        if counted is not None:                     # the debug board is listed but never counted
            cell = (data['arm'], data['tool'])
            counted.setdefault(cell, [[], 0])[1] += 1
            if v['passes']:
                counted[cell][0].append(data['board'])
        rows.append([data['board'], data['arm'], data['tool'], 'yes' if v['passes'] else 'no', s.get('unconnected'),
                     v.get('introduced_errors'), json.dumps(v.get('introduced_by_type')) if s else None,
                     s.get('drc_errors'), json.dumps(s.get('drc_errors_by_type')) if s else None, s.get('drc_warnings'),
                     s.get('vias'), s.get('track_length_mm'), (data.get('score_tool_rules') or {}).get('drc_errors'),
                     r.get('job_elapsed_s') or r.get('wall_s'), r.get('exit_code'),
                     r.get('job_status') or ('timeout' if r.get('timed_out') else ''), rules_origin(root)])
    head = ['board', 'arm', 'tool', 'passes', 'unconnected', 'introduced errors', 'introduced by type', 'errors',
            'errors by type', 'warnings', 'vias', 'length mm', 'errors (tool-written rules)', 'seconds', 'exit',
            'status', 'rules']
    table = lambda head_, rows_: ['| ' + ' | '.join(head_) + ' |', '|' + ' --- |' * len(head_),
                                  *('| ' + ' | '.join('' if v is None else str(v) for v in row) + ' |' for row in rows_)]
    print('\n'.join(table(head, rows)))
    count_rows = lambda counted: [[arm, tool, len(names), total, ', '.join(names)]
                                  for (arm, tool), (names, total) in sorted(counted.items())]
    board_rows, stated_rows = count_rows(passing), count_rows(passing_stated)
    board_head = ['arm', 'tool', 'boards passing', 'of', 'which']
    print('\nPasses: 0 open connections and no DRC error beyond the unrouted input\'s, against the board\'s own rules. '
          'Boards of the chosen set only.\n')
    print('\n'.join(table(board_head, board_rows)))
    if stated_rows:
        print('\nThe same for the boards prepared with stated fab rules (their source has no project file).\n')
        print('\n'.join(table(board_head, stated_rows)))
    write_json(RUNS / 'summary.json', {'cells': [dict(zip(head, row)) for row in rows],
                                       'passing': [dict(zip(board_head, row)) for row in board_rows],
                                       'passing_stated_fab_rules': [dict(zip(board_head, row)) for row in stated_rows]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name, func in (('prep', prep), ('reuse', reuse), ('baseline', baseline), ('route', route_arm),
                       ('place', place_arm), ('krt-spread', krt_spread), ('summary', summary)):
        p = sub.add_parser(name)
        p.set_defaults(func=func)
        if name != 'summary':
            p.add_argument('name')
            p.add_argument('--no-pours', action='store_true', help='the zone-fallback variant')
        if name == 'prep':
            p.add_argument('source')
            p.add_argument('--fab-rules', action='store_true',
                           help='for a source without a project file: state the rules of fab-rules.json')
        if name == 'reuse':
            p.add_argument('source', help='the earlier run directory whose prepared inputs to copy')
        if name == 'place':
            p.add_argument('--search-b', action='store_true', help="agent-ee only: the secondary search setting")
            p.add_argument('--capped', action='store_true', help='agent-ee only: the earlier default, 100,000 steps per seed')
            p.add_argument('--seed-offset', type=int, default=0, help='agent-ee only: add to every seed number')
        if name in ('route', 'place'):
            p.add_argument('--tools', nargs='+', default=['krt', 'agent-ee'],
                           choices=['krt', 'agent-ee'] + ['krt-held'] * (name == 'route'))
    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
