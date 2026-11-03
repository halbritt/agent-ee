"""Confined stage child execution, unified StageContext, and KiCad project staging.

An outer Bubblewrap sandbox (such as stage_receipts.run_step) already owns PID,
IPC, UTS and network namespaces, read-only system mounts, and private scratch.
Child tools executing within that boundary must never launch nested bwrap
sandboxes. Instead, supervised execution within outer confinement must use
native_run.Host() with verified scratch paths, clean child environment, and
pre/post file_tree checks verifying input immutability.
"""
import enum
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import replace
import pathlib
from pathlib import Path

from .checks import CheckError
from .executor import SANDBOX_PATH
from .stage_receipts import file_tree
from .storage import digest, read_json


from .forward_annotation import AnnotationError


class ConfinementError(AnnotationError):
    """Raised when stage confinement inspection or verification fails."""
    def __init__(self, message, code='stage_confinement', location='/'):
        super().__init__(code, message, location)


class _ConfinementViewMeta(enum.EnumType):
    """Allow ConfinementView() with 0 arguments to detect the active outer view."""

    def __call__(cls, value=None, *args, **kwargs):
        if value is None:
            return cls.detect()
        if isinstance(value, cls):
            return value
        return super().__call__(value, *args, **kwargs)


class ConfinementView(enum.Enum, metaclass=_ConfinementViewMeta):
    """The recognized outer container views enclosing stage tool execution."""

    STAGE_TOOL = '/tool/agent-ee'
    CHECK_APP = '/app'
    PLACEMENT_TOOL = '/placement-tool'
    ARTWORK_TOOL = '/artwork-tool'

    @property
    def mounts(self):
        return self.parse_mountinfo()

    @property
    def confined(self):
        try:
            return self.verify()
        except ValueError:
            return False

    @classmethod
    def parse_mountinfo(cls, text=None):
        """Parse and strictly validate /proc/self/mountinfo or provided text."""
        try:
            raw = text if text is not None else Path('/proc/self/mountinfo').read_text()
            mounts, ids = {}, set()
            for line in raw.splitlines():
                if not line or ' - ' not in line:
                    raise ValueError('malformed mountinfo')
                view, filesystem = line.split(' - ')
                fields, native = view.split(), filesystem.split()
                if (len(fields) < 6 or len(native) != 3
                        or not fields[0].isdigit() or int(fields[0]) <= 0
                        or str(int(fields[0])) != fields[0] or fields[0] in ids
                        or not fields[1].isdigit() or str(int(fields[1])) != fields[1]
                        or len(fields[2].split(':')) != 2
                        or any(not n.isdigit() for n in fields[2].split(':'))
                        or not fields[4].startswith('/') or '\\' in fields[4]
                        or str(pathlib.PurePosixPath(fields[4])) != fields[4]
                        or '..' in pathlib.PurePosixPath(fields[4]).parts):
                    raise ValueError('malformed mountinfo')
                flags = set(fields[5].split(','))
                if len(flags & {'ro', 'rw'}) != 1:
                    raise ValueError('ambiguous mount permissions')
                ids.add(fields[0])
                mounts[fields[4]] = (flags, native[0])
            return mounts
        except (OSError, ValueError, IndexError) as exc:
            raise ConfinementError('stage confinement: malformed or unavailable mountinfo') from exc

    def verify(self, mountinfo_text=None, source_path=None, *, _fab_templates=False, _pod_plan=False):
        """Validate confinement: tmpfs root, /proc, /dev, /tmp, read-only system mounts."""
        mounts = self.parse_mountinfo(mountinfo_text)
        special = {'/': 'tmpfs', '/proc': 'proc', '/dev': 'tmpfs', '/tmp': 'tmpfs',
                   '/dev/pts': 'devpts', '/dev/shm': 'tmpfs', '/dev/mqueue': 'mqueue'}
        devices = {'/dev/' + name for name in ('null', 'zero', 'full', 'random', 'urandom', 'tty')}
        required = {'/candidate': 'ro', '/work': 'rw', '/usr': 'ro', '/etc': 'ro'}
        readonly = ['/candidate', '/usr', '/etc', '/lib', '/lib64', '/bin', '/sbin']

        if self == ConfinementView.STAGE_TOOL:
            required['/tool/agent-ee'] = 'ro'
            readonly += ['/tool/agent-ee', '/app']
            if _pod_plan:
                required['/tool/plan'] = 'ro'
                readonly += ['/tool/plan']
            if _fab_templates:
                required['/tool/fixtures/fab-export'] = 'ro'
                readonly += ['/tool/fixtures/fab-export']
        elif self == ConfinementView.CHECK_APP:
            required['/app'] = 'ro'
            readonly += ['/app', '/evidence']
        elif self == ConfinementView.PLACEMENT_TOOL:
            required['/placement-tool'] = 'ro'
            required['/placement-request'] = 'ro'
            readonly += ['/placement-tool', '/placement-request', '/app']
        elif self == ConfinementView.ARTWORK_TOOL:
            required['/artwork-tool'] = 'ro'
            readonly += ['/artwork-tool', '/app']

        def supported(path, flags, filesystem):
            if path in special:
                return filesystem == special[path]
            if path in devices:
                return filesystem in ('tmpfs', 'devtmpfs')
            ppath = pathlib.PurePosixPath(path)
            if ppath.is_relative_to('/proc'):
                return filesystem == 'proc'
            if ppath.is_relative_to('/work'):
                return True
            return any(ppath.is_relative_to(root) for root in readonly) and 'ro' in flags

        if (any(path not in mounts for path in ('/', '/proc', '/dev', '/tmp'))
                or any(mode not in mounts.get(path, (set(), None))[0] for path, mode in required.items())
                or any(not supported(path, flags, filesystem) for path, (flags, filesystem) in mounts.items())):
            raise ConfinementError('stage confinement: fixed executor sandbox required')

        if source_path is None:
            fa_mod = sys.modules.get('agent_ee.forward_annotation')
            if fa_mod and hasattr(fa_mod, '__file__') and fa_mod.__file__ and fa_mod.__file__.startswith('/tool/'):
                source = pathlib.PurePath(fa_mod.__file__)
            else:
                source = pathlib.Path(__file__).resolve()
        else:
            source = pathlib.PurePath(source_path)
        if source.is_absolute() and not source.is_relative_to(self.value):
            raise ConfinementError(f'stage confinement: source {source} does not match active tool mount {self.value}')
        return True

    @classmethod
    def detect(cls, mountinfo_text=None, source_path=None):
        """Detect the active confinement view based on source path and mountinfo."""
        source = Path(__file__).resolve() if source_path is None else Path(source_path).resolve()
        for member in cls:
            if source.is_relative_to(member.value):
                member.verify(mountinfo_text=mountinfo_text, source_path=source)
                return member
        try:
            mounts = cls.parse_mountinfo(mountinfo_text)
            for member in cls:
                if member.value in mounts and 'ro' in mounts[member.value][0]:
                    member.verify(mountinfo_text=mountinfo_text, source_path=member.value)
                    return member
        except ValueError:
            pass
        return cls.STAGE_TOOL

    @classmethod
    def is_confined(cls, source_path=None):
        """Return True if executing within a valid recognized outer container view."""
        source = Path(__file__).resolve() if source_path is None else Path(source_path).resolve()
        for member in cls:
            if source.is_relative_to(member.value):
                try:
                    member.verify(source_path=source)
                    return True
                except ValueError:
                    return False
        try:
            mounts = cls.parse_mountinfo()
            for member in cls:
                if member.value in mounts and 'ro' in mounts[member.value][0]:
                    try:
                        member.verify(source_path=member.value)
                        return True
                    except ValueError:
                        pass
        except Exception:
            pass
        return False

