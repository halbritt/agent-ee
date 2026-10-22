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
    if cls == 'manufacturer':
        from agent_ee.locality_provenance import manufacturing_record
        capture = manufacturing_record()['sources']['pcb']
        return {'kind': 'manufacturer_capture', 'capture': 'pcb', 'sha256': capture['sha256'], 'locator': locator}
    reference = {'path': path, 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                 'locator': locator, 'quote': text}
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
