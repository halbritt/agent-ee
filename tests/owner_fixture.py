"""Committed owner citations for original test fixtures (AGENTEE-348).

Production resolves owner authority only through ``source_authority.OWNER_DECISIONS``,
which no test edits on disk. A fixture that exercises hard owner enforcement commits
its original decision text to a private temporary repository, proves that commit and
those bytes with ``verify_committed`` and pins them in this test process only. The
commit is real; its author is the fixture, exactly as before the citation model.
"""
import atexit
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from agent_ee import source_authority

REPOSITORY = 'test-fixture/owner-decisions'
_ROOT = []
_PINNED = {}


def _git(root, *args):
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'Original fixture author', 'GIT_AUTHOR_EMAIL': 'fixture@invalid',
           'GIT_COMMITTER_NAME': 'Original fixture author', 'GIT_COMMITTER_EMAIL': 'fixture@invalid',
           'GIT_AUTHOR_DATE': '2026-10-22T00:00:00Z', 'GIT_COMMITTER_DATE': '2026-10-22T00:00:00Z',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
    return subprocess.run(['git', '-C', str(root), *args], env=env, check=True,
                          capture_output=True).stdout.decode().strip()


def committed(path, text):
    """Commit ``text`` at ``path``; return the citation's repository and commit fields."""
    key = (path, text)
    if key not in _PINNED:
        if not _ROOT:
            _ROOT.append(Path(tempfile.mkdtemp(prefix='agent-ee-owner-fixture-')))
            atexit.register(shutil.rmtree, _ROOT[0], True)
            _git(_ROOT[0], 'init', '-q')
        root = _ROOT[0]
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding='utf-8')
        _git(root, 'add', '--', path)
        _git(root, '-c', 'commit.gpgsign=false', 'commit', '-q', '--allow-empty', '-m', 'fixture decision ' + path)
        commit = _git(root, 'rev-parse', 'HEAD')
        sha256 = hashlib.sha256(text.encode('utf-8')).hexdigest()
        problems = source_authority.verify_committed(root, commit, path, sha256)
        if problems:
            raise AssertionError('fixture owner decision did not commit: ' + str(problems))
        source_authority.OWNER_DECISIONS[(REPOSITORY, commit, path)] = sha256
        _PINNED[key] = {'repository': REPOSITORY, 'commit': commit}
    return dict(_PINNED[key])


def repository():
    """The private fixture repository (for resolution probes)."""
    return _ROOT[0]


def module_command(module):
    """``python -m module`` for a child process that keeps this process's verified pins."""
    import sys
    pins = {key: value for key, value in source_authority.OWNER_DECISIONS.items() if key[0] == REPOSITORY}
    prelude = ('import runpy, sys\nfrom agent_ee import source_authority\n'
               f'source_authority.OWNER_DECISIONS.update({pins!r})\n'
               f'sys.argv[0] = {module!r}\nrunpy.run_module({module!r}, run_name="__main__", alter_sys=True)\n')
    return [sys.executable, '-c', prelude]


def citation(cls, text, path, locator='fixture limit'):
    """The AGENTEE-348 cited form of ``cls`` over original fixture ``text`` captured at ``path``."""
    reference = {'path': path, 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                 'locator': locator, 'quote': text}
    if cls == 'manufacturer':
        # AGENTEE-348 H5: a captured manufacturer document whose quote states the value,
        # never the builtin capture attached to an arbitrary limit.
        return {'kind': 'manufacturer_source', 'manufacturer': 'Original fixture fabricator', **reference}
    if cls == 'owner':
        return {'kind': 'owner_decision', **reference, 'owner': 'Original fixture author', **committed(path, text)}
    return {'kind': 'captured_source', **reference}


def cite(document, *specs):
    """Cite each named labelled declaration of a constraint document over original fixture text.

    ``specs`` are the declaration objects inside ``document``; each gets one captured
    record that states its millimetre values, so it stays hard under the citation model.
    """
    records = document.setdefault('source_records', {})
    for spec in specs:
        values = [f'{v} mm' for k, v in sorted(spec.items()) if k.endswith('_mm')]
        text = 'Original fixture limit: ' + (', '.join(values) or spec.get('rule', spec['basis']))
        path = 'fixture-sources/' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:16] + '.txt'
        records[path] = text
        spec['citation'] = citation(spec['class'], text, path)
    return document


