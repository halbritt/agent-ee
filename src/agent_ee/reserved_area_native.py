"""Native witnesses for advisory single-owner reserve alternatives."""
import hashlib
from pathlib import Path
import re
import subprocess

from .storage import digest, write_json
from .reserved_area import fabrication_outlines


class NativeAlternatives:
    """Validate on retained scratch boards under the proposal's captured rules.

    No artwork repair or packing search runs here. Every other footprint is protected;
    an unavailable tool produces unknown evidence, never a legal alternative.
    """

    def __init__(self, source, root, facts, policy, plan):
        self.source = Path(source)
        self.root = Path(root)
        self.facts = facts
        self.policy = policy
        self.plan = plan
        self.cache = {}
        self.baseline = None
        self.board_bytes = self.source.read_bytes()
        self.fabrication = fabrication_outlines(self.source)
        self.rules = {suffix: self.source.with_suffix(suffix).read_bytes()
                      if self.source.with_suffix(suffix).is_file() else None
                      for suffix in ('.kicad_pro', '.kicad_dru')}

    def __call__(self, ref, position, angle):
        key = (ref, tuple(position), angle)
        if key in self.cache:
            return self.cache[key]
        directory = self.root / f'pose-{len(self.cache):04d}'
        directory.mkdir(parents=True)
        output = directory / 'candidate.kicad_pcb'
        evidence = {'status': 'unknown', 'source_sha256': hashlib.sha256(self.board_bytes).hexdigest(),
                    'rules': {k: hashlib.sha256(v).hexdigest() if v is not None else None
                              for k, v in self.rules.items()},
                    'directory': str(directory.relative_to(self.root.parent))}
        try:
            self._validate(ref, position, angle, output, evidence)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            evidence.update(status='unknown', reason=str(exc))
        write_json(directory / 'validation.json', evidence)
        # Native DRC embeds its execution date. Keep raw-file digests in the
        # witness, not the deterministic advisory cost report.
        summary = {k: v for k, v in evidence.items() if k not in
                   ('drc_sha256', 'baseline_drc_sha256')}
        summary['qualification'] = {'validated': 'legal under captured rules',
                                    'unknown': 'hypothetical, unverified',
                                    'rejected': 'not legal'}[summary['status']]
        summary['witness'] = str((directory / 'validation.json').relative_to(self.root.parent))
        if summary['status'] == 'unknown':
            summary['reason'] = 'native validation unavailable; see witness'
        self.cache[key] = summary
        return summary

    def _validate(self, ref, position, angle, output, evidence):
        import pcbnew
        from .placer_runner import _apply_plan, _native_drc, _pad_inventory, DRC_COURTYARDS
        from .placement_adapter import write_scope
        from .placement_probe import extract
        from .reserved_area import _candidate_cost
        from .design_rule_provenance import (classify_finding, finding_sources, numeric_finding,
                                            NATIVE_CONSTRAINT_TYPES)

        if (self.source.read_bytes() != self.board_bytes or
                self.facts['board_sha256'] != evidence['source_sha256']):
            raise ValueError('reserve source changed or differs from captured facts')
        board = pcbnew.LoadBoard(str(self.source))
        pads = _pad_inventory(board)
        placements = [{'uuid': fp['uuid'], 'ref': fp['ref'], 'fixed': fp['ref'] != ref,
                       'position_nm': list(position) if fp['ref'] == ref else fp['position_nm'],
                       'rotation_deg': angle if fp['ref'] == ref else fp['rotation_deg']}
                      for fp in self.facts['footprints']]
        _apply_plan(board, {'placements': placements}, output)
        for suffix, payload in self.rules.items():
            if payload is None:
                output.with_suffix(suffix).unlink(missing_ok=True)
            else:
                if suffix == '.kicad_dru' and re.search(rb'\(\s*severity\s+"?ignore\b', payload, re.I):
                    raise ValueError('native reserve DRC custom rule explicitly ignored')
                output.with_suffix(suffix).write_bytes(payload)
        saved_board = pcbnew.LoadBoard(str(output))
        if _pad_inventory(saved_board) != pads:
            raise ValueError('native reserve pose changed pad geometry')
        protected = [p['uuid'] for p in placements if p['fixed']]
        scope = write_scope(self.board_bytes, output.read_bytes(), protected)
        write_json(output.parent / 'placement-write-scope.json', scope)
        if scope['status'] != 'allowed':
            evidence.update(status='rejected', reason='write-scope preservation refused', write_scope=scope)
            return
        saved = extract(output)
        actual = next(fp for fp in saved['footprints'] if fp['ref'] == ref)
        if actual['position_nm'] != list(position) or (actual['rotation_deg'] - angle) % 360:
            raise ValueError('native reserve saved pose differs from requested pose')
        # Regrade the saved geometry, including the same hard group/block rules.
        measured = _candidate_cost(saved, self.policy, fabrication_outlines(output), self.plan,
                                   actual, (position, angle))
        if measured is None:
            evidence.update(status='rejected', reason='saved pose failed placement predicates')
            return
        original = next(fp for fp in self.facts['footprints'] if fp['ref'] == ref)
        expected = _candidate_cost(self.facts, self.policy, self.fabrication, self.plan,
                                   original, (position, angle))
        if measured != expected:
            raise ValueError('saved reserve measurement differs from geometric prediction')
        if self.baseline is None:
            baseline_dir = self.root / 'baseline'
            baseline_dir.mkdir()
            baseline = baseline_dir / 'candidate.kicad_pcb'
            baseline.write_bytes(self.board_bytes)
            for suffix, payload in self.rules.items():
                if payload is not None:
                    baseline.with_suffix(suffix).write_bytes(payload)
            self.baseline = _native_drc(baseline, baseline_dir)
        report = _native_drc(output, output.parent)
        # These checks concern routing or schematic library metadata, not poses.
        unrelated = {'track_not_centered_on_via', 'tuning_profile_track_geometries',
                     'footprint_filters_mismatch', 'footprint_type_mismatch'}
        if any(c['key'] not in unrelated for r in (report, self.baseline) for c in r['ignored_checks']):
            raise ValueError('native reserve DRC has ignored placement checks; legality unverified')
        evidence['baseline_drc_sha256'] = digest(self.root / 'baseline' / 'native-drc.json')
        sources = finding_sources(output, report['violations'])
        for finding in report['violations']:
            classify_finding(finding, sources)
        # Numeric template defaults remain advisory. Other native findings cannot
        # be waived merely because there is no numeric source for their category.
        blockers = [v for v in report['violations'] if
                    v['type'] in DRC_COURTYARDS or
                    not (v['type'] in NATIVE_CONSTRAINT_TYPES and numeric_finding(v) and
                         v['constraint_provenance']['enforcement'] == 'advisory')]
        evidence.update(status='rejected' if blockers else 'validated',
                        reason='native DRC findings' if blockers else 'native DRC and preservation passed',
                        board_sha256=digest(output), drc_sha256=digest(output.parent / 'native-drc.json'),
                        write_scope_sha256=digest(output.parent / 'placement-write-scope.json'),
                        blocking_types=sorted({v['type'] for v in blockers}),
                        advisory_types=sorted({v['type'] for v in report['violations'] if v not in blockers}))
        if self.source.read_bytes() != self.board_bytes or any(
                (self.source.with_suffix(suffix).read_bytes() if self.source.with_suffix(suffix).is_file()
                 else None) != payload for suffix, payload in self.rules.items()):
            raise ValueError('captured reserve board or rules changed during validation')