@contextmanager
def stage_kicad_project(inputs, work, design_rel, suffixes=('.kicad_pro', '.kicad_prl', '.kicad_dru'), *, target_name='project', project_dir=None, error_type=None):
    """Context manager providing a private copy of the project in scratch.

    Verifies input immutability before and after child execution, and guarantees
    that only <stem>.kicad_prl was modified or added in the private project.
    """
    inputs, work = Path(inputs).resolve(), Path(work).resolve()
    design = Path(design_rel)
    project = Path(project_dir).resolve() if project_dir is not None else work / target_name

    def _fail(msg):
        if error_type is not None:
            if isinstance(error_type, type) and issubclass(error_type, CheckError):
                raise error_type('malformed', msg)
            raise error_type(msg)
        raise ValueError(msg)

    if design.is_absolute() or '..' in design.parts or not str(design_rel).strip():
        _fail('design path must be relative to project root without parent references')

    before, other = file_tree(inputs)
    if other:
        _fail('child inputs must be regular captured files')

    owned_prefix = None
    if work.is_relative_to(inputs):
        owned_prefix = work.relative_to(inputs).as_posix()
        before = {k: v for k, v in before.items() if not k.startswith(owned_prefix + '/') and k != owned_prefix}

    project.mkdir(parents=True, exist_ok=True)
    for rel in before:
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inputs / rel, target, follow_symlinks=False)

    copied, other = file_tree(project)
    if other or copied != before:
        _fail('private project copy changed')

    preferences = design.with_suffix('.kicad_prl').as_posix()
    try:
        yield project
    finally:
        after_in, other_in = file_tree(inputs)
        if owned_prefix:
            after_in = {k: v for k, v in after_in.items() if not k.startswith(owned_prefix + '/') and k != owned_prefix}
            other_in = [k for k in other_in if not k.startswith(owned_prefix + '/') and k != owned_prefix]
        if other_in or after_in != before:
            _fail('child changed its captured inputs (changed its private inputs)')
        after_proj, other_proj = file_tree(project)
        pref = project / preferences
        if (other_proj or (pref.exists() and not pref.is_file())
                or (preferences in copied and preferences not in after_proj)
                or {k: v for k, v in after_proj.items() if k != preferences} !=
                   {k: v for k, v in copied.items() if k != preferences}):
            _fail('child changed its private project')

