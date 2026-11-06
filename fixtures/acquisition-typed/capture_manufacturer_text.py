#!/usr/bin/env python3
"""Capture acquisition primary text and retain the CPU tools used by each run."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(document):
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')


def run(argv, root):
    completed = subprocess.run(argv, cwd=root, capture_output=True, timeout=120,
                               env={**os.environ, 'LC_ALL': 'C.UTF-8', 'TZ': 'UTC'})
    return completed, {'argv': argv, 'exit_code': completed.returncode,
                       'stderr': completed.stderr.decode('utf-8')}


def verify_sources(root, declaration):
    inventory = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'captured input contains a link: {path}')
        if path.is_file():
            inventory[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    if digest(json_bytes(inventory)) != declaration['base_inventory_sha256']:
        raise ValueError('base input inventory differs from the accepted 198-file capture')
    for document in declaration['documents']:
        source = document['source']
        manifest_raw = (root / source['manifest_path']).read_bytes()
        if digest(manifest_raw) != source['manifest_sha256']:
            raise ValueError('source manifest changed: ' + document['id'])
        part = next(p for p in json.loads(manifest_raw)['parts'] if p['id'] == source['part_id'])
        if (Path(source['path']) != Path(source['manifest_path']).parent / part['path']
                or part['sha256'] != source['sha256']
                or digest((root / source['path']).read_bytes()) != source['sha256']):
            raise ValueError('source PDF differs from its capture manifest: ' + document['id'])


def observe_tools(root, selectors):
    if set(selectors) != {'pdfinfo', 'pdftotext'}:
        raise ValueError('capture requires pdfinfo and pdftotext selectors')
    tools = {}
    for name, selector in selectors.items():
        if (not isinstance(selector, dict) or set(selector) != {'path'}
                or not isinstance(selector['path'], str) or not Path(selector['path']).is_absolute()):
            raise ValueError('tool selector requires an absolute path: ' + name)
        path = selector['path']
        sha256 = digest(Path(path).read_bytes())
        completed, _ = run([path, '-v'], root)
        lines = (completed.stdout + completed.stderr).decode('utf-8').splitlines()
        prefix = name + ' version '
        if (completed.returncode or not lines or not lines[0].startswith(prefix)
                or not lines[0][len(prefix):].strip()):
            raise ValueError('extraction binary version unavailable: ' + name)
        if digest(Path(path).read_bytes()) != sha256:
            raise ValueError('extraction binary changed during version probe: ' + name)
        tools[name] = {'path': path, 'sha256': sha256, 'version': lines[0]}
    return tools


def verify_tools(root, tools):
    for name, tool in tools.items():
        if digest(Path(tool['path']).read_bytes()) != tool['sha256']:
            raise ValueError('extraction binary changed: ' + name)
    observed = observe_tools(root, {name: {'path': tool['path']} for name, tool in tools.items()})
    for name, tool in tools.items():
        if observed[name] != tool:
            raise ValueError('extraction binary changed: ' + name)


def extract_document(root, out, document, tools, record):
    source = document['source']
    completed, record['pdfinfo'] = run([tools['pdfinfo']['path'], source['path']], root)
    record['pdfinfo']['stdout'] = completed.stdout.decode('utf-8')
    if completed.returncode:
        raise ValueError('pdfinfo failed: ' + document['id'])
    pages = int(next(line.split(':', 1)[1] for line in record['pdfinfo']['stdout'].splitlines()
                     if line.startswith('Pages:')))
    if pages != document['physical_pages']:
        raise ValueError('physical page count changed: ' + document['id'])
    argv = [tools['pdftotext']['path'], '-f', '1', '-l', str(pages), '-layout',
            '-enc', 'UTF-8', '-eol', 'unix', source['path'], '-']
    completed, record['pdftotext'] = run(argv, root)
    raw = completed.stdout
    # Keep even failed tool output; only a complete manifest can enter the overlay.
    target = out / (document['id'] + '.txt')
    target.write_bytes(raw)
    record['text'] = {'path': target.name, 'sha256': digest(raw), 'bytes': len(raw)}
    if completed.returncode:
        raise ValueError('pdftotext failed: ' + document['id'])
    decoded = raw.decode('utf-8')  # strict; no replacement, reflow, stripping or OCR
    if not decoded.strip() or target.read_text(encoding='utf-8').encode('utf-8') != raw:
        raise ValueError('empty or newline-normalized text: ' + document['id'])
    chunks = raw.split(b'\f')
    if len(chunks) != pages + 1 or chunks[-1] != b'':
        raise ValueError('extractor did not emit exactly one trailing form feed per physical page')
    offset = 0
    record['pages'] = []
    for number, content in enumerate(chunks[:-1], 1):
        end = offset + len(content) + 1
        record['pages'].append({'physical_page': number, 'byte_start': offset,
                                'content_byte_end': end - 1, 'byte_end': end,
                                'sha256': digest(raw[offset:end]),
                                'text_nonempty': bool(content.decode('utf-8').strip())})
        offset = end
    record['empty_text_pages'] = [p['physical_page'] for p in record['pages'] if not p['text_nonempty']]
    record['status'] = 'captured'


def capture(root, out):
    declaration_path = Path(__file__).with_name('manufacturer-text-sources.json')
    declaration_raw = declaration_path.read_bytes()
    declaration = json.loads(declaration_raw)
    root, out = root.resolve(), out.resolve()
    if out == root or root in out.parents or out in root.parents:
        raise ValueError('output must be disjoint from the original capture')
    verify_sources(root, declaration)
    tools = observe_tools(root, declaration['tools'])
    out.mkdir()  # exclusive ownership; existing successful or failed captures are never overwritten
    manifest = {'schema': 'agent-ee.manufacturer-text-capture/1', 'status': 'incomplete',
                'source_descriptor_sha256': digest(declaration_raw),
                'procedure_sha256': digest(Path(__file__).read_bytes()),
                'tools': tools, 'environment': {'LC_ALL': 'C.UTF-8', 'TZ': 'UTC'},
                'page_offsets': 'UTF-8 bytes, half-open; byte_end includes one final form feed; content_byte_end excludes it',
                'limitations': declaration['limitations'], 'documents': [], 'failures': []}
    try:
        for document in declaration['documents']:
            record = {'id': document['id'], 'source': document['source'], 'status': 'incomplete'}
            manifest['documents'].append(record)
            verify_tools(root, tools)
            extract_document(root, out, document, tools, record)
            verify_tools(root, tools)
        verify_sources(root, declaration)
        manifest['status'] = 'complete'
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        manifest['status'] = 'failed'
        manifest['failures'].append({'type': type(exc).__name__, 'message': str(exc)})
        raise
    finally:
        (out / 'manifest.json').write_bytes(json_bytes(manifest))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    captured = capture(args.base, args.out)
    print(json.dumps({'status': captured['status'], 'documents': len(captured['documents']),
                      'manifest_sha256': digest((args.out / 'manifest.json').read_bytes())}))
