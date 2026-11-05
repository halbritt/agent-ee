"""Download-only part discovery and offline KiCad inventory for design authors."""

import argparse
import base64
import datetime
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from . import jlc_stock_capture as stock
from .storage import write_json


def search_parts(keyword, out):
    """One bounded catalog page, retaining the exchange before interpreting it."""
    if not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= 120:
        raise ValueError('keyword: supply 1 to 120 characters')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    # A shared lock also spaces requests from successive helper invocations.
    lock_path = out.parent / '.jlc-search.lock'
    with lock_path.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        lock.seek(0)
        previous = lock.read().strip()
        delay = stock.DEFAULT_INTERVAL - (time.time() - float(previous)) if previous else 0
        if delay > 0:
            time.sleep(delay)
        exchange = stock._query(stock.DEFAULT_ENDPOINT, keyword.strip(), 30, stock.MAX_BYTES)
        lock.seek(0)
        lock.truncate()
        lock.write(str(time.time()))
        lock.flush()
    write_json(out / 'exchange.json', exchange)
    raw = base64.b64decode(exchange['body']) if exchange['body'] is not None else b''
    (out / 'response.json').write_bytes(raw)
    write_json(out / 'request.json', dict(endpoint=stock.DEFAULT_ENDPOINT, method='POST',
        body=json.loads(stock.request_body(keyword.strip())),
        retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        response_sha256=hashlib.sha256(raw).hexdigest()))
    if exchange['reason'] or exchange['http_status'] != 200:
        raise ValueError('parts.search: ' + str(exchange['reason'] or exchange['http_status']))
    doc = stock._strict_json(raw)
    if not isinstance(doc, dict) or doc.get('code') != 200:
        raise ValueError('parts.search: catalog did not return code 200')
    page = doc.get('data', {}).get('componentPageInfo', {})
    rows = page.get('list') or []
    if not isinstance(rows, list):
        raise ValueError('parts.search: missing component list')
    parts = []
    for row in rows:
        lcsc = row.get('componentCode')
        status, part = stock.parse_search_response(raw, lcsc)
        if status != 'found':
            continue
        urls = {key: row.get(key) for key in ('dataManualFileAccessIdUrl',
            'dataManualOfficialLink', 'dataManualUrl') if row.get(key)}
        part.update(datasheet_urls=urls,
            datasheet_status='candidate URL; PDF not yet verified' if urls else 'unavailable')
        parts.append(part)
    tiers = {'Basic': 0, 'Preferred': 1, 'Extended': 2}
    parts.sort(key=lambda p: (p['stock'] == 0, not p['datasheet_urls'],
        tiers[p['library_tier']], p['price1'] is None, p['price1'] or 0, p['lcsc']))
    result = dict(schema='agent-ee.design-search/1', keyword=keyword, parts=parts,
        limitations=['One page only; no stock reservation; candidate datasheet URLs are unverified.'])
    write_json(out / 'parts.json', result)
    return result


def member(lib_id):
    if not isinstance(lib_id, str) or not re.fullmatch(r'[^:/\\\s]+:[^:/\\\s]+', lib_id) or '..' in lib_id:
        raise ValueError('CAD identifier: use library:member without path separators')
    return lib_id.split(':')


def inspect_symbol(lib_id, root=Path('/usr/share/kicad')):
    from .symbol_capture import capture_symbols
    from .symbol_library import parse_library, resolve_symbol
    library, name = member(lib_id)
    raw = (Path(root) / 'symbols' / (library + '.kicad_sym')).read_bytes()
    captured, receipt = capture_symbols(raw, [name], library=library)
    pins, graphics, properties = resolve_symbol(parse_library(captured), name,
        library + '.kicad_sym', hashlib.sha256(captured).hexdigest())
    return dict(lib_id=lib_id, pins=pins, properties=properties, receipt=receipt)


def inspect_footprint(lib_id, root=Path('/usr/share/kicad')):
    from .footprint_library import index_footprints
    from .symbol_library import children, parse_sexpr
    library, name = member(lib_id)
    rel = f'{library}.pretty/{name}.kicad_mod'
    raw = (Path(root) / 'footprints' / rel).read_bytes()
    if any(p[3] == 'custom' for p in children(parse_sexpr(raw), 'pad')):
        raise ValueError('parts.footprint: custom pads are outside the offline compiler subset')
    manifest = dict(schema='agent-ee.footprint-libraries/1', complete=True,
        libraries=[dict(name=library, members=[dict(path=rel, sha256=hashlib.sha256(raw).hexdigest())])])
    return index_footprints(manifest, {rel: raw})


def inspect_courtyard(lib_ids, root=Path('/usr/share/kicad')):
    from .courtyard import footprint_courtyard
    from .symbol_library import parse_sexpr
    if isinstance(lib_ids, str):
        lib_ids = [lib_ids]
    results = []
    total_area = 0.0
    for lib_id in lib_ids:
        library, name = member(lib_id)
        rel = f'{library}.pretty/{name}.kicad_mod'
        raw = (Path(root) / 'footprints' / rel).read_bytes()
        node = parse_sexpr(raw)
        info = footprint_courtyard(node)
        results.append(dict(lib_id=lib_id, courtyard=info))
        if info.get('area_mm2') is not None:
            total_area += info['area_mm2']
    return dict(schema='agent-ee.design-courtyard/1', footprints=results,
                courtyard_area_sum_mm2=total_area)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('search')
    p.add_argument('keyword')
    p.add_argument('--out', required=True, type=Path)
    for command in ('symbol', 'footprint'):
        p = sub.add_parser(command)
        p.add_argument('lib_id')
        p.add_argument('--kicad-root', type=Path, default=Path('/usr/share/kicad'))
    p = sub.add_parser('courtyard')
    p.add_argument('lib_ids', nargs='+')
    p.add_argument('--kicad-root', type=Path, default=Path('/usr/share/kicad'))
    args = parser.parse_args(argv)
    try:
        if args.command == 'search':
            result = search_parts(args.keyword, args.out)
        elif args.command == 'courtyard':
            result = inspect_courtyard(args.lib_ids, args.kicad_root)
        elif args.command == 'symbol':
            result = inspect_symbol(args.lib_id, args.kicad_root)
        else:
            result = inspect_footprint(args.lib_id, args.kicad_root)
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
