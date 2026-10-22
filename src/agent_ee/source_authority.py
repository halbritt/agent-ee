"""One citation model for owner, manufacturer, datasheet and engineering limits (AGENTEE-348).

A class label or a prose basis never grants enforcement. A limit is hard only
when its citation resolves to evidence of its kind; otherwise it is advisory
and the reason is recorded. Both the design-rule import (AGENTEE-331) and the
locality bounds (AGENTEE-337) use these functions, so one class name carries
the same authority in both:

- owner: a decision cited at a pinned owner commit (``OWNER_DECISIONS``), with
  captured bytes that hash to the pinned file and contain the quote;
- manufacturer: a named, hash-pinned manufacturer capture;
- datasheet: a captured source record, by hash, with a locator and a quote;
- engineering: any of those citations, under the same checks.

References locate reviewable evidence; they do not authenticate its author.
Deliberate forgery is outside the threat model (AGENTEE-348 owner decision
2026-10-22, owner-relayed); this guards against mislabelled unsourced values.
"""
import hashlib
import re
import subprocess
from fractions import Fraction

from .candidate import safe_relative

SHA256 = re.compile('[0-9a-f]{64}')
COMMIT = re.compile('[0-9a-f]{40}')
# A self-assertion by an agent or the generic design-owner role is not the owner.
AGENT_OWNER = re.compile(r'\bagent(?:[- _]?\d+)?\b|design[-_ ]?owner', re.I)
# Owner decisions citable as owner authority: (repository, commit, path) -> full-file
# SHA-256 of the decision file at that commit. Pinned only by a reviewed agent-ee change
# that traced the commit and bytes to the owner's repository (see verify_committed);
# text a deciding agent writes or captures never extends this table. None is pinned yet.
OWNER_DECISIONS = {}
# Citation kinds each class may resolve through.
KINDS = {'owner': ('owner_decision',), 'manufacturer': ('manufacturer_capture',),
         'datasheet': ('captured_source',),
         'engineering': ('captured_source', 'manufacturer_capture', 'owner_decision')}


def advisory(reason):
    return {'enforcement': 'advisory', 'reason': reason}


def reference(value):
    if not isinstance(value, dict):
        return False
    if any(not isinstance(value.get(k), str) or not value[k].strip()
           for k in ('path', 'sha256', 'locator')):
        return False
    try:
        safe_relative(value['path'])
    except ValueError:
        return False
    return SHA256.fullmatch(value['sha256']) is not None


def verified_reference(value, records):
    """Return typed verification, distinguishing absent evidence from corruption."""
    unsourced = advisory('unsourced numeric locality bound')
    if not reference(value):
        return unsourced
    text = (records or {}).get(value['path'])
    if not isinstance(text, str):
        return unsourced
    if hashlib.sha256(text.encode('utf-8')).hexdigest() != value['sha256']:
        return {'enforcement': 'integrity', 'reason': 'source record sha256 mismatch'}
    quote = value.get('quote')
    if not isinstance(quote, str) or not quote.strip() or quote not in text:
        return unsourced
    return {'enforcement': 'verified', 'reason': 'source record and quote verified'}


def owner_reference(decision, records):
    """Captured owner-decision text and a named non-agent owner, before commit resolution."""
    verification = verified_reference(decision, records)
    if verification['enforcement'] != 'verified':
        return verification
    owner = decision.get('owner')
    if not isinstance(owner, str) or not owner.strip() or AGENT_OWNER.search(owner):
        return advisory('owner decision names no owner, or names an agent or the design-owner role')
    return verification


def owner_decision(decision, records):
    """Hard only for a decision whose cited commit and bytes are a pinned owner commit."""
    verification = owner_reference(decision, records)
    if verification['enforcement'] != 'verified':
        return verification
    commit, repository = decision.get('commit'), decision.get('repository')
    if not isinstance(commit, str) or not commit.strip():
        return advisory('owner decision is not cited at a commit (uncommitted)')
    if COMMIT.fullmatch(commit) is None or not isinstance(repository, str) or not repository.strip():
        return advisory('owner decision commit is not a full commit id in a named repository')
    pinned = OWNER_DECISIONS.get((repository, commit, decision['path']))
    if pinned is None:
        return advisory('owner decision commit does not resolve to a pinned owner commit')
    if pinned != decision['sha256']:
        return advisory('owner decision bytes differ from the pinned owner commit (stale)')
    return {'enforcement': 'hard', 'reason': 'committed owner decision verified'}


def manufacturer_capture(citation):
    """A named capture in the hash-pinned manufacturer record, cited by its hash."""
    from .locality_provenance import manufacturing_record
    if (not isinstance(citation, dict) or any(not isinstance(citation.get(k), str)
            or not citation[k].strip() for k in ('capture', 'sha256', 'locator'))):
        return advisory('manufacturer citation needs a capture name, its sha256 and a locator')
    capture = manufacturing_record()['sources'].get(citation['capture'])
    if capture is None:
        return advisory('no captured manufacturer record named ' + citation['capture'])
    if capture['sha256'] != citation['sha256']:
        return advisory('manufacturer capture sha256 differs from the pinned capture (stale)')
    return {'enforcement': 'hard', 'reason': 'captured manufacturer record verified by hash'}


def states(quote, values_mm):
    """Every cited millimetre value occurs as a whole number token in the quote."""
    tokens = {Fraction(n) for n in re.findall(r'(?<![\w.])([0-9]+(?:\.[0-9]+)?)\s*mm\b', quote)}
    return all(Fraction(str(v)) in tokens for v in values_mm)


def cite(cls, citation, records, values_mm=()):
    """Resolve a class's citation; a label or prose basis alone is advisory."""
    if cls not in KINDS:
        return advisory('default class is never hard')
    if not isinstance(citation, dict):
        return advisory('free-text ' + cls + ' label without a resolvable citation')
    kind = citation.get('kind')
    if kind not in KINDS[cls]:
        return advisory(cls + ' cannot resolve through citation kind ' + repr(kind))
    if kind == 'manufacturer_capture':
        return manufacturer_capture(citation)
    result = (owner_decision if kind == 'owner_decision' else verified_reference)(citation, records)
    if result['enforcement'] == 'verified':
        result = {'enforcement': 'hard', 'reason': 'captured source record verified'}
    elif kind == 'captured_source' and result['enforcement'] == 'advisory':
        result = advisory('captured source record, locator or quote not verified')
    if result['enforcement'] == 'hard' and not states(citation['quote'], values_mm):
        return advisory('cited quote does not state the declared value')
    return result


def verify_committed(repository, commit, path, sha256):
    """Problems resolving a pinned decision in a repository's object store (read-only)."""
    def git(*args):
        return subprocess.run(['git', '--no-optional-locks', '-C', str(repository), *args],
                              capture_output=True, timeout=60)
    found = git('rev-parse', '--verify', '--quiet', commit + '^{commit}')
    if found.returncode != 0 or found.stdout.decode().strip() != commit:
        return ['commit absent from the repository']
    raw = git('show', commit + ':' + path)
    if raw.returncode != 0:
        return ['path absent at the commit']
    if hashlib.sha256(raw.stdout).hexdigest() != sha256:
        return ['committed bytes differ from the cited sha256']
    return []
