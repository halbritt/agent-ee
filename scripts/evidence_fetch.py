#!/usr/bin/python3
"""Inventory retained evidence and explicitly fetch verified named groups (Linux)."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

SCHEMA = 'agent-ee.evidence-files/1'


def relative(name):
    if (not isinstance(name, str) or not name or '\\' in name or
            any(ord(c) < 32 for c in name) or
            PurePosixPath(name).is_absolute() or
            any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError(f'unsafe evidence path: {name!r}')
    return name


def inventory(root):
    """Inventory every regular file below each immediate evidence directory."""
    groups = {}
    for group in sorted(Path(root).iterdir()):
        if group.is_symlink():
            raise ValueError(f'link in evidence: {group}')
        if not group.is_dir():
            continue  # Top-level human summaries stay in the repository.
        files = {}
        for path in sorted(group.rglob('*')):
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError(f'non-regular evidence: {path}')
            name = relative(path.relative_to(group).as_posix())
            if path.name == '.credentials.json':
                raise ValueError('credential contents must never be published')
            checksum = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    checksum.update(block)
            files[name] = {'bytes': path.stat().st_size, 'sha256': checksum.hexdigest()}
        if files:
            groups[relative(group.name)] = {'files': files}
    return {'schema': SCHEMA, 'groups': groups}


def group_files(manifest, group):
    relative(group)
    if '/' in group or manifest.get('schema') != SCHEMA:
        raise ValueError('invalid group or manifest schema')
    files = manifest['groups'][group]['files']
    if not isinstance(files, dict) or not files:
        raise ValueError('group must contain files')
    for name, record in files.items():
        relative(name)
        if (not isinstance(record, dict) or type(record.get('bytes')) is not int or
                record['bytes'] < 0 or not isinstance(record.get('sha256'), str) or
                not re.fullmatch('[0-9a-f]{64}', record['sha256'])):
            raise ValueError(f'invalid size/hash: {name}')
        if '.credentials.json' in name.split('/'):
            raise ValueError('credential path in manifest')
        for parent in PurePosixPath(name).parents:
            if parent.as_posix() in files:
                raise ValueError(f'file/directory conflict: {name}')
    return files


def open_directory(path):
    """Pin an existing directory while refusing links in every ancestor."""
    path = Path(os.path.abspath(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def download(url, destination, record):
    checksum = hashlib.sha256()
    remaining = record['bytes']
    with urlopen(url, timeout=30) as source, destination.open('xb') as output:
        while block := source.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(block)
            if remaining < 0:
                raise ValueError(f'download exceeds manifest size: {url}')
            checksum.update(block)
            output.write(block)
        if remaining or checksum.hexdigest() != record['sha256']:
            raise ValueError(f'download size/hash mismatch: {url}')
        output.flush()
        os.fsync(output.fileno())


def fetch(manifest, group, base_url, destination):
    files = group_files(manifest, group)
    url = urlsplit(base_url)
    if url.scheme not in ('https', 'http', 'file') or url.query or url.fragment:
        raise ValueError('store must be an explicit https/http/file base URL without query/fragment')
    parent_fd = open_directory(destination)
    try:
        # Pin staging and publication to the same directory, even if renamed by
        # another process. RENAME_NOREPLACE also refuses a racing empty directory.
        parent = Path(f'/proc/self/fd/{parent_fd}')
        if os.path.lexists(parent / group):
            raise FileExistsError(parent / group)
        with tempfile.TemporaryDirectory(prefix='.evidence-fetch-', dir=parent) as scratch:
            stage = Path(scratch) / group
            stage.mkdir()
            for name, record in sorted(files.items()):
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                download(base_url.rstrip('/') + '/' + quote(group + '/' + name, safe='/'), target, record)
            libc = ctypes.CDLL(None, use_errno=True)
            rename = libc.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            source = os.fsencode(str(stage.relative_to(parent)))
            if rename(parent_fd, source, parent_fd, os.fsencode(group), 1):
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), group)
            os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate manifest key: {key}')
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('manifest')
    build.add_argument('--evidence', type=Path, default=Path('evidence'))
    build.add_argument('--out', type=Path, required=True)
    get = commands.add_parser('fetch')
    get.add_argument('group')
    get.add_argument('--manifest', type=Path, default=Path('evidence/files-manifest.json'))
    get.add_argument('--base-url', required=True)
    get.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'manifest':
        manifest = inventory(args.evidence)
        with args.out.open('x') as stream:
            stream.write(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    else:
        manifest = json.loads(args.manifest.read_text(), object_pairs_hook=unique_object)
        fetch(manifest, args.group, args.base_url, args.destination)
        print(args.destination / args.group)


if __name__ == '__main__':
    main()