class StageContext:
    """Unified adapter managing stage child execution within outer container confinement."""

    def __init__(self, view=None, work=None, *, _fab_templates=False, _pod_plan=False):
        if isinstance(view, (pathlib.PurePath, os.PathLike)) or (isinstance(view, str) and not any(view == m.value for m in ConfinementView)):
            work, view = view, None
        explicit = view is not None
        self.view = view if isinstance(view, ConfinementView) else (
            ConfinementView(view) if explicit else ConfinementView.detect()
        )
        self.scratch = Path(work).resolve() if work is not None else Path('/work/out')
        self.work = self.scratch
        self.stage = self
        self._fab_templates = _fab_templates
        if explicit or _pod_plan or ConfinementView.is_confined():
            self.view.verify(_fab_templates=self._fab_templates, _pod_plan=_pod_plan)

    def environment(self, inputs, work):
        """Construct the isolated child environment for KiCad and Python."""
        inputs, work = Path(inputs).resolve(), Path(work).resolve()
        pythonpath = self.view.value if hasattr(self.view, 'value') else '/tool/agent-ee'
        return {
            **os.environ,
            'PATH': SANDBOX_PATH,
            'LANG': 'C.UTF-8',
            'PYTHONPATH': pythonpath,
            'HOME': str(work / 'home'),
            'KICAD_CONFIG_HOME': str(work / 'kicad-config'),
            'KICAD10_SYMBOL_DIR': str(inputs / 'stock/symbols'),
            'KICAD10_FOOTPRINT_DIR': str(inputs / 'stock/footprints'),
            'KICAD10_TEMPLATE_DIR': str(inputs / 'stock/template'),
        }

    def run(self, intent, *, inputs=None, work=None, timeout=None, mounts=(), cancel=None,
            on_line=None, on_launch=None, on_tick=None, **callbacks):
        """Execute a child process within outer confinement using native_run.Host()."""
        from . import native_run
        from .checks import CheckError
        from .datasheet_facts import EXTRACTOR

        started = time.monotonic()
        if self.scratch == Path('/work/out/rating-citations') or (intent.argv and intent.argv[0] == EXTRACTOR):
            if (intent.tool != 'probe' or intent.isolation != 'sandbox' or intent.network
                    or intent.stdin is not None or intent.env is not None or intent.cwd is not None
                    or intent.remount_ro or intent.log_bytes is not None or intent.skip_visuals is not None
                    or intent.stop_grace_s is not None or intent.merge_stderr or mounts or callbacks
                    or any(v is not None for v in (on_line, on_launch, on_tick))):
                raise ValueError('unsupported binding-fill rating child intent')
            inputs_p = Path(inputs) if inputs is not None else None
            work_p = Path(work) if work is not None else None
            scratch = Path('/work/out/rating-citations')
            if (inputs_p != Path('/candidate') or inputs_p.resolve() != inputs_p
                    or work_p is None or work_p.parent != scratch or work_p.resolve() != work_p
                    or scratch.resolve() != scratch):
                raise ValueError('rating child requires fixed candidate and citation scratch')
            argv = intent.argv
            captured = None
            if argv == (EXTRACTOR, '-v') and work_p.name == 'version':
                concrete = argv
            elif (len(argv) == 4 and argv[:2] == (EXTRACTOR, '-layout')
                    and argv[2].startswith('/candidate/') and argv[3] == '/work/document.txt'
                    and re.fullmatch(r'pdf-(0|[1-9][0-9]*)', work_p.name)):
                from .parts_manifest import read_captured, _path
                captured = _path(argv[2].removeprefix('/candidate/'))
                before = hashlib.sha256(read_captured(inputs_p, captured)).hexdigest()
                concrete = (EXTRACTOR, '-layout', str(inputs_p / captured), str(work_p / 'document.txt'))
            else:
                raise ValueError('unsupported binding-fill pdftotext argv or work path')
            env = self.environment(inputs_p, work_p)
            env.update(PATH=SANDBOX_PATH, LANG='C.UTF-8')
            try:
                remaining = timeout
                if timeout is not None:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= native_run.LAUNCH_FLOOR_S:
                        raise native_run.DeadlineExhausted('rating child setup exhausted its deadline')
                child = replace(intent, argv=concrete, timeout=remaining, isolation='host', env=env, cwd=str(work_p))
                return native_run.Host().run(child, inputs=None, work=work_p, timeout=remaining,
                                             mounts=(), cancel=cancel)
            finally:
                if captured is not None:
                    from .parts_manifest import read_captured
                    if hashlib.sha256(read_captured(inputs_p, captured)).hexdigest() != before:
                        raise ValueError('rating child changed its captured PDF')

        if self._fab_templates:
            if intent.tool == 'source_revision':
                return native_run.Host().run(intent, inputs=inputs, work=work, timeout=timeout, mounts=(), cancel=cancel)
            if (intent.isolation != 'sandbox' or intent.network or intent.remount_ro
                    or intent.log_bytes is not None or callbacks
                    or any(v is not None for v in (on_line, on_launch, on_tick))):
                raise ValueError('unsupported receipted fab child intent')
            if inputs is not None and work is not None:
                if (not Path(inputs).resolve().is_relative_to(self.scratch)
                        or not Path(work).resolve().is_relative_to(self.scratch)):
                    raise ValueError('fab child paths must stay in stage scratch')
        else:
            if (intent.network or intent.remount_ro or intent.log_bytes is not None or callbacks
                    or any(v is not None for v in (on_line, on_launch, on_tick))):
                raise ValueError('unsupported stage child intent')

        work = self.scratch if work is None else Path(work).resolve()
        inputs = Path(inputs).resolve() if inputs is not None else None

        baseline = None
        protected = [inputs] if inputs is not None else []
        for source, target in mounts:
            source = Path(source).resolve()
            if target != native_run.BASELINE_AT or (self._fab_templates and not source.is_relative_to(self.scratch)) or baseline is not None:
                raise ValueError('unsupported receipted fab child mount' if self._fab_templates else 'unsupported child mount')
            baseline = source
            protected.append(source)

        def identity():
            records = []
            for path in protected:
                if path == inputs:
                    files, other = file_tree(path)
                    if other:
                        raise CheckError('malformed', 'child inputs must be regular captured files')
                    records.append(files)
                else:
                    records.append(digest(path))
            return records

        before = identity()
        child_inputs = inputs
        project, copied, preferences = None, None, None

        design_suffix = None
        if intent.argv[:3] == ('kicad-cli', 'pcb', 'export'):
            design_suffix = '.kicad_pcb'
        elif (intent.argv[:3] == ('kicad-cli', 'sch', 'export')
              and intent.argv[3:4] in (('netlist',), ('bom',))):
            design_suffix = '.kicad_sch'

        if design_suffix is not None and inputs is not None:
            design = Path(intent.argv[-1].removeprefix('/candidate/'))
            if (not intent.argv[-1].startswith('/candidate/') or design.suffix != design_suffix
                    or not (inputs / design).resolve().is_relative_to(inputs)
                    or design.as_posix() not in before[0]):
                raise ValueError('fab export must name its captured design file')
            project = work / 'project'
            shutil.copytree(inputs, project, symlinks=True)
            copied, other = file_tree(project)
            if other or copied != before[0]:
                raise CheckError('malformed', 'fab private project copy changed')
            child_inputs = project
            preferences = design.with_suffix('.kicad_prl').as_posix()

        def _arg(arg):
            if arg == native_run.BASELINE_AT:
                if baseline is None:
                    raise ValueError('fab child baseline was not captured')
                return str(baseline)
            for virtual, actual in (('/candidate', child_inputs), ('/work', work)):
                if actual is not None and (arg == virtual or arg.startswith(virtual + '/')):
                    path = actual / arg.removeprefix(virtual).lstrip('/')
                    if not path.resolve().is_relative_to(actual):
                        raise ValueError('fab child path escapes its private directory' if self._fab_templates else 'stage child path escapes its private directory')
                    return str(path)
            return arg
        concrete = tuple(_arg(a) for a in intent.argv)

        env = self.environment(inputs if inputs is not None else work, work)
        if intent.env:
            env.update(intent.env)

        remaining = timeout
        if timeout is not None:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= native_run.LAUNCH_FLOOR_S:
                raise native_run.DeadlineExhausted('stage child setup exhausted its deadline')

        child = replace(intent, argv=concrete, timeout=remaining, isolation='host', env=env, cwd=str(work))
        try:
            return native_run.Host().run(child, inputs=None, work=work, timeout=remaining,
                                         mounts=(), cancel=cancel)
        finally:
            if before and identity() != before:
                raise CheckError('malformed', 'fab child changed its captured inputs')
            if project is not None:
                after, other = file_tree(project)
                pref = project / preferences
                if (other or (pref.exists() and not pref.is_file())
                        or (preferences in copied and preferences not in after)
                        or {k: v for k, v in after.items() if k != preferences} !=
                           {k: v for k, v in copied.items() if k != preferences}):
                    raise CheckError('malformed', 'fab child changed its private project')

    def probe(self, inputs, work, argv, timeout=120):
        """Execute a supervised in-stage child process via Host."""
        from . import native_run
        inputs, work = Path(inputs).resolve(), Path(work).resolve()
        from .forward_annotation import AnnotationError
        if not inputs.is_relative_to(self.scratch) or not work.is_relative_to(self.scratch):
            raise AnnotationError('stage_confinement', 'stage child paths must stay in stage scratch')
        before, other = file_tree(inputs)
        if other:
            raise AnnotationError('unsafe_path', 'stage child inputs must be regular captured files')
        record = native_run.run(native_run.Intent('pcbnew_script',
            ['/usr/bin/python3', '-B', '-P', *argv], timeout=timeout, isolation='host',
            env=self.environment(inputs, work)), work=work)
        after, other = file_tree(inputs)
        if other or after != before:
            raise AnnotationError('stale_input', 'stage child changed its private inputs')
        return dict(record)

    _probe = probe

    def child(self, inputs, work, argv, timeout=120):
        """Translate virtual /candidate/ and /work/ paths and run forward annotation probe."""
        concrete = []
        for arg in argv:
            if arg.startswith('/candidate/'):
                arg = str(Path(inputs) / arg.removeprefix('/candidate/'))
            elif arg.startswith('/work/'):
                arg = str(Path(work) / arg.removeprefix('/work/'))
            concrete.append(arg)
        return self.probe(inputs, work, ['-m', 'agent_ee.forward_annotation_probe', *concrete], timeout)

    def custom_geometry(self, data, footprint):
        """Execute supervised custom-pad probe and parse footprint geometry."""
        from .footprint_library import FootprintError
        parent = self.scratch / 'library-probes'
        parent.mkdir(parents=True, exist_ok=True)
        if parent.is_symlink() or parent.resolve() != parent:
            raise ValueError('custom-pad scratch must stay in outer work')
        root = Path(tempfile.mkdtemp(prefix='member-', dir=parent))
        inputs, work = root / 'inputs', root / 'work'
        inputs.mkdir(parents=True, exist_ok=True)
        name = footprint.get('name') if isinstance(footprint, dict) else None
        if not isinstance(name, str) or not name.strip() or '/' in name or '\\' in name or '..' in name:
            raise FootprintError(f'invalid or unsafe footprint name: {name!r}')
        member = inputs / (name + '.kicad_mod')
        if not member.resolve().is_relative_to(inputs.resolve()):
            raise FootprintError('footprint path escapes inputs directory')
        member.write_bytes(data)
        member.chmod(0o400)
        command = ('from agent_ee.footprint_probe import _stage_main; import sys; '
                   '_stage_main(*sys.argv[1:])')
        result = self.probe(inputs, work, ['-c', command, str(inputs), name,
                                           str(work / 'footprint.json')], 60)
        if result['execution'] != 'ok':
            console = (work / 'console.log').read_text(errors='replace')[-2000:] if (work / 'console.log').is_file() else ''
            raise FootprintError('native custom-pad load ' + result['execution'] + ': ' + console)
        report_file = work / 'footprint.json'
        if not report_file.is_file():
            raise FootprintError('native custom-pad report missing')
        try:
            report = json.loads(report_file.read_bytes())
        except Exception as exc:
            raise FootprintError(f'native custom-pad report invalid: {exc}') from exc
        if not isinstance(report, dict):
            raise FootprintError('native custom-pad report must be an object')
        return report
