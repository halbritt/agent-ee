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
# Citation kinds each class may resolve through. ``manufacturer_capture`` binds the
# hash-pinned builtin record (raw vendor bytes uncommitted) to one of its transcribed
# numeric facts; ``manufacturer_source`` is a captured manufacturer document whose quote
# states the value, the evidence a fab profile already requires.
KINDS = {'owner': ('owner_decision',), 'manufacturer': ('manufacturer_capture', 'manufacturer_source'),
         'datasheet': ('captured_source',),
         'engineering': ('captured_source', 'manufacturer_capture', 'manufacturer_source', 'owner_decision')}
# Numeric facts transcribed in each builtin manufacturer capture.
CAPTURE_FACTS = {'pcb': ('different_net_clearance_nm',)}
# Exact length units to millimetres (native rules and quotes).
UNITS = {'mm': Fraction(1), 'nm': Fraction(1, 10 ** 6), 'um': Fraction(1, 1000), 'mil': Fraction(254, 10000),
         'in': Fraction(254, 10)}


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


def millimetres(value):
    """An exact millimetre Fraction from a JSON number or a Fraction."""
    return value if isinstance(value, Fraction) else Fraction(str(value))


def manufacturer_capture(citation, values_mm=()):
    """A named capture in the hash-pinned manufacturer record, bound to one recorded fact."""
    from .locality_provenance import manufacturing_record
    if (not isinstance(citation, dict) or any(not isinstance(citation.get(k), str)
            or not citation[k].strip() for k in ('capture', 'sha256', 'locator'))):
        return advisory('manufacturer citation needs a capture name, its sha256 and a locator')
    record = manufacturing_record()
    capture = record['sources'].get(citation['capture'])
    if capture is None:
        return advisory('no captured manufacturer record named ' + citation['capture'])
    if capture['sha256'] != citation['sha256']:
        return advisory('manufacturer capture sha256 differs from the pinned capture (stale)')
    if record.get('locators', {}).get(citation['capture']) != citation['locator']:
        return advisory('manufacturer citation locator differs from the captured record locator')
    fact = citation.get('fact')
    if not isinstance(fact, str) or fact not in CAPTURE_FACTS.get(citation['capture'], ()):
        return advisory(f'manufacturer capture {citation["capture"]} records no fact {fact}')
    if any(millimetres(v) != Fraction(record[fact]) * UNITS['nm'] for v in values_mm):
        return advisory('declared value differs from the captured manufacturer fact ' + fact)
    return {'enforcement': 'hard', 'reason': 'captured manufacturer fact verified by hash'}


# A quoted numeric literal (AGENTEE-348 round-2 H1-H3): the COMPLETE run of digits,
# decimal point, sign, exponent and separator characters is one literal, never a suffix of
# one. It may not start inside a word, another literal or after a sign or operator.
LITERAL = re.compile(r'(?<![\w.,/+\-−±])[+\-−±]?[0-9][0-9.,/]*(?:[eE][+\-−]?[0-9]+)?')
DECIMAL = re.compile(r'[+\-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+\-]?[0-9]+)?')
# Length units a quote may state; each converts exactly to millimetres.
QUOTE_UNITS = {'mm': Fraction(1), 'nm': Fraction(1, 10 ** 6), 'um': Fraction(1, 1000),
               'µm': Fraction(1, 1000), 'μm': Fraction(1, 1000), 'mil': Fraction(254, 10000)}
UNIT = re.compile(r'[ \t]?(mm|nm|um|µm|μm|mil)(?![\w])')
# Explicit count facts: a dimensionless integer bound to the noun it counts.
COUNT_NOUNS = {'thermal_spoke_count': ('spoke', 'spokes'), 'via_count': ('via', 'vias')}


def numeric_literals(quote):
    """Typed facts from a quote: ('length', mm) or ('number', value), with their spans.

    A literal is read whole and parsed exactly, sign and exponent included. A literal with
    a separator, fraction or range character is unsupported and yields no fact.
    """
    facts = []
    for match in LITERAL.finditer(quote):
        text = match[0].rstrip('.,')
        end = match.start() + len(text)
        if DECIMAL.fullmatch(text.replace('−', '-')) is None:
            continue
        value = Fraction(text.replace('−', '-'))
        unit = UNIT.match(quote, end)
        if unit:
            facts.append(('length', value * QUOTE_UNITS[unit[1]], match.start(), unit.end()))
        elif re.match(r'[\wµμ]', quote[end:end + 1]):
            continue  # a literal run into a word ('3x', '3rd') states nothing
        elif not re.match(r'[ \t]?[A-Za-zµμ]', quote[end:]) or COUNT_TAIL.match(quote, end):
            facts.append(('number', value, match.start(), end))
    return facts


COUNT_TAIL = re.compile(r'[ \t]+(?:thermal[ \t]+)?(spokes?|vias?)(?![\w])', re.I)
COUNT_HEAD = re.compile(r'(spoke|via)[ \t]+count[ \t]*(?:of|is|=|:)?[ \t]*$', re.I)


def counted(quote, fact):
    """The count kind an integer fact explicitly counts, or None."""
    kind, value, start, end = fact
    if kind != 'number' or not quote[start:end].isdigit():  # an unsigned integer literal only
        return None
    tail, head = COUNT_TAIL.match(quote, end), COUNT_HEAD.search(quote[:start])
    noun = (tail[1] if tail else head[1] if head else '').lower()
    return next((count for count, nouns in COUNT_NOUNS.items() if noun in nouns or noun + 's' in nouns), None)


def states(quote, values_mm, counts=()):
    """Every governed value is an explicit typed fact of the quote, compared exactly.

    A length matches only a stated length of equal exact value; a count ``(kind, n)``
    matches only an integer explicitly counting that kind. A length never qualifies a
    count, and a number never qualifies a length.
    """
    facts = numeric_literals(quote)
    lengths = {value for kind, value, _, _ in facts if kind == 'length'}
    stated = {(counted(quote, fact), fact[1]) for fact in facts}
    return (all(millimetres(v) in lengths for v in values_mm)
            and all(isinstance(c, tuple) and len(c) == 2 and type(c[1]) is int
                    and (c[0], Fraction(c[1])) in stated for c in counts))


def cite(cls, citation, records, values_mm=(), counts=()):
    """Resolve a class's citation against the governed values; a label alone is advisory.

    Authority binds a payload: a citation with no declared value to state is advisory.
    """
    if cls not in KINDS:
        return advisory('default class is never hard')
    if not isinstance(citation, dict):
        return advisory('free-text ' + cls + ' label without a resolvable citation')
    kind = citation.get('kind')
    if kind not in KINDS[cls]:
        return advisory(cls + ' cannot resolve through citation kind ' + repr(kind))
    if not list(values_mm) and not list(counts):
        return advisory('no declared value to bind to the citation')
    if kind == 'manufacturer_capture':
        return manufacturer_capture(citation, values_mm) if not list(counts) else advisory(
            'a manufacturer capture records no count')
    if kind == 'manufacturer_source' and (not isinstance(citation.get('manufacturer'), str)
                                          or not citation['manufacturer'].strip()):
        return advisory('manufacturer source names no manufacturer')
    result = (owner_decision if kind == 'owner_decision' else verified_reference)(citation, records)
    if result['enforcement'] == 'verified':
        result = {'enforcement': 'hard', 'reason': 'captured source record verified'}
    elif kind != 'owner_decision' and result['enforcement'] == 'advisory':
        result = advisory('captured source record, locator or quote not verified')
    if result['enforcement'] == 'hard' and not states(citation['quote'], values_mm, counts):
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
