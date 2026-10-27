"""Every native subprocess in src/agent_ee goes through agent_ee.native_run (AGENTEE-540).

A direct site is a subprocess/os process call, or a call of executor.execute or
executor.sandbox_argv, anywhere outside native_run.py and executor.py (the sandbox engine
behind the Sandbox adapter). Argv built in a variable counts: the scan cannot see what it holds.

Names resolve per scope (module, enclosing functions, a class body only from itself; innermost
binding wins) through every import form and simple assignment aliases (sp = subprocess,
launch = subprocess.Popen); any reference to a process function counts, called or not, as does
getattr(<module>, "<name>") and `from <module> import *`.

Known static-analysis limits (not detected): importlib.import_module("subprocess"),
__import__("subprocess"), a module reached through a container or attribute other than an import
(self.sp = subprocess; obj.sp.run), aliases built by tuple unpacking, and four scope forms that
need code shadowing a process alias: a default argument evaluated in the enclosing scope
(def f(sp=sp.run([...]))), comprehension variables leaking into the enclosing function, lambda
parameters (they do not shadow), and global/nonlocal declarations (ignored). A symtable-based
resolver would close the scope forms; src has none of them today.

EXEMPT is the approved table of sites that are not native runs, matched by file, enclosing
function and (where given) literal tool, never by a blanket file or tool rule. Any other direct
site, in any file, fails here, as does a literal kicad-cli DRC or netlist-export argv built anywhere
but native_run.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src' / 'agent_ee'
ENGINE = {'src/agent_ee/native_run.py', 'src/agent_ee/executor.py'}
ANY = None   # every site in the file

# agent-579 decision, 2026-10-26 (records d7250bc8 and 140c7a36): these four classes are not native
# runs and are recorded as deviations from the AGENTEE-540 criterion text. Anything else is a violation.
EXEMPT = {
    'in-sandbox router script': {
        'reason': 'kicad_route_attempt.py is stdlib-only and byte-hashed on a read-only mount (AGENTEE-93/539), '
                  'so it cannot import native_run; it runs only inside an outer native run that is already '
                  'sandboxed, deadline-clamped and cancellable. Its launch is not exempt.',
        'tool': None,
        'sites': {('src/agent_ee/kicad_route_attempt.py', ANY)},
    },
    'git provenance read': {
        'reason': 'reads repository metadata; runs no tool on a candidate and produces no evidence',
        'tool': 'git',
        'sites': {
            ('src/agent_ee/candidate.py', 'source_manifest'),
            ('src/agent_ee/source_authority.py', 'git'),
            ('src/agent_ee/reference_blocks.py', '_git'),
            ('src/agent_ee/generate.py', 'tool_identity'),
            ('src/agent_ee/generate.py', 'git'),
            ('src/agent_ee/rung1_campaign.py', 'read_only_git'),
            # Not named in the 140c7a36 list; same purpose (implementation/manifest provenance).
            ('src/agent_ee/benchmark_run.py', 'git_output'),
            ('src/agent_ee/benchmark_run.py', 'manifest_store'),
            ('src/agent_ee/benchmark_run.py', 'committed_manifest'),
            ('src/agent_ee/benchmark_run.py', 'archive_implementation'),
            ('src/agent_ee/benchmark_run.py', 'implementation'),
        },
    },
    'network acquisition': {
        'reason': 'fetches under its own network and isolation rules; not a tool run on candidate inputs',
        'tool': None,
        'sites': {('src/agent_ee/datasheet_capture.py', 'run_owned_child'),
                  ('src/agent_ee/catalog_feed.py', 'acquire_catalog')},
    },
    'operator diagnostics': {
        'reason': 'agent-ee doctor host probes with short timeouts; produce no evidence',
        'tool': None,
        'sites': {('src/agent_ee/cli.py', 'doctor')},
    },
}

# The approved table, restated in full (tool and every (file, function) site): changing or widening
# EXEMPT needs this deliberate second edit.
APPROVED = {
    'in-sandbox router script': (None, {('src/agent_ee/kicad_route_attempt.py', ANY)}),
    'git provenance read': ('git', {
        ('src/agent_ee/candidate.py', 'source_manifest'),
        ('src/agent_ee/source_authority.py', 'git'),
        ('src/agent_ee/reference_blocks.py', '_git'),
        ('src/agent_ee/generate.py', 'tool_identity'),
        ('src/agent_ee/generate.py', 'git'),
        ('src/agent_ee/rung1_campaign.py', 'read_only_git'),
        ('src/agent_ee/benchmark_run.py', 'git_output'),
        ('src/agent_ee/benchmark_run.py', 'manifest_store'),
        ('src/agent_ee/benchmark_run.py', 'committed_manifest'),
        ('src/agent_ee/benchmark_run.py', 'archive_implementation'),
        ('src/agent_ee/benchmark_run.py', 'implementation'),
    }),
    'network acquisition': (None, {('src/agent_ee/datasheet_capture.py', 'run_owned_child'),
                                   ('src/agent_ee/catalog_feed.py', 'acquire_catalog')}),
    'operator diagnostics': (None, {('src/agent_ee/cli.py', 'doctor')}),
}

SUBPROCESS = {'run', 'Popen', 'call', 'check_call', 'check_output'}
OS_PROCESS = ('system', 'exec', 'spawn', 'posix_spawn', 'fork')
EXECUTOR = {'execute', 'sandbox_argv'}


MODULES = {'subprocess': 'subprocess', 'os': 'os', 'executor': 'executor', 'agent_ee.executor': 'executor'}
SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _process(module, name):
    """True when module.name starts a process (or is the sandbox engine's entry point)."""
    return (module == 'subprocess' and name in SUBPROCESS or module == 'os' and name.startswith(OS_PROCESS)
            or module == 'executor' and name in EXECUTOR)


def _import_source(node):
    """The real module a `from ... import` reads: relative `.executor` is agent_ee.executor."""
    source = node.module or ''
    return 'agent_ee.executor' if node.level and source == 'executor' else source


def _scope_bindings(scope):
    """{name: [binding, ...]} for the statements directly in one scope (nested scopes bind only their
    own name here). A binding is ('module', dotted), ('function', (module, name)), ('expr', node) for an
    assignment whose value may carry an alias, or ('other',)."""
    found = {}

    def bind(name, binding):
        found.setdefault(name, []).append(binding)

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bind(child.name, ('other',))
                continue
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.asname:
                        bind(alias.asname, ('module', alias.name))
                    else:
                        head = alias.name.split('.')[0]
                        bind(head, ('module', head))
            elif isinstance(child, ast.ImportFrom):
                source = _import_source(child)
                for alias in child.names:
                    local = alias.asname or alias.name
                    if alias.name == '*':
                        continue
                    if MODULES.get(source) and _process(MODULES[source], alias.name):
                        bind(local, ('function', (MODULES[source], alias.name)))
                    elif alias.name == 'executor' and (child.level or source == 'agent_ee'):
                        bind(local, ('module', 'agent_ee.executor'))
                    else:
                        bind(local, ('other',))
            elif isinstance(child, ast.Assign) and len(child.targets) == 1 and isinstance(child.targets[0], ast.Name):
                bind(child.targets[0].id, ('expr', child.value))
                walk(child.value)
                continue
            elif isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                bind(child.id, ('other',))
            walk(child)

    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = scope.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
            if arg is not None:
                bind(arg.arg, ('other',))
    walk(scope)
    return found


def _literal_tool(call):
    if call.args and isinstance(call.args[0], (ast.List, ast.Tuple)) and call.args[0].elts:
        first = call.args[0].elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value.rsplit('/', 1)[-1]
    return None


class _Sites(ast.NodeVisitor):
    """Every reference to a process function (called or not), each counted once, with names resolved
    per scope: module, then each enclosing function, innermost binding first (class bodies are seen
    only from their own body, as in Python). A name bound in a scope to anything else shadows outer
    bindings; a name no scope binds is taken as the module of that name (subprocess, os, executor)."""

    def __init__(self):
        self.scopes, self.functions, self.found, self.seen = [], [], [], set()

    # --- resolution ---------------------------------------------------------------------------
    def _chain(self, upto):
        """Scope indexes visible from scope `upto`, innermost first (class scopes only as the innermost)."""
        for index in range(upto, -1, -1):
            if index != upto and isinstance(self.scopes[index][0], ast.ClassDef):
                continue
            yield index

    def _meanings(self, name, upto=None, active=frozenset()):
        """What a name can denote here: {('module', dotted)} and {('function', (module, name))}."""
        upto = len(self.scopes) - 1 if upto is None else upto
        for index in self._chain(upto):
            bindings = self.scopes[index][1].get(name)
            if bindings is None:
                continue
            meanings = set()
            for binding in bindings:
                if binding[0] in ('module', 'function'):
                    meanings.add(binding)
                elif binding[0] == 'expr' and (name, index) not in active:
                    meanings |= self._denotes(binding[1], index, active | {(name, index)})
            return meanings
        return {('module', name)} if name in MODULES else set()

    def _denotes(self, node, upto, active=frozenset()):
        """Meanings of an expression evaluated in scope `upto` (names, dotted attributes)."""
        if isinstance(node, ast.Name):
            return self._meanings(node.id, upto, active)
        if isinstance(node, ast.Attribute):
            meanings = set()
            for kind, value in self._denotes(node.value, upto, active):
                if kind == 'module':
                    canonical = MODULES.get(value)
                    if canonical and _process(canonical, node.attr):
                        meanings.add(('function', (canonical, node.attr)))
                    meanings.add(('module', f'{value}.{node.attr}'))
            return meanings
        return set()

    def _owner(self, node):
        """The process module (canonical) an expression denotes, if any."""
        for kind, value in self._denotes(node, len(self.scopes) - 1):
            if kind == 'module' and MODULES.get(value):
                return MODULES[value]
        return None

    def _target(self, node):
        """'module.name' / 'module name' when node names a process function, else None."""
        if isinstance(node, ast.Attribute):
            owner = self._owner(node.value)
            if owner and _process(owner, node.attr):
                return f'{owner}.{node.attr}'
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            for kind, value in self._meanings(node.id):
                if kind == 'function':
                    return '{} {}'.format(*value)
        return None

    # --- traversal ----------------------------------------------------------------------------
    def _record(self, node, what, tool=None):
        self.found.append((node.lineno, what, self.functions[-1] if self.functions else None, tool))

    def _scope(self, node):
        self.scopes.append((node, _scope_bindings(node)))
        is_function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if is_function:
            self.functions.append(node.name)
        self.generic_visit(node)
        if is_function:
            self.functions.pop()
        self.scopes.pop()

    visit_Module = visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def visit_ImportFrom(self, node):
        source = MODULES.get(_import_source(node))
        if source and any(alias.name == '*' for alias in node.names):
            self._record(node, f'from {source} import *')

    def visit_Call(self, node):
        what = self._target(node.func)
        if what:
            self.seen.add(id(node.func))
            self._record(node, what, _literal_tool(node))
        elif (isinstance(node.func, ast.Name) and node.func.id == 'getattr' and len(node.args) >= 2
              and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            owner = self._owner(node.args[0])
            if owner and _process(owner, node.args[1].value):
                self._record(node, f'getattr {owner}.{node.args[1].value}')
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if id(node) not in self.seen:
            what = self._target(node)
            if what:
                self._record(node, what)
                return
        self.generic_visit(node)

    def visit_Name(self, node):
        if id(node) not in self.seen:
            what = self._target(node)
            if what:
                self._record(node, what)


def direct_sites(text):
    """(line, call, innermost function, literal tool) of every direct site in one module's source."""
    visitor = _Sites()
    visitor.visit(ast.parse(text))
    return sorted(visitor.found, key=lambda site: site[:2])


def argv_builds(text):
    """(line, tool) of each literal kicad-cli DRC or netlist-export argv in one module's source."""
    builds = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.List, ast.Tuple)):
            words = [e.value if isinstance(e, ast.Constant) else None for e in node.elts]
            for tool, pair in (('drc', ['pcb', 'drc']), ('netlist', ['export', 'netlist'])):
                if any(words[i:i + 2] == pair for i in range(len(words) - 1)):
                    builds.append((node.lineno, tool))
    return builds


