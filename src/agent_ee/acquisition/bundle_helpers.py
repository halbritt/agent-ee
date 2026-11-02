"""Shared variant bundle synthesis and verification helpers for acquisition recipes."""
from hashlib import sha256
from pathlib import Path

from ..catalog import strict_json
from ..footprint_library import _members as _footprint_members
from ..parts_manifest import _read_blob
from ..storage import identity, json_bytes


def _captured(root, pins):
    """Read and verify captured blobs against pinned SHA-256 hashes."""
    return {p: _read_blob(root, dict(path=p, sha256=h)) for p, h in pins.items()}


captured = _captured


def _members(cad):
    """Inspect footprint member blobs in CAD manifest."""
    return _footprint_members(cad)


members = _members


def compute_cad_members_sha256(cad, circuit):
    """Compute the combined SHA-256 identity of CAD footprints and circuit libraries."""
    member_blobs = [blob for _, blob in _footprint_members(cad)] + [
        {k: lib[k] for k in ('path', 'sha256')} for lib in circuit.get('libraries', [])]
    return identity(sorted(member_blobs, key=lambda row: row['path']))


def _models(files, selections=None, models=None):
    """Attach 3D step models to footprints in CAD manifest without altering copper."""
    if selections is None:
        return
    manifest = strict_json(files['parts.json'])
    cad_path = manifest['cad']['manifest']['path']
    cad = strict_json(files[cad_path])
    for ref, name in (models or {}).items():
        if ref not in selections:
            continue
        footprint = selections[ref]['footprint']
        library, member = footprint.split(':')
        path = library + '.pretty/' + member + '.kicad_mod'
        raw = files[path].decode()
        if '(model ' in raw or not raw.rstrip().endswith(')') or 'lib3d/' + name not in files:
            raise ValueError(f'footprint or 3D model input differs: {ref}')
        files[path] = (raw.rstrip()[:-1] + '\n(model "${KIPRJMOD}/../lib3d/' + name + '" '
            '(offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))\n)\n').encode()
        for lib, blob in _footprint_members(cad):
            if blob['path'] == path:
                blob['sha256'] = sha256(files[path]).hexdigest()
    files[cad_path] = json_bytes(cad)
    manifest['cad']['manifest']['sha256'] = sha256(files[cad_path]).hexdigest()
    circuit = strict_json(files['circuit.json'])
    manifest['cad']['members_sha256'] = compute_cad_members_sha256(cad, circuit)
    files['parts.json'] = json_bytes(manifest)
    return manifest


models = _models
