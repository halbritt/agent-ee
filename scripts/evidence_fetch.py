#!/usr/bin/python3
"""Inventory retained evidence and explicitly fetch verified named groups (Linux)."""

import argparse
import ctypes
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import uuid
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

SCHEMA = 'agent-ee.evidence-files/1'
# Outside every checkout; never committed. AGENT_EE_EVIDENCE_CREDENTIALS overrides it.
CREDENTIALS = '~/.config/agent-ee/evidence-s3.env'
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()


class StoreUnavailable(Exception):
    """Typed refusal: the relocated evidence store cannot be used from here.

    Raised for a missing, unreadable, malformed or group/other-accessible
    credentials file. It never means that evidence is absent or acceptable.
    """


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


def credentials_path(path=None):
    return Path(os.path.expanduser(path or os.environ.get('AGENT_EE_EVIDENCE_CREDENTIALS') or CREDENTIALS))


def read_credentials(path=None):
    """Parse a KEY=VALUE credentials file that only its owner can read."""
    path = credentials_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise StoreUnavailable(f'evidence store credentials not found: {path}; relocated evidence '
                               'cannot be fetched without them (see docs/reproducing-evidence.md)') from None
    except OSError as exc:
        raise StoreUnavailable(f'evidence store credentials unreadable: {path}: {exc.strerror}') from None
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise StoreUnavailable(f'evidence store credentials must be a regular file with mode 0600: {path}')
        values = {}
        for number, line in enumerate(stream, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.partition('=')
            if not sep or not re.fullmatch('[A-Z0-9_]+', key):
                raise StoreUnavailable(f'malformed credentials line {number} in {path}')
            values[key] = value.strip()
    missing = [key for key in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AGENT_EE_EVIDENCE_S3_ENDPOINT')
               if not values.get(key)]
    if missing:
        raise StoreUnavailable(f'evidence store credentials {path} lack {", ".join(missing)}')
    return values


class S3Store:
    """Path-style S3 (Garage) reads and writes signed with AWS Signature Version 4."""

    def __init__(self, base_url, credentials, endpoint=None):
        url = urlsplit(base_url)
        if url.scheme != 's3' or not url.netloc or url.query or url.fragment:
            raise ValueError('store must be s3://BUCKET[/PREFIX]')
        self.bucket, self.prefix = url.netloc, url.path.strip('/')
        endpoint = urlsplit(endpoint or credentials['AGENT_EE_EVIDENCE_S3_ENDPOINT'])
        if endpoint.scheme not in ('http', 'https') or not endpoint.netloc or endpoint.path.strip('/') \
                or endpoint.query or endpoint.fragment:
            raise StoreUnavailable('evidence store endpoint must be an http(s) origin without a path')
        self.endpoint = endpoint
        self.region = credentials.get('AWS_DEFAULT_REGION') or 'garage'
        self.key_id = credentials['AWS_ACCESS_KEY_ID']
        self._secret = credentials['AWS_SECRET_ACCESS_KEY']

    def object_key(self, group, name):
        return '/'.join(part for part in (self.prefix, group, name) if part)

    def request(self, method, key, payload_sha256=EMPTY_SHA256, data=None, length=None, now=None, extra=None):
        path = '/' + quote(self.bucket, safe='') + '/' + quote(key, safe='/-_.~')
        now = now or datetime.datetime.now(datetime.timezone.utc)
        stamp, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
        headers = {'host': self.endpoint.netloc, 'x-amz-content-sha256': payload_sha256, 'x-amz-date': stamp,
                   **{name.lower(): value for name, value in (extra or {}).items()}}
        signed = ';'.join(sorted(headers))
        canonical = '\n'.join([method, path, '', *(f'{name}:{headers[name]}' for name in sorted(headers)),
                               '', signed, payload_sha256])
        scope = f'{day}/{self.region}/s3/aws4_request'
        text = '\n'.join(['AWS4-HMAC-SHA256', stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        key_bytes = ('AWS4' + self._secret).encode()
        for part in (day, self.region, 's3', 'aws4_request'):
            key_bytes = hmac.new(key_bytes, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key_bytes, text.encode(), hashlib.sha256).hexdigest()
        headers['authorization'] = (f'AWS4-HMAC-SHA256 Credential={self.key_id}/{scope}, '
                                    f'SignedHeaders={signed}, Signature={signature}')
        if length is not None:
            headers['content-length'] = str(length)
        del headers['host']  # urllib supplies the identical Host header.
        return Request(f'{self.endpoint.scheme}://{self.endpoint.netloc}{path}', data=data,
                       headers=headers, method=method)

    def __repr__(self):
        return f'S3Store(s3://{self.bucket}/{self.prefix} via {self.endpoint.geturl()}, key {self.key_id})'


def open_store(base_url, credentials=None, endpoint=None):
    """None for plain https/http/file URLs; an S3Store (credentials required) for s3:// URLs."""
    if urlsplit(base_url).scheme != 's3':
        return None
    return S3Store(base_url, read_credentials(credentials), endpoint)


def download(url, destination, record):
    label = getattr(url, 'full_url', url)
    checksum = hashlib.sha256()
    remaining = record['bytes']
    with urlopen(url, timeout=30) as source, destination.open('xb') as output:
        while block := source.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(block)
            if remaining < 0:
                raise ValueError(f'download exceeds manifest size: {label}')
            checksum.update(block)
            output.write(block)
        if remaining or checksum.hexdigest() != record['sha256']:
            raise ValueError(f'download size/hash mismatch: {label}')
        output.flush()
        os.fsync(output.fileno())


def fetch(manifest, group, base_url, destination, store=None):
    files = group_files(manifest, group)
    url = urlsplit(base_url)
    if store is None and url.scheme == 's3':
        raise StoreUnavailable('s3:// stores need credentials; use open_store()')
    if url.scheme not in ('https', 'http', 'file', 's3') or url.query or url.fragment:
        raise ValueError('store must be an explicit https/http/file/s3 base URL without query/fragment')
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
                source = (store.request('GET', store.object_key(group, name)) if store else
                          base_url.rstrip('/') + '/' + quote(group + '/' + name, safe='/'))
                download(source, target, record)
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


class AtomicCreateUnavailable(StoreUnavailable):
    """Typed refusal: the store does not enforce PUT If-None-Match: *.

    Without an atomic create-if-absent a writer racing between the existence
    check and the PUT would be silently replaced, so upload refuses outright.
    (Garage v2.3.0, the owner's store on 2026-10-22, ignores the precondition.)
    """


CREATE_IF_ABSENT = {'If-None-Match': '*'}
# Precondition Failed, or Conflict for a concurrent conditional write (AWS S3).
CREATE_REFUSED = (409, 412)


def put_if_absent(store, key, record, data):
    """True if this PUT created the object; False if the store refused because one exists."""
    try:
        with urlopen(store.request('PUT', key, record['sha256'], data, len(data), extra=CREATE_IF_ABSENT),
                     timeout=120):
            return True
    except HTTPError as exc:
        if exc.code in CREATE_REFUSED:
            return False
        raise


def require_atomic_create(store):
    """Demonstrate that the store enforces create-if-absent, or raise AtomicCreateUnavailable.

    Writes one fresh random probe key twice with If-None-Match: *; the second
    write must be refused. Only that probe key is written and then deleted.
    """
    key = store.object_key('.agent-ee-create-probe', uuid.uuid4().hex)
    probe = {'sha256': hashlib.sha256(b'probe').hexdigest()}
    if not put_if_absent(store, key, probe, b'probe'):
        raise AtomicCreateUnavailable(f'create-if-absent probe refused a fresh key {key}')
    try:
        second = {'sha256': hashlib.sha256(b'replaced').hexdigest()}
        if put_if_absent(store, key, second, b'replaced'):
            raise AtomicCreateUnavailable(
                f'{store!r} ignores PUT If-None-Match: *; upload refuses because a concurrent writer could be '
                'replaced (see docs/reproducing-evidence.md)')
    finally:
        with urlopen(store.request('DELETE', key), timeout=30):
            pass


def verify_existing(store, key, record):
    """Accept an existing object only if a GET returns exactly the manifest bytes; never replace it."""
    with tempfile.TemporaryDirectory() as scratch:
        download(store.request('GET', key), Path(scratch) / 'object', record)


def local_bytes(path, record):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'missing or non-regular local evidence: {path}')
    data = path.read_bytes()
    if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError(f'local evidence differs from manifest: {path}')
    return data


def upload(manifest, group, evidence, store):
    """Upload one group's exact manifest bytes; never replace a differing object.

    Local bytes are checked against the manifest before sending. Upload first
    demonstrates that the store enforces create-if-absent and refuses
    (AtomicCreateUnavailable) when it does not. Every PUT carries
    If-None-Match: * and declares the manifest SHA-256 as its signed payload
    hash, so the store rejects a body that changed in transit. An object that
    exists, including one created concurrently after the existence check, is
    accepted only after a GET returns exactly the manifest bytes.
    """
    files = group_files(manifest, group)
    root = Path(evidence) / group
    for name, record in sorted(files.items()):
        local_bytes(root / name, record)
    require_atomic_create(store)
    for name, record in sorted(files.items()):
        key = store.object_key(group, name)
        try:
            with urlopen(store.request('HEAD', key), timeout=30):
                pass
        except HTTPError as exc:
            if exc.code != 404:
                raise
            if put_if_absent(store, key, record, local_bytes(root / name, record)):
                continue
        verify_existing(store, key, record)
    return len(files)


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
    put = commands.add_parser('upload')
    put.add_argument('group')
    put.add_argument('--manifest', type=Path, default=Path('evidence/files-manifest.json'))
    put.add_argument('--evidence', type=Path, required=True)
    put.add_argument('--base-url', required=True)
    for command in (get, put):
        command.add_argument('--credentials', help=f's3:// credentials file (default {CREDENTIALS})')
        command.add_argument('--endpoint', help='override the credentials file S3 endpoint origin')
    args = parser.parse_args()
    if args.command == 'manifest':
        manifest = inventory(args.evidence)
        with args.out.open('x') as stream:
            stream.write(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        return 0
    manifest = json.loads(args.manifest.read_text(), object_pairs_hook=unique_object)
    try:
        store = open_store(args.base_url, args.credentials, args.endpoint)
    except StoreUnavailable as exc:
        print(f'evidence_fetch: refused (store unavailable): {exc}', file=sys.stderr)
        return 3
    if args.command == 'upload':
        if store is None:
            raise ValueError('upload requires an s3:// store')
        try:
            count = upload(manifest, args.group, args.evidence, store)
        except AtomicCreateUnavailable as exc:
            print(f'evidence_fetch: refused (no atomic create-if-absent): {exc}', file=sys.stderr)
            return 3
        print(f'{args.group}: {count} files present in store')
    else:
        fetch(manifest, args.group, args.base_url, args.destination, store)
        print(args.destination / args.group)
    return 0


if __name__ == '__main__':
    sys.exit(main())
