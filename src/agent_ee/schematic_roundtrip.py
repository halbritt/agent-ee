"""Offline diagnostic source -> IR -> native schematic qualification.

This report has no coordinator seal or campaign acceptance authority. Inputs
are borrowed bytes; retained snapshots and all tool scratch belong to a new
caller-selected private directory. Pure extraction remains execution-free.
"""
import argparse
import hashlib
import json
import math
import os
import shutil
import stat
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from . import native_run
from .candidate import safe_relative
from .connectivity_ir import _json_bytes, _read_json

from .native_netlist import read_native_inventory
from .netlist_parity import compare, resolved_inventory, validate_policy, MAX_BYTES, MAX_LIBRARY_BYTES
from .schematic_decompile import decompile
from .schematic_emitter import emit_schematic


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path, limit):
    # Native tools own their scratch; never follow a planted output link.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('expected regular file: ' + str(path))
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('byte limit exceeded: ' + str(path))
    return raw


def _write(root, name, raw):
    path = root / safe_relative(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def _policy(predicate, anchors=None):
    extra = dict(anchors=anchors, instance_components=sorted(anchors)) if predicate == 'instance' else {}
    return validate_policy(dict(source=dict(kind='intent', path='intent.json'), netlist_check='native',
                                predicate=predicate, multiset='exactly', **extra))


def qualify(expected, source, emitted, source_anchors, emitted_anchors):
    """Use shared41 policies for complete topology and independent instance identity.

    Inventories are borrowed, and anchors are immutable logical-ID mappings
    derived from source occurrence IDs and the emitter UUID contract respectively.
    """
    reports = {}
    for label, actual, anchors in (('source', source, source_anchors), ('emitted', emitted, emitted_anchors)):
        for predicate in ('components', 'topology', 'instance'):
            reports[label + '_' + predicate] = compare(expected, actual, _policy(predicate, anchors))
    reports['native_topology'] = compare(source, emitted, _policy('topology'))
    return reports


def roundtrip(source, libraries, captures, layout, out, *, timeout=120, cancellation=None):
    """Retain source-bound diagnostic artifacts and real networkless execution.

    Unsupported extraction/emission or failed native execution never carries a
    fabricated parity score. Cancellation propagates through the shared executor;
    the initial unknown report and process receipts remain on disk.
    """
    if not isinstance(source, bytes) or len(source) > 8 * 1024 * 1024:
        raise ValueError('source must be bytes at most 8 MiB')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ValueError('timeout must be finite and in (0, 600] seconds')
    out = Path(os.path.abspath(out))
    if any(p.is_symlink() for p in (out, *out.parents)):
        raise ValueError('symlink output path refused')
    out.mkdir(mode=0o700, exist_ok=False)
    report = dict(schema='agent-ee.schematic-roundtrip/1', authority='diagnostic',
                  execution='not_run', outcome='unknown', coverage='missing',
                  source_sha256=_hash(source), libraries=libraries, layout_sha256=_hash(_json_bytes(layout)),
                  artifacts={}, executions={}, comparisons={}, identity_mapping={}, findings=[])

    def finish():
        # The coordinator-owned report is outside all tool-writable directories.
        from .storage import write_json
        report['execution_receipts'] = {
            path: _hash(_read(out / path, MAX_BYTES))
            for path in report['executions'].values() if (out / path).is_file()}
        write_json(out / 'report.json', report)
        return report

    def run(inputs, work, argv):
        report['executions'][work.name] = str((work / 'execution.json').relative_to(out))
        try:
            record = native_run.run(
                native_run.Intent('schematic_roundtrip', argv, timeout=timeout),
                inputs=inputs,
                work=work,
                cancel=cancellation,
            )
            if cancellation is not None and cancellation.cancelled.is_set():
                raise KeyboardInterrupt
            return record
        except KeyboardInterrupt:
            report.update(execution='interrupted', outcome='unknown', coverage='missing')
            finish()
            raise

    finish()
    _write(out / 'source', 'source.kicad_sch', source)
    extracted = decompile(source, libraries, captures)
    for name, value in extracted.items():
        if value is not None:
            _write(out, 'extracted/' + name + '.json', _json_bytes(value))
    if extracted['report']['execution'] != 'ok':
        report.update(execution='unsupported', findings=extracted['report']['findings'])
        return finish()
    _write(out, 'layout.json', _json_bytes(layout))
    _write(out, 'libraries.json', _json_bytes(libraries))
    # Library paths are validated by extraction/resolution before use. Keep the
    # exact bytes alongside each native project, read-only in its sandbox.
    for label in ('source', 'emitted'):
        candidate = out / label
        candidate.mkdir(exist_ok=True)
        for library in libraries:
            _write(candidate, library['path'], captures[library['path']])
    emission, files = emit_schematic(extracted['intent'], captures, layout,
                                     expected_resolved=extracted['resolved'])
    _write(out, 'emission.json', _json_bytes(emission))
    if emission['execution'] != 'ok':
        report.update(execution=emission['execution'], findings=emission['findings'])
        return finish()
    for name, raw in files.items():
        _write(out / 'emitted', name, raw)
    for label in ('source', 'emitted'):
        for path in sorted((out / label).rglob('*')):
            if path.is_file():
                report['artifacts'][str(path.relative_to(out))] = _hash(_read(path, MAX_LIBRARY_BYTES))
    for name in ('layout.json', 'libraries.json', 'emission.json', 'extracted/report.json',
                 'extracted/intent.json', 'extracted/resolved.json'):
        report['artifacts'][name] = _hash(_read(out / name, MAX_LIBRARY_BYTES))
    expected = resolved_inventory(extracted['resolved'])
    source_anchors = extracted['report']['source_anchors']
    ns = uuid.UUID(layout['namespace'])
    emitted_anchors = {cid: dict(sheet='/', uuid=str(uuid.uuid5(ns,
        json.dumps(['symbol', cid, 1], separators=(',', ':'))))) for cid in expected['components']}
    report['identity_mapping'] = {cid: dict(source=source_anchors[cid], emitted=emitted_anchors[cid])
                                  for cid in sorted(expected['components'])}
    graphs = {}
    try:
        executable = shutil.which('kicad-cli', path='/usr/local/bin:/usr/bin:/bin')
        report['tool'] = dict(path=executable, sha256=None, version=None)
        if executable is not None:
            from .storage import digest
            report['tool']['sha256'] = digest(executable)
        version_work = out / 'version'
        version = run(out / 'source', version_work, ['kicad-cli', '--version'])
        if version['execution'] != 'ok' or version['exit_code'] != 0:
            report.update(execution='failed', findings=[dict(code='native_version', execution=version['execution'])])
            return finish()
        report['tool']['version'] = _read(version_work / 'stdout.log', 65536).decode('utf-8').strip()
        for label, filename in (('source', 'source.kicad_sch'), ('emitted', layout['project'] + '.kicad_sch')):
            work = out / (label + '-export')
            record = run(out / label, work, ['kicad-cli', 'sch', 'export', 'netlist',
                '--format', 'kicadxml', '-o', '/work/design.xml', '/candidate/' + filename])
            if record['execution'] != 'ok' or record['exit_code'] != 0 or record.get('displaced_records'):
                report.update(execution='failed', findings=[dict(code='native_export', stage=label,
                                                               execution=record['execution'], exit_code=record['exit_code'])])
                return finish()
            raw = _read(work / 'design.xml', MAX_BYTES)
            report['artifacts'][label + '-export/design.xml'] = _hash(raw)
            graphs[label] = read_native_inventory(work / 'design.xml')
        erc_work = out / 'emitted-erc'
        erc_execution = run(out / 'emitted', erc_work,
            ['kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all', '-o', '/work/erc.json',
             '/candidate/' + layout['project'] + '.kicad_sch'])
        if erc_execution['execution'] != 'ok' or erc_execution['exit_code'] != 0:
            report.update(execution='failed', findings=[dict(code='native_erc', execution=erc_execution['execution'])])
            return finish()
        erc_raw = _read(erc_work / 'erc.json', MAX_BYTES)
        report['artifacts']['emitted-erc/erc.json'] = _hash(erc_raw)
        erc = _read_json(erc_raw)
        report['erc'] = dict(authority='diagnostic', violations=[v for sheet in erc['sheets'] for v in sheet['violations']])
        if executable is not None and digest(executable) != report['tool']['sha256']:
            raise ValueError('native executable changed during roundtrip')
        report['comparisons'] = qualify(expected, graphs['source'], graphs['emitted'], source_anchors, emitted_anchors)
    except (OSError, ValueError, ET.ParseError) as exc:
        report.update(execution='failed', findings=[dict(code='native_evidence_invalid', message=str(exc))])
        return finish()
    complete = all(r['execution'] == 'ok' for r in report['comparisons'].values())
    passed = complete and all(r['outcome'] == 'pass' for r in report['comparisons'].values())
    report.update(execution='ok' if complete else 'unsupported', outcome='pass' if passed else ('defect' if complete else 'unknown'),
                  coverage='complete' if complete else 'missing',
                  inventory=dict(components=len(expected['components']), pins=sum(len(c['pins']) for c in expected['components'].values()),
                                 nets=len(expected['nets'])))
    return finish()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('schematic', type=Path)
    parser.add_argument('--libraries', required=True, type=Path)
    parser.add_argument('--captures', required=True, type=Path)
    parser.add_argument('--layout', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    libraries = _read_json(_read(args.libraries, 1024 * 1024))
    # Validate paths/schema before opening any capture, including ancestor links.
    from .connectivity_ir import _validate_flat_intent, INTENT_SCHEMA
    _validate_flat_intent(dict(schema=INTENT_SCHEMA, libraries=libraries, components=[], nets=[], no_connects=[]))
    captures, total = {}, 0
    for library in libraries:
        path = args.captures / safe_relative(library['path'])
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('symlink capture refused')
        raw = _read(path, MAX_LIBRARY_BYTES - total)
        total += len(raw)
        captures[library['path']] = raw
    report = roundtrip(_read(args.schematic, 8 * 1024 * 1024), libraries, captures,
                       _read_json(_read(args.layout, 1024 * 1024)), args.out)
    return 0 if report['outcome'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