def exemption(rel, site):
    """The EXEMPT class covering site in file rel, or None."""
    _, _, function, tool = site
    for name, entry in EXEMPT.items():
        if ((rel, ANY) in entry['sites'] or (rel, function) in entry['sites']) and \
                (entry['tool'] is None or entry['tool'] == tool):
            return name
    return None


def inventory():
    """{file: [unexempted direct sites]} and the set of (class, file, function) exemptions used."""
    found, used = {}, set()
    for path in sorted(SOURCE.rglob('*.py')):
        rel = path.relative_to(ROOT).as_posix()
        if rel in ENGINE:
            continue
        for site in direct_sites(path.read_text()):
            name = exemption(rel, site)
            if name is None:
                found.setdefault(rel, []).append(site)
            else:
                entry = EXEMPT[name]['sites']
                used.add((name, rel, ANY if (rel, ANY) in entry else site[2]))
    return found, used


class NativeRunGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found, cls.used = inventory()

    def test_no_direct_native_sites(self):
        problems = [f'{rel}: line {line} {what} in {function}'
                    for rel, sites in self.found.items() for line, what, function, _ in sites]
        self.assertEqual(problems, [], 'route these through agent_ee.native_run.run')

    def test_drc_and_netlist_argv_are_built_only_by_native_run(self):
        builds = [f'{path.relative_to(ROOT).as_posix()}: line {line} {tool}'
                  for path in sorted(SOURCE.rglob('*.py')) if path.name != 'native_run.py'
                  for line, tool in argv_builds(path.read_text())]
        self.assertEqual(builds, [], 'use native_run.drc_argv / native_run.netlist_argv')
        self.assertEqual(argv_builds("a = ['kicad-cli', 'pcb', 'drc', '-o', r]\nb = (cli, 'sch', 'export', 'netlist')"),
                         [(1, 'drc'), (2, 'netlist')])

    def test_exemption_table_is_exactly_the_approved_one(self):
        # Tool and every (file, function) site: dropping the git restriction or adding a site fails.
        self.assertEqual({name: (entry['tool'], entry['sites']) for name, entry in EXEMPT.items()}, APPROVED)
        for name, entry in EXEMPT.items():
            self.assertTrue(entry['reason'], name)

    def test_every_exemption_still_matches_a_site(self):
        listed = {(name, rel, function) for name, entry in EXEMPT.items() for rel, function in entry['sites']}
        self.assertEqual(listed - self.used, set(), 'remove exemptions that no longer match')

    def test_router_launch_is_not_exempt(self):
        # The routing step's argv (python -I -B <adapter script>) is executed by stage receipts; that
        # outer launch must go through native_run, so no exemption may cover the launcher.
        launcher = 'src/agent_ee/stage_receipts.py'
        self.assertFalse(any(rel == launcher for entry in EXEMPT.values() for rel, _ in entry['sites']))
        self.assertEqual(self.found.get(launcher, []), [])

    def test_scanner_finds_each_direct_form(self):
        text = '''
import os, subprocess
from .executor import execute as run_tool, sandbox_argv
from . import executor
def outer():
    def inner():
        subprocess.run(['/usr/bin/git', 'status'])
    subprocess.run(['kicad-cli', 'version'])
subprocess.Popen(argv)
os.execvp('bwrap', [])
executor.execute(a, b, c)
run_tool(a, b, c)
sandbox_argv(a, b, c)
native_run.run(intent, work=w)
'''
        self.assertEqual([(what, function, tool) for _, what, function, tool in direct_sites(text)],
                         [('subprocess.run', 'inner', 'git'), ('subprocess.run', 'outer', 'kicad-cli'),
                          ('subprocess.Popen', None, None), ('os.execvp', None, None),
                          ('executor.execute', None, None), ('executor execute', None, None),
                          ('executor sandbox_argv', None, None)])

    def test_scanner_resolves_aliases_and_getattr(self):
        # Contract review 0df8d994 finding 1: each of these is a direct site.
        for text, what in (
                ('import subprocess as sp\nsp.run(["kicad-cli"])', 'subprocess.run'),
                ('from subprocess import Popen\nPopen(["kicad-cli"])', 'subprocess Popen'),
                ('from . import executor as e\ne.execute(a, b, c)', 'executor.execute'),
                ('import subprocess\ngetattr(subprocess, "run")(["kicad-cli"])', 'getattr subprocess.run'),
                ('import os as operating\noperating.system("kicad-cli")', 'os.system'),
                ('from subprocess import run as r\nr(["x"])', 'subprocess run'),
                ('import agent_ee.executor\nagent_ee.executor.sandbox_argv(a, b, c)', 'executor.sandbox_argv'),
                ('import agent_ee.executor as ex\nex.execute(a, b, c)', 'executor.execute'),
                ('from agent_ee import executor\nexecutor.execute(a, b, c)', 'executor.execute'),
                ('import subprocess\nsp = subprocess\nsp.run(["x"])', 'subprocess.run'),
                ('from subprocess import *\nrun(["x"])', 'from subprocess import *')):
            with self.subTest(text=text):
                self.assertEqual([site[1] for site in direct_sites(text)], [what])
        # An alias by assignment is a site where it is taken and where it is called.
        self.assertEqual([site[1:3] for site in direct_sites(
            'import subprocess\ndef f():\n    launch = subprocess.Popen\n    launch(["x"])')],
            [('subprocess.Popen', 'f'), ('subprocess Popen', 'f')])

    def test_scanner_resolves_names_per_scope(self):
        # Contract review 87157d72: one function's import must not shadow another's, and a local
        # definition shadows an outer import (innermost binding wins).
        sites = direct_sites('def f():\n    import subprocess as sp\n    sp.run(["x"])\n'
                             'def g():\n    import math as sp\n    sp.sqrt(4)\n')
        self.assertEqual([site[1:3] for site in sites], [('subprocess.run', 'f')])
        self.assertEqual(direct_sites('from subprocess import run\ndef f():\n    def run():\n        pass\n'
                                      '    run()\n'), [])
        self.assertEqual(direct_sites('import subprocess\ndef f(subprocess):\n    subprocess.run(["x"])\n'), [])
        self.assertEqual([site[1:3] for site in direct_sites(
            'import subprocess as sp\ndef f():\n    sp.run(["x"])\nclass C:\n    import math as sp\n'
            '    def m(self):\n        sp.run(["y"])\n')], [('subprocess.run', 'f'), ('subprocess.run', 'm')])
        # Not process starts: other modules' run, a local name that merely shadows one, os.path.
        for text in ('import asyncio\nasyncio.run(main())', 'import os\nos.path.join("a")',
                     'from . import native_run\nnative_run.run(intent, work=w)', 'def run(): pass\nrun()'):
            with self.subTest(text=text):
                self.assertEqual(direct_sites(text), [])

    def test_exemptions_match_function_and_tool_not_whole_files(self):
        git_read = (1, 'subprocess.run', 'tool_identity', 'git')
        probe = (2, 'subprocess.run', 'tool_identity', 'python3')
        self.assertEqual(exemption('src/agent_ee/generate.py', git_read), 'git provenance read')
        self.assertIsNone(exemption('src/agent_ee/generate.py', probe))
        self.assertIsNone(exemption('src/agent_ee/cli.py', (3, 'subprocess.run', 'pcbnew_extension', None)))
        self.assertEqual(exemption('src/agent_ee/cli.py', (4, 'subprocess.run', 'doctor', None)),
                         'operator diagnostics')
        self.assertIsNone(exemption('src/agent_ee/placement_job.py', (5, 'subprocess.run', 'git', 'git')))


if __name__ == '__main__':
    unittest.main()