def owner_bound(family, limit_nm, records, basis='Explicit original fixture requirement'):
    """Locality owner provenance whose committed fixture decision states ``limit_nm`` (H2).

    The record is added to ``records``, the document's captured ``source_records``.
    """
    text = f'Original fixture author: the {family} test maximum is {limit_nm} nm.'
    path = f'fixture-decisions/{family}-{limit_nm}nm.txt'
    records[path] = text
    return {'class': 'owner', 'basis': basis, 'decision_reference': {
        'path': path, 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
        'locator': family + ' test parameter contract', 'quote': text, 'owner': 'Original fixture author',
        **committed(path, text)}}


def rebind(document, family):
    """Re-cite every ``family`` fixture pair of a locality document at its current maximum.

    Tests that change a fixture bound keep its owner decision value-bound (AGENTEE-348 H2).
    """
    records = document.setdefault('source_records', {})
    for obligation in document.get('obligations', []):
        for pair in obligation.values():
            decision = pair.get('provenance', {}).get('decision_reference') if isinstance(pair, dict) else None
            if (isinstance(decision, dict) and decision.get('locator') == family + ' test parameter contract'
                    and type(pair.get('max_distance_nm')) is int):
                pair['provenance'] = owner_bound(family, pair['max_distance_nm'], records,
                                                 pair['provenance'].get('basis', 'Explicit original fixture requirement'))
    return document


def owner_rules(document, raw, filename):
    """Declare every rule of captured native ``raw`` as owner, cited by a committed decision.

    Each decision states the rule's exact payload, so it qualifies under AGENTEE-348 H1;
    a captured native file alone confers no owner authority.
    """
    from decimal import Decimal
    from agent_ee.design_rule_provenance import rule_payload
    from agent_ee.net_constraints import rule_forms
    declared = document.setdefault('custom_rules', {}).setdefault(filename, [])
    records = document.setdefault('source_records', {})
    for node in rule_forms(raw, native=True):
        if not (isinstance(node, list) and len(node) > 1 and node[0] == 'rule'):
            continue
        name = str(node[1])
        lengths, counts = rule_payload(node)
        values = [format((Decimal(v.numerator) / Decimal(v.denominator)).normalize(), 'f') + ' mm' for v in lengths]
        text = f'Original fixture owner rule {name}: ' + ', '.join(values + [str(n) for n in counts])
        path = 'fixture-decisions/rule-' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:16] + '.txt'
        records[path] = text
        declared[:] = [spec for spec in declared if spec['rule'] != name]
        declared.append({'rule': name, 'class': 'owner', 'basis': 'Original fixture owner rule',
                         'citation': citation('owner', text, path)})
    return document


def owner_rule_project(raw, filename, project=None):
    """``project`` bound to an owner declaration of every rule in native ``raw`` (H1 migration)."""
    import copy as _copy
    from agent_ee.design_rule_provenance import bind_project, import_constraints
    from agent_ee.storage import json_bytes
    document = owner_rules({'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {}}, raw, filename)
    project, record = import_constraints(_copy.deepcopy(project or {}), json_bytes(document),
                                         'fixture-sources/owner-rules.json', 'fixture')
    bind_project(project, record, raw)
    return project


def bind_owner_rules(board):
    """Bind the owner declaration of ``board``'s captured ``.kicad_dru`` into its ``.kicad_pro``."""
    import json
    from pathlib import Path
    board = Path(board)
    pro = board.with_suffix('.kicad_pro')
    raw = board.with_suffix('.kicad_dru').read_bytes()
    project = json.loads(pro.read_bytes()) if pro.is_file() else {}
    project.pop('agent_ee_constraint_provenance', None)
    pro.write_text(json.dumps(owner_rule_project(raw, board.with_suffix('.kicad_dru').name, project)))
