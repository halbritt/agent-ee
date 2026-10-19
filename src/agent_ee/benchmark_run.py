"""Scratch cache, immutable preparation and checks-only benchmark accounting."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.request

from .benchmark import checked_read, mutate, safe_path, sha
from .findings import CHECK_EXECUTIONS, CHECK_OUTCOMES, safe_id
from .storage import canonical, digest, identity, read_json, write_json

REPOSITORY = Path(__file__).resolve().parents[2]


def external(path, repository=REPOSITORY):
    path = Path(path).absolute()
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError('scratch/cache path traverses symlink')
    if path.resolve().is_relative_to(repository.resolve()):
        raise ValueError('downloaded/generated designs must stay outside repository')
    return path


def fresh(path):
    path = external(path)
    path.mkdir(parents=True, exist_ok=False)
    return path


def load_manifest(path):
    from .hardware_interface import unique_object
    manifest = json.loads(Path(path).read_text(), object_pairs_hook=unique_object)
    if manifest['schema'] != 'agent-ee.seeded-benchmark/1' or manifest['synthetic'] is not True:
        raise ValueError('expected synthetic benchmark manifest')
    boards = manifest['boards']; cases = manifest['cases']
    for items in (boards,cases):
        ids = [safe_id(row['id']) for row in items]
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate benchmark identity')
    board_ids = {b['id'] for b in boards}
    if manifest.get('corpus', 'agentee-21-public') not in MINIMUM_CORPUS:
        raise ValueError('unknown benchmark corpus')
    if not re.fullmatch(r'agent-ee-[a-z0-9-]+-', manifest.get('container_prefix', 'agent-ee-21-')):
        raise ValueError('invalid container prefix')
    for b in boards:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',b['repo']) or not re.fullmatch(r'[a-f0-9]{40}',b['commit']):
            raise ValueError('unpinned upstream source')
        owner = owner_source(b)
        if owner and not re.fullmatch(r'[a-f0-9]{40}', owner['tree']):
            raise ValueError('unpinned owner source tree')
        # An owner repository without a licence file stays scratch-only; it
        # never borrows a hardware grant it does not declare.
        grant = b['license']['hardware_grant'] or (owner and b['license']['hardware_grant'] is None
                                                    and b['license'].get('status'))
        if not grant or b['license']['publication'] != 'not authorized; no repository copy':
            raise ValueError('license/publication declaration required')
        for p,h in b['native_file_sha256'].items():
            safe_path(Path('/unused'),p)
            if not re.fullmatch(r'[a-f0-9]{64}',h):
                raise ValueError('invalid source hash')
    for c in cases:
        if c['board_id'] not in board_ids or not c['expected_checks'] or not c['truth']['requirement']:
            raise ValueError('incomplete expectation')
        if c['status'] != 'structurally-registered-not-executed' or not c['structural_receipt']['structural_only']:
            raise ValueError('unverified structural recipe')
        for check in c['expected_checks']:
            if check['kind'] == 'planned' and (check['expected_execution'] != 'unsupported'
                                               or not re.fullmatch(r'AGENTEE-[0-9]+', check['detector'])):
                raise ValueError('planned detector must be preregistered as unsupported')
        if 'native_visibility' in c and type(c['native_visibility'].get('real_defect')) is not bool:
            raise ValueError('native visibility must be an explicit Boolean')
    return manifest


# Minimum committed (boards, cases) per corpus before any scored execution.
MINIMUM_CORPUS = {'agentee-21-public': (5, 30), 'fome-mc': (1, 6)}


def owner_source(board):
    source = board.get('source')
    if source is None:
        return None
    if source.get('kind') != 'owner_git':
        raise ValueError('unknown benchmark source kind')
    return source


def git_output(repository, *args):
    return subprocess.run(['git', '-C', str(repository), *args], capture_output=True, check=True).stdout


def owner_tree(repository, board, target):
    """Export only pinned paths of one owner commit; the repository is read, never written."""
    repository = Path(repository)
    commit = board['commit']
    if git_output(repository, 'rev-parse', '--verify', commit+'^{commit}').decode().strip() != commit:
        raise ValueError('owner commit identity mismatch')
    if git_output(repository, 'rev-parse', '--verify', commit+'^{tree}').decode().strip() != board['source']['tree']:
        raise ValueError('owner tree identity mismatch')
    paths = sorted({**board['native_file_sha256'], **board['captured_dependency_sha256']})
    archive = target/'source.tar.gz'
    with archive.open('xb') as out:
        out.write(git_output(repository, 'archive', '--format=tar.gz', '--prefix=tree/', commit, '--', *paths))
    extracted = target/'unpacked';extracted.mkdir()
    root = extract_archive(archive, extracted)
    root.rename(target/'tree');extracted.rmdir()
    for rel, expected in {**board['native_file_sha256'], **board['captured_dependency_sha256']}.items():
        if sha(safe_path(target/'tree', rel).read_bytes()) != expected:
            raise ValueError('owner source hash mismatch: '+rel)


def extract_archive(archive, destination):
    """Extract only ordinary files/directories; no tar links/devices/traversal."""
    with tarfile.open(archive,'r:gz') as tar:
        members = tar.getmembers()
        if sum(m.size for m in members)>256*1024*1024 or len(members)>20000:
            raise ValueError('upstream archive too large')
        names = set()
        for member in members:
            safe_path(destination,member.name)
            if member.name in names or not (member.isfile() or member.isdir()):
                raise ValueError('duplicate or unsupported archive member')
            names.add(member.name)
        tar.extractall(destination, members=members, filter='data')
    roots = list(destination.iterdir())
    if len(roots)!=1 or not roots[0].is_dir():
        raise ValueError('expected one upstream archive root')
    return roots[0]


def fetch(manifest_path, cache, *, archives=None, git_sources=None, footprint_library='/usr/share/kicad/footprints/Connector_PinHeader_1.27mm.pretty'):
    manifest = load_manifest(manifest_path)
    cache = fresh(cache)
    for board in manifest['boards']:
        target = cache/board['id'];target.mkdir()
        if owner_source(board):
            if board['id'] not in (git_sources or {}):
                raise ValueError('owner source requires an explicit local repository: '+board['id'])
            owner_tree(git_sources[board['id']], board, target)
            continue
        archive = target/'source.tar.gz'
        if archives:
            source = safe_path(archives,board['repo'].split('/')[-1]+'.tar.gz')
            shutil.copyfile(source,archive)
        else:
            url = f"https://codeload.github.com/{board['repo']}/tar.gz/{board['commit']}"
            with urllib.request.urlopen(url,timeout=60) as response, archive.open('xb') as out:
                size = 0
                while block := response.read(1024*1024):
                    size += len(block)
                    if size>64*1024*1024:
                        raise ValueError('download too large')
                    out.write(block)
        if digest(archive) != board['archive_sha256']:
            raise ValueError('upstream archive hash mismatch')
        extracted = target/'unpacked';extracted.mkdir()
        root = extract_archive(archive,extracted)
        root.rename(target/'tree');extracted.rmdir()
        for rel,expected in board['native_file_sha256'].items():
            checked_read(target/'tree',rel,expected)
        license = board['license']
        data = safe_path(target/'tree',license['path']).read_bytes()
        import hashlib
        blob = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if sha(data)!=license['sha256'] or blob!=license['git_blob_sha']:
            raise ValueError('upstream license hash mismatch')
    alternates = cache/'alternate-footprints';alternates.mkdir()
    boards = {b['id']:b for b in manifest['boards']}
    installed = False
    for case in manifest['cases']:
        m = case['mutation']
        if m['operation'] != 'replace_embedded_physical_footprint':
            continue
        if 'alternate_git' not in m:
            installed = True
            continue
        # An earlier owner revision of the same project supplies the pattern.
        name = Path(m['alternate_source']).name
        board = boards[case['board_id']]
        data = git_output(git_sources[board['id']], 'show', m['alternate_git']['commit']+':'+m['alternate_git']['path'])
        if sha(data)!=m['alternate_sha256'] or not re.fullmatch(r'[a-f0-9]{40}',m['alternate_git']['commit']):
            raise ValueError('owner alternate footprint differs from pin')
        safe_path(alternates,name).write_bytes(data)
    if not installed:
        write_json(cache/'cache.json',{'manifest_sha256':digest(manifest_path),'licenses_overridden':False})
        return cache
    library = read_json(Path(manifest_path).parent/'alternate-library.json')
    license_data = Path('/usr/share/doc/kicad-footprints/LICENSE.md').read_bytes()
    if sha(license_data)!=library['license_sha256']:
        raise ValueError('alternate library license differs from pin')
    (alternates/'LICENSE.md').write_bytes(license_data)
    write_json(alternates/'provenance.json',library)
    for case in manifest['cases']:
        m = case['mutation']
        if m['operation'] == 'replace_embedded_physical_footprint' and 'alternate_git' not in m:
            name = Path(m['alternate_source']).name
            data = safe_path(footprint_library,name).read_bytes()
            if sha(data)!=m['alternate_sha256']:
                raise ValueError('installed alternate footprint differs from pin')
            (alternates/name).write_bytes(data)
    write_json(cache/'cache.json',{'manifest_sha256':digest(manifest_path),'licenses_overridden':False})
    return cache


def contract_for(board, cases):
    contract = {'schema':'agent-ee.interface/1','components':{},'terminals':{},'allowed_edits':[],'invariants':[]}
    for case in cases:
        for check in case['expected_checks']:
            if check['kind']!='hardware_interface':
                continue
            predicates = check['predicate'] if isinstance(check['predicate'],list) else [check['predicate']]
            for i,predicate in enumerate(predicates):
                predicate = copy.deepcopy(predicate)
                def terminal(ref, pin):
                    selector = next(s for s in case['source_selectors'] if s['reference']==ref)
                    # Hierarchical selectors carry the native netlist sheet path.
                    contract['components'][ref] = {'locations':[{'sheet':selector['schematic'].get('native_sheet','/'),
                        'reference':ref}],'symbols':[selector['schematic']['lib_id']]}
                    if pin is None:
                        return ref
                    tid = f'{ref}-{pin}'
                    contract['terminals'][tid] = {'component':ref,'pin':pin}
                    return tid
                if 'terminals' in predicate:
                    predicate['terminals'] = [terminal(t['reference'],t['pin']) for t in predicate['terminals']]
                else:
                    ref = predicate.pop('reference')
                    pin = predicate.pop('pin',None)
                    predicate['component' if pin is None else 'terminal'] = terminal(ref,pin)
                predicate.update(id=f'{case["id"]}-{i}',requirement='assembly',
                    source={'path':'benchmark/requirements.json','locator':board['id']})
                contract['invariants'].append(predicate)
    from .hardware_interface import validate_contract
    return validate_contract(contract)


def prepare(manifest_path, cache, output):
    manifest = load_manifest(manifest_path)
    cache = external(cache)
    if read_json(cache/'cache.json')['manifest_sha256']!=digest(manifest_path):
        raise ValueError('cache belongs to different manifest')
    output = fresh(output)
    receipts = {}
    for board in manifest['boards']:
        source = cache/board['id']/'tree'
        cases = [c for c in manifest['cases'] if c['board_id']==board['id']]
        requirements = {'synthetic':True,'board':board['id'],'requirements':[
            {'id':c['id'],'statement':c['truth']['requirement'],'basis':c['truth']['independent_basis']} for c in cases],
            'limitations':manifest['limitations'],'control_context':board['control_caveats']}
        contract = contract_for(board,cases)
        if identity(contract)!=board['contract_sha256']:
            raise ValueError('interface contract differs from preregistration')
        for case in [None,*cases]:
            cid = case['id'] if case else board['id']+'-control'
            destination = output/cid;destination.mkdir()
            files = set(board['native_file_sha256'])
            # Only the inventoried local dependency files are carried forward.
            # Never synthesize a missing custom library or replace native parts.
            files.update(board['captured_dependency_sha256'])
            hashes = {**board['native_file_sha256'],**board['captured_dependency_sha256']}
            for rel in sorted(files):
                data = safe_path(source,rel).read_bytes()
                if sha(data)!=hashes[rel]:
                    raise ValueError('source/dependency changed: '+rel)
                target = safe_path(destination,rel);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            if case:
                changed,receipt = mutate(case,board,source,cache/'alternate-footprints')
                if receipt != case['structural_receipt']:
                    raise ValueError('mutation differs from registered structural receipt: '+cid)
                for rel,text in changed.items():
                    safe_path(destination,rel).write_bytes(text.encode('utf-8'))
                receipts[cid] = receipt
            write_json(destination/'benchmark/requirements.json',requirements)
            write_json(destination/'benchmark/contract.json',contract)
            native = board['native_files']
            scope = {'schema':'agent-ee.scope/2','name':board['id'],
                'scope':manifest.get('scope_statement','Synthetic assembly, schematic wiring and physical pad geometry benchmark; no functional qualification.'),
                'schematic':native['schematic'],'board':native['pcb'],
                'inputs':[{'path':p,'at':p} for p in sorted(files|{'benchmark/requirements.json','benchmark/contract.json'})],
                'requirements':['benchmark/requirements.json'],
                'checks':[{'id':'benchmark-netlist','kind':'netlist'},
                    {'id':'benchmark-hardware-interface','kind':'hardware_interface','params':{
                        'contract':'benchmark/contract.json','netlist_check':'benchmark-netlist'}},
                    {'id':'benchmark-erc','kind':'erc'}, {'id':'benchmark-drc','kind':'drc'}],
                'baseline':{'requirements':[{'id':'assembly','statement':'Preserve frozen source assembly and wiring requirements.',
                    'source':{'path':'benchmark/requirements.json','locator':board['id']},
                    'checks':['benchmark-netlist','benchmark-hardware-interface','benchmark-erc','benchmark-drc']}],
                    'targets':['benchmark/requirements.json'],
                    'reference_architectures':['benchmark/requirements.json']},
                'limitations':manifest['limitations']+[board['dependency_status']]}
            write_json(destination/'scope.json',scope)
    inventory = {str(p.relative_to(output)):digest(p) for p in sorted(output.rglob('*')) if p.is_file()}
    write_json(output/'prepared.json',{'schema':'agent-ee.benchmark-prepared/1',
        'manifest_sha256':digest(manifest_path),'files':inventory,'receipts':receipts})
    return output


def manifest_store(manifest_path):
    """The Git checkout that tracks a manifest: this repository, or a private Git-backed store.

    Preregistration is proven by Git ancestry, so a manifest kept outside Git, or
    untracked (for example under the ignored build/), has no proof and is refused.
    """
    path = Path(manifest_path).resolve()
    top = subprocess.run(['git','-C',str(path.parent),'rev-parse','--show-toplevel'],capture_output=True,text=True)
    if top.returncode:
        raise ValueError('expectation manifest is not in a Git checkout; preregistration cannot be proven')
    store = Path(top.stdout.strip()).resolve()
    rel = str(path.relative_to(store))
    if subprocess.run(['git','-C',str(store),'ls-files','--error-unmatch','--',rel],capture_output=True).returncode:
        raise ValueError('expectation manifest is not tracked by its Git checkout; preregistration cannot be proven')
    return store, rel


def committed_manifest(manifest_path, commit, repository=None):
    path = Path(manifest_path).resolve()
    if not re.fullmatch(r'[a-f0-9]{40}',commit):
        raise ValueError('expectation commit must be a full commit identity')
    store, rel = manifest_store(path)
    if repository is not None and Path(repository).resolve() != store:
        raise ValueError('expectation manifest is not tracked by the named Git checkout')
    repository = store
    subprocess.run(['git','-C',str(repository),'merge-base','--is-ancestor',commit,'HEAD'],check=True,capture_output=True)
    original = subprocess.run(['git','-C',str(repository),'show',commit+':'+rel],check=True,capture_output=True).stdout
    if original != path.read_bytes():
        raise ValueError('expectation bytes differ from committed preregistration')
    manifest = load_manifest(path)
    boards, cases = MINIMUM_CORPUS[manifest.get('corpus','agentee-21-public')]
    if len(manifest['boards'])<boards or len(manifest['cases'])<cases:
        raise ValueError(f'{boards} board(s) and {cases} committed mutations required')
    return manifest


def check_prepared(prepared, manifest_path):
    prepared = external(prepared)
    record = read_json(prepared/'prepared.json')
    if record['manifest_sha256']!=digest(manifest_path):
        raise ValueError('prepared manifest mismatch')
    paths = list(prepared.rglob('*'))
    for path in paths:
        if path.is_symlink():
            raise ValueError('prepared directory gained symlink')
    actual = {str(p.relative_to(prepared)):digest(p) for p in paths if p.is_file() and p!=prepared/'prepared.json'}
    if actual!=record['files']:
        raise ValueError('prepared bytes changed')
    return record


def execute_batch(prepared, output):
    """Internal container entry: ordinary public checks-only review plus verify."""
    prepared = Path(prepared);output = Path(output)
    request = read_json(output.parent/'request.json')
    if (not re.fullmatch(r'[a-f0-9]{40}',request['expectation_commit']) or
            not re.fullmatch(r'sha256:[a-f0-9]{64}',request['image_id']) or
            request['prepared_sha256']!=digest(prepared/'prepared.json') or
            request['strict_toolchain'] is not True or request['model_pass'] is not False or
            os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN')!='1'):
        raise ValueError('missing/invalid committed strict execution request')
    output = fresh(output)
    with (output/'doctor.json').open('wb') as doctor:
        subprocess.run(['/opt/agent-ee/bin/agent-ee','doctor','--checks-only'],stdout=doctor,check=True)
    records = []
    baselines = {}
    for scope in sorted(prepared.glob('*/scope.json')):
        cid = scope.parent.name;run = output/cid
        review_case(scope,run)
        baseline = read_json(run/'baseline.json')['baseline_id']
        board_id = read_json(scope)['name']
        if board_id in baselines and baselines[board_id]!=baseline:
            raise ValueError('control/variant immutable criteria differ')
        baselines[board_id] = baseline
        records.append({'id':cid,'run':str(run),'result_sha256':digest(run/'result.json')})
    write_json(output/'index.json',{'schema':'agent-ee.benchmark-executions/1','records':records,'model_pass':False})


def pinned_image(image, expected):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image) or image!=expected:
        raise ValueError('image differs from pinned immutable image identity')
    actual = subprocess.run(['docker','image','inspect',image,'--format','{{.Id}}'],capture_output=True,text=True,check=True).stdout.strip()
    if actual!=image:
        raise ValueError('Docker image identity mismatch')


def archive_implementation(output):
    """Extract the committed HEAD tree under output; refuse uncommitted changes."""
    # Freeze the implementation as a separate reachable commit too. Git archive
    # is built only from HEAD, and the launcher runs exclusively that tree.
    status = subprocess.run(['git','-C',str(REPOSITORY),'status','--porcelain'],capture_output=True,text=True,check=True).stdout
    if status:
        raise ValueError('commit implementation before benchmark execution')
    implementation = subprocess.run(['git','-C',str(REPOSITORY),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    output = fresh(output)
    archive = subprocess.run(['git','-C',str(REPOSITORY),'archive',implementation],capture_output=True,check=True).stdout
    source = output/'implementation';source.mkdir()
    import io
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source,filter='data')
    return output, implementation, sha(archive), source


def container_command(name, image, source, mounts, args):
    """The pinned image without network, under the documented outer confinement."""
    command = ['docker','run','--rm','--init','--name',name,
        '--network','none','--user',f'{os.getuid()}:{os.getgid()}','-e','HOME=/tmp',
        '-e','AGENT_EE_REQUIRE_TOOLCHAIN=1','--security-opt','seccomp=unconfined',
        '--security-opt','apparmor=agent-ee-container','--security-opt','systempaths=unconfined',
        '-v',f'{source}:/opt/agent-ee:ro']
    for path,readonly in mounts:
        command += ['-v',f'{path}:{path}'+(':ro' if readonly else '')]
    return command+['-w','/opt/agent-ee','--entrypoint','/usr/bin/python3',image,
                    '/opt/agent-ee/scripts/seeded_benchmark.py',*args]


def launch(command, output):
    write_json(output/'command.json',command)
    with (output/'console.log').open('wb') as console:
        with subprocess.Popen(command,stdout=console,stderr=subprocess.STDOUT) as process:
            starttime = Path(f'/proc/{process.pid}/stat').read_text().rsplit(')',1)[1].split()[19]
            write_json(output/'process.json',{'pid':process.pid,'starttime':starttime,
                'container':command[command.index('--name')+1],'command':command})
            returncode = process.wait()
    write_json(output/'exit.json',{'exit_code':returncode})
    if returncode:
        raise subprocess.CalledProcessError(returncode,command)


def run(manifest_path, cache_prepared, output, expectation_commit, image):
    manifest = committed_manifest(manifest_path,expectation_commit)
    prepared = external(cache_prepared);record = check_prepared(prepared,manifest_path)
    pinned_image(image,manifest['image_id'])
    output, implementation, archive_sha256, source = archive_implementation(output)
    store = 'agent-ee' if manifest_store(manifest_path)[0]==REPOSITORY.resolve() else 'private-git'
    request = {'schema':'agent-ee.benchmark-request/1','expectation_commit':expectation_commit,'expectation_store':store,
        'manifest_sha256':digest(manifest_path),'implementation_commit':implementation,
        'implementation_archive_sha256':archive_sha256,'image_id':image,'prepared_sha256':digest(prepared/'prepared.json'),
        'strict_toolchain':True,'model_pass':False}
    write_json(output/'request.json',request)
    launch(container_command(manifest.get('container_prefix','agent-ee-21-')+output.name,image,source,
        [(prepared,True),(output,False)],['_execute',str(prepared),str(output/'runs')]),output)
    if check_prepared(prepared,manifest_path)!=record:
        raise ValueError('prepared input changed during execution')
    return output


def image_native_rerun(first, output, image, repeats=2):
    """native_rerun inside the retained run's pinned image, from the committed implementation."""
    first = external(first);retained = read_json(first/'request.json')
    pinned_image(image,retained['image_id'])
    output, implementation, archive_sha256, source = archive_implementation(output)
    write_json(output/'request.json',{'schema':'agent-ee.benchmark-native-rerun-request/1',
        'source_request_sha256':digest(first/'request.json'),'source_implementation_commit':retained['implementation_commit'],
        'implementation_commit':implementation,'implementation_archive_sha256':archive_sha256,'image_id':image,
        'repeats':repeats,'strict_toolchain':True,'model_pass':False})
    launch(container_command('agent-ee-'+output.name,image,source,[(first,True),(output,False)],
        ['native-rerun',str(first),str(output/'rerun'),'--repeats',str(repeats)]),output)
    return read_json(output/'rerun'/'native-rerun.json')


def canonical_check(check):
    """Drop only documented execution volatility; keep numerical/locator data."""
    result = copy.deepcopy(check)
    for key in ('started_utc','finished_utc','duration_s','candidate_id','run_id'):
        result.pop(key,None)
    if isinstance(result.get('details',{}).get('tool'),dict):
        result['details']['tool'].pop('duration_s',None)
        result['details']['tool'].pop('console_sha256',None)
    # Native raw evidence hashes may encode timestamps. Their raw artifacts
    # remain sealed; comparison uses parsed native records separately.
    result.pop('evidence',None)
    return result


def observations(run):
    run = Path(run)
    result = read_json(run/'result.json')
    from .cli import artifacts
    from .candidate import verify_snapshot
    from .baseline import verify_evidence
    if artifacts(run)!=result['artifact_hashes']:
        raise ValueError('retained benchmark artifacts changed')
    verify_snapshot(run)
    verify_evidence(run,result['checks'],required=False)
    if result['reviews'] or result['review_executions'] or result['adjudicated']:
        raise ValueError('model review must not enter deterministic benchmark')
    records = {}
    for check in result['checks']:
        if check['execution'] not in CHECK_EXECUTIONS or check['outcome'] not in CHECK_OUTCOMES:
            raise ValueError('invalid check status')
        cid = check['id']
        if read_json(run/'checks'/(cid+'.json'))!=check:
            raise ValueError('check projection differs from retained check')
        if cid in records:
            raise ValueError('duplicate check record')
        records[cid] = check_record(run,check)
    return records


def check_record(run, check):
    """One check's comparable record: canonical result, native report and pad map."""
    run = Path(run)
    raw = None
    if check['kind'] in ('drc','erc'):
        path = run/'work'/check['id']/(check['kind']+'.json')
        if path.exists():
            raw = read_json(path)
            # These native fields are provenance/time only. Preserve all
            # actual violation geometry, item UUIDs, rule types and values.
            for key in ('date','kicad_version','source'):
                raw.pop(key,None)
    record = {'check':canonical_check(check),'native':canonical_native(raw)}
    if check['kind']=='drc':
        from .benchmark import parse, children, uid, values
        manifest = read_json(run/'candidate.json')
        board = parse((run/'inputs'/manifest['scope']['board']).read_text())
        mapping = {}
        for footprint in children(board,'footprint'):
            pads = {}
            for pad in children(footprint,'pad'):
                if values(pad)[0]:
                    pads.setdefault(values(pad)[0],[]).append(uid(pad))
            mapping[uid(footprint)] = pads
        record['pcb_pad_uuids'] = mapping
    return record


def connection_semantics(native, islands):
    """Net -> sorted unconnected island identities, from native ratsnest witnesses.

    Each unconnected_items record names one item of each of two copper islands.
    KiCad may pick a different item pair, or a different spanning tree over the
    same islands, on an identical board. The records of each net must form one
    spanning tree over its islands; otherwise, or if any witness item has no
    consistent native island, the semantics are unresolved (None).
    """
    if native is None or not isinstance(native.get('unconnected_items'),list):
        return None
    nets = {}
    for row in native['unconnected_items']:
        found = [islands.get(item.get('uuid')) for item in row.get('items',[])]
        if len(found)!=2 or None in found or found[0][0]!=found[1][0] or found[0][1]==found[1][1]:
            return None
        nets.setdefault(found[0][0],[]).append((found[0][1],found[1][1]))
    result = {}
    for net,edges in nets.items():
        nodes = {node for edge in edges for node in edge}
        reached, frontier = set(), [edges[0][0]]
        while frontier:
            node = frontier.pop()
            if node not in reached:
                reached.add(node)
                frontier.extend(b if a==node else a for a,b in edges if node in (a,b))
        if len(edges)!=len(nodes)-1 or reached!=nodes:
            return None
        result[net] = sorted(nodes)
    return result


# KiCad's console names each TrueType font it replaces. Text geometry, and so
# DRC text and silkscreen records, depends on the installed substitute.
FONT_SUBSTITUTION = re.compile(r"Font '([^']+)' not found; substituting '([^']+)'")


def font_substitutions(run, check_id):
    path = Path(run)/'work'/check_id/'console.log'
    return dict(FONT_SUBSTITUTION.findall(path.read_text(errors='replace'))) if path.is_file() else {}


def same_board(first, second):
    """The captured board text when both runs reviewed byte-identical boards, else None."""
    texts = []
    for run in (Path(first),Path(second)):
        manifest = read_json(run/'candidate.json')
        rel = manifest['scope'].get('board')
        path = run/'inputs'/rel if rel else None
        if path is None or not path.is_file() or digest(path)!=manifest['files'].get(rel):
            return None
        texts.append(path.read_bytes())
    return texts[0].decode('utf-8') if texts[0]==texts[1] else None


def native_rerun(first, output, repeats=2):
    """Re-execute every retained run's declared checks from its captured inputs.

    Each repeat copies the hash-verified captured inputs into a fresh scratch run
    and calls the ordinary check dispatcher, so native tools run in the same
    networkless sandbox with the same arguments. Results are compared with the
    retained run exactly, then by native connection semantics.
    """
    import time
    from .checks import run_checks
    first = external(first);output = fresh(output)
    if repeats<1:
        raise ValueError('at least one repeat required')
    index = read_json(first/'runs/index.json')
    cases = []
    for entry in sorted(index['records'],key=lambda e:e['id']):
        retained = first/'runs'/safe_id(entry['id'])
        if digest(retained/'result.json')!=entry['result_sha256']:
            raise ValueError('retained result changed')
        original = observations(retained)
        manifest = read_json(retained/'candidate.json')
        row = {'id':entry['id'],'repeats':[]}
        for repeat in range(1,repeats+1):
            run = output/f'repeat-{repeat}'/entry['id'];run.mkdir(parents=True)
            for rel,expected in sorted(manifest['files'].items()):
                source = retained/'inputs'/rel
                if source.is_symlink() or digest(source)!=expected:
                    raise ValueError('captured input changed: '+rel)
                target = run/'inputs'/rel;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,target)
            for name in ('candidate.json','baseline.json'):
                if (retained/name).is_file():
                    shutil.copyfile(retained/name,run/name)
            results = run_checks(run,time.monotonic()+1800)
            records = {check['id']:check_record(run,check) for check in results}
            board = same_board(retained,run)
            native = compare_records(original,records,board,NATIVE_FIELDS)
            fonts = {c['id']:{'retained':font_substitutions(retained,c['id']),'rerun':font_substitutions(run,c['id'])}
                     for c in results if c['kind'] in ('erc','drc')}
            row['repeats'].append({'repeat':repeat,'executions':{c['id']:c['execution'] for c in results},
                'native_exact':native['exact'],'native_semantic':native['semantic'],
                'font_substitutions':{k:v for k,v in fonts.items() if v['retained']!=v['rerun']},
                **compare_records(original,records,board)})
        cases.append(row)
    from .cli import runtime_files
    retained = read_json(first/'runs'/index['records'][0]['id']/'result.json')['runtime_files']
    host = runtime_files()
    native = ('kicad-cli','bwrap','/usr/bin/python3','pcbnew')
    summary = {'schema':'agent-ee.benchmark-native-rerun/1','source':str(first),
        'request':read_json(first/'request.json'),'repeats':repeats,'model_pass':False,
        'exact_cases':sum(all(r['exact'] for r in c['repeats']) for c in cases),
        'semantic_cases':sum(all(r['semantic'] for r in c['repeats']) for c in cases),
        'native_exact_cases':sum(all(r['native_exact'] for r in c['repeats']) for c in cases),
        'native_semantic_cases':sum(all(r['native_semantic'] for r in c['repeats']) for c in cases),
        'all_cases':len(cases),'cases':cases,
        # Check records also carry implementation-defined details; only the
        # native records are expected to match a run of another implementation.
        'implementation':{'retained':read_json(first/'request.json').get('implementation_commit'),'rerun':implementation()},
        # The rerun environment is this process, not the pinned image; the
        # native binaries are compared by content with the retained run.
        'native_runtime':{name:{'retained':retained.get(name,{}).get('sha256'),'rerun':host.get(name,{}).get('sha256')}
                          for name in native},
        'same_font_substitutions':not any(r['font_substitutions'] for c in cases for r in c['repeats']),
        'same_native_runtime':all(retained.get(n,{}).get('sha256') and
                                  retained[n]['sha256']==host.get(n,{}).get('sha256') for n in native)}
    write_json(output/'native-rerun.json',summary)
    return summary


def implementation():
    """This process's implementation commit, marked when the checkout has uncommitted changes."""
    head = subprocess.run(['git','-C',str(REPOSITORY),'rev-parse','HEAD'],capture_output=True,text=True)
    status = subprocess.run(['git','-C',str(REPOSITORY),'status','--porcelain'],capture_output=True,text=True)
    if head.returncode or status.returncode:
        return None
    return head.stdout.strip()+('+uncommitted' if status.stdout.strip() else '')


def native_rows(native, section):
    return sorted(canonical(row).decode() for row in (native or {}).get(section,[]))


NATIVE_FIELDS = ('native','pcb_pad_uuids')


def compare_records(original, other, board_text=None, fields=None):
    """Exact comparison of two runs' check records, then native connection semantics.

    Only DRC unconnected_items may be equal semantically rather than exactly; every
    other field, section, value and locator must stay identical. A semantic match
    needs the same captured board, resolved by native connectivity. ``fields``
    restricts the comparison, for example to the native records alone.
    """
    if fields is not None:
        def project(records):
            return {cid:{'check':{'kind':r['check']['kind']},**{k:r[k] for k in fields if k in r}}
                    for cid,r in records.items()}
        original, other = project(original), project(other)
    differences = []
    semantic = True
    for cid in sorted(set(original)|set(other)):
        first, second = original.get(cid), other.get(cid)
        if first==second:
            continue
        row = {'check_id':cid}
        if first is None or second is None:
            row['missing'] = 'first' if first is None else 'second'
            differences.append(row);semantic = False
            continue
        row['fields'] = sorted(k for k in set(first)|set(second) if first.get(k)!=second.get(k))
        a, b = first.get('native') or {}, second.get('native') or {}
        row['native_sections'] = sorted(k for k in set(a)|set(b) if a.get(k)!=b.get(k))
        row['first_only'] = {};row['second_only'] = {}
        for section in row['native_sections']:
            if isinstance(a.get(section),list) and isinstance(b.get(section),list):
                x, y = native_rows(a,section), native_rows(b,section)
                row['first_only'][section] = [json.loads(r) for r in x if r not in y]
                row['second_only'][section] = [json.loads(r) for r in y if r not in x]
        stripped = [{**r,'native':{k:v for k,v in (r.get('native') or {}).items() if k!='unconnected_items'}}
                    for r in (first,second)]
        row['semantic_equal'] = False
        if (first['check']['kind']=='drc' and board_text is not None and stripped[0]==stripped[1]):
            from .benchmark import copper_islands
            islands = copper_islands(board_text)
            left, right = connection_semantics(a,islands), connection_semantics(b,islands)
            row['connection_semantics'] = left if left==right else {'first':left,'second':right}
            row['semantic_equal'] = left is not None and left==right
        semantic = semantic and row['semantic_equal']
        differences.append(row)
    return {'exact':not differences,'semantic':semantic,'differences':differences}


def classify(expected, record, case, control=None):
    if expected['kind']=='planned':
        # A planned detector is preregistered with no scope check; any
        # retained record would mean the expectation no longer describes it.
        if record is not None:
            raise ValueError('planned detector unexpectedly executed; preregister a new expectation')
        return 'unsupported'
    if record is None:
        return 'not_evaluated'
    check = record['check']
    execution,outcome = check['execution'],check['outcome']
    if execution in ('unsupported','unavailable','not_run'):
        return 'unsupported' if execution!='not_run' else 'not_evaluated'
    if execution!='ok':
        return 'execution_error'
    if outcome=='unknown':
        return 'not_evaluated'
    if expected['kind']=='hardware_interface':
        wanted = {f'{case["id"]}-{i}' for i,_ in enumerate(expected['predicate'] if isinstance(expected['predicate'],list) else [expected['predicate']])}
        invariants = check.get('details',{}).get('invariants',[])
        matched = [r for r in invariants if r['id'] in wanted]
        if len(matched)!=len(wanted) or any(r['status']=='not_evaluated' for r in matched):
            return 'not_evaluated'
        violations = [r for r in matched if r['status']=='violation']
        if violations and control is not None:
            baseline = {r['id']:r['status'] for r in control['check'].get('details',{}).get('invariants',[])}
            if not any(baseline.get(r['id'])=='satisfied' for r in violations):
                return 'control_unresolved'
        return 'detection' if violations else 'miss'
    if expected['kind']=='drc':
        raw = record['native']
        if raw is None:
            return 'execution_error'
        # A generic DRC failure cannot establish the seed. Require its declared
        # native section plus exact target footprint/pad UUID intersection.
        predicate = expected['predicate'];section = predicate['kind']
        if section not in ('schematic_parity','unconnected_items'):
            raise ValueError('unknown native benchmark predicate')
        target = set()
        if section=='schematic_parity':
            pins = predicate.get('pins',[predicate['pin']] if 'pin' in predicate else [])
            # A predicate naming its component targets only that footprint; other
            # selectors exist for interface relations, not for this native record.
            selectors = [s for s in case['source_selectors']
                         if 'reference' not in predicate or s.get('reference')==predicate['reference']]
            if not selectors:
                return 'not_evaluated'
            for selector in selectors:
                footprint = selector['pcb']['footprint_uuid']
                mapping = record['pcb_pad_uuids'].get(footprint,{})
                if any(pin not in mapping for pin in pins) or not pins:
                    return 'not_evaluated'
                target.update(pad for pin in pins for pad in mapping[pin])
        if section=='unconnected_items' and 'before_pad_geometry' in case['mutation']:
            target = {p['uuid'] for p in case['mutation']['before_pad_geometry'] if p['number']!='1'}
        elif section=='unconnected_items':
            # Declared pad numbers of the selected footprint, by retained UUID.
            mapping = record['pcb_pad_uuids'].get(case['source_selectors'][0]['pcb']['footprint_uuid'],{})
            if not predicate['pads'] or any(pad not in mapping for pad in predicate['pads']):
                return 'not_evaluated'
            target = {uuid for pad in predicate['pads'] for uuid in mapping[pad]}
        def identities(row):
            return {item['uuid'] for item in row.get('items',[]) if 'uuid' in item}
        baseline = control['native'][section] if control and control['native'] is not None else []
        if control is not None and (control['native'] is None or control['check']['execution']!='ok'):
            return 'control_unresolved'
        baseline_keys = {(r.get('type'),pad) for r in baseline for pad in identities(r)&target}
        relevant = [r for r in raw[section] if identities(r)&target]
        matched = [r for r in relevant if any((r.get('type'),pad) not in baseline_keys for pad in identities(r)&target)]
        if relevant and not matched and baseline_keys:
            return 'control_unresolved'
        return 'detection' if matched else 'miss'
    if expected['kind']=='erc':
        # Credit needs the predicted native violation type at a registered
        # schematic item UUID that the paired control does not already report.
        raw = record['native']
        if raw is None:
            return 'execution_error'
        if control is None or control['native'] is None or control['check']['execution']!='ok':
            return 'control_unresolved'
        predicate = expected['predicate']
        targets = set(predicate['item_uuids'])
        if not targets:
            raise ValueError('ERC prediction requires target item UUIDs')
        def keys(native):
            return {(v.get('type'),item['uuid']) for sheet in native.get('sheets',[]) for v in sheet.get('violations',[])
                    for item in v.get('items',[]) if item.get('uuid') in targets}
        observed = {k for k in keys(raw) if k[0]==predicate['type']}
        if observed - keys(control['native']):
            return 'detection'
        return 'control_unresolved' if observed else 'miss'
    raise ValueError('no qualified matcher for expected check kind')


def native_records(record):
    """Native finding identities: type plus sorted item UUIDs, as a multiset."""
    raw = record['native'] if record else None
    if raw is None:
        return None
    if record['check']['kind']=='erc':
        rows = [v for sheet in raw.get('sheets',[]) for v in sheet.get('violations',[])]
    else:
        rows = [v for k in ('violations','unconnected_items','schematic_parity') for v in raw.get(k,[])]
    result = {}
    for row in rows:
        key = canonical([row.get('type'),sorted(i.get('uuid','') for i in row.get('items',[]))])
        result[key] = result.get(key,0)+1
    return result


def native_delta(observed, control):
    """New native ERC/DRC records in a variant relative to its paired control."""
    delta = {}
    for cid,record in observed.items():
        kind = record['check']['kind']
        if kind not in ('erc','drc'):
            continue
        new,old = native_records(record),native_records(control.get(cid))
        if new is None or old is None or record['check']['execution']!='ok':
            delta[kind] = None
            continue
        delta[kind] = sum(max(0,n-old.get(k,0)) for k,n in new.items())
    return delta


def report(manifest_path, first, second=None):
    manifest = load_manifest(manifest_path)
    first = Path(first);request = read_json(first/'request.json')
    if request['manifest_sha256']!=digest(manifest_path) or request['image_id']!=manifest['image_id']:
        raise ValueError('execution request identity mismatch')
    expected_ids = {b['id']+'-control' for b in manifest['boards']}|{c['id'] for c in manifest['cases']}
    index = read_json(first/'runs/index.json')
    if index['model_pass'] or {r['id'] for r in index['records']}!=expected_ids or len(index['records'])!=len(expected_ids):
        raise ValueError('execution index missing controls/cases or includes model pass')
    observed = {}
    for entry in index['records']:
        run = first/'runs'/entry['id']
        if digest(run/'result.json')!=entry['result_sha256']:
            raise ValueError('retained result changed')
        observed[entry['id']] = observations(run)
    cases = []
    counts = {k:{'detection':0,'miss':0,'unsupported':0,'not_evaluated':0,'execution_error':0,'control_unresolved':0} for k in ('hardware_interface','drc','erc','netlist','planned')}
    detected = 0;evaluated = 0
    for case in manifest['cases']:
        predictions = []
        for expectation in case['expected_checks']:
            kind = expectation['kind']
            control = observed[case['board_id']+'-control'].get(expectation['check_id'])
            status = classify(expectation,observed[case['id']].get(expectation['check_id']),case,control)
            counts.setdefault(kind,{}).setdefault(status,0);counts[kind][status]+=1
            predictions.append({'check_id':expectation['check_id'],'kind':kind,'status':status,
                'qualifying_locator':expectation['qualifying_locator']})
            if kind=='planned':
                predictions[-1]['detector'] = expectation['detector']
        statuses = {p['status'] for p in predictions}
        detected += 'detection' in statuses
        evaluated += bool(statuses & {'detection','miss'})
        row = {'id':case['id'],'predictions':predictions}
        if 'native_visibility' in case:
            row['native_visibility'] = {'real_defect':case['native_visibility']['real_defect'],
                'native_prediction_detected':any(p['status']=='detection' for p in predictions if p['kind'] in ('erc','drc')),
                'new_native_records':native_delta(observed[case['id']],observed[case['board_id']+'-control'])}
        cases.append(row)
    controls = []
    unresolved = {k:0 for k in counts}
    adjudication = adjudicate_controls(manifest,first)
    decided = {c['id']:c['records'] for c in adjudication['controls']}
    for board in manifest['boards']:
        control = observed[board['id']+'-control']
        for r in control.values():
            unresolved[r['check']['kind']] += finding_count(r)
        def verdicts(cid):
            rows = [r['verdict'] for r in decided[board['id']+'-control'] if r['check_id']==cid]
            return {v:rows.count(v) for v in sorted(set(rows))}
        controls.append({'id':board['id']+'-control','checks':[
            {'id':cid,'kind':r['check']['kind'],'execution':r['check']['execution'],
             'outcome':r['check']['outcome'],'finding_count':finding_count(r),'adjudication':verdicts(cid)}
            for cid,r in control.items()]})
    comparison = None
    if second:
        other_request = read_json(Path(second)/'request.json')
        for key in ('expectation_commit','manifest_sha256','implementation_commit','image_id','prepared_sha256'):
            if other_request[key]!=request[key]:
                raise ValueError('rerun identity mismatch: '+key)
        other_index = read_json(Path(second)/'runs/index.json')
        if {r['id'] for r in other_index['records']}!=expected_ids:
            raise ValueError('rerun missing cases')
        differences = []
        witness_differences = []
        assessment_differences = []
        other_observed = {}
        for entry in other_index['records']:
            run = Path(second)/'runs'/entry['id']
            if digest(run/'result.json')!=entry['result_sha256']:
                raise ValueError('rerun result changed')
            other = observations(run)
            other_observed[entry['id']] = other
            original = observed[entry['id']]
            if other!=original:
                differences.append(entry['id'])
                compared = compare_records(original,other,same_board(first/'runs'/entry['id'],run))
                witness_differences.append({'id':entry['id'],**compared})
            if {k:v['check'] for k,v in other.items()}!={k:v['check'] for k,v in original.items()}:
                assessment_differences.append(entry['id'])
        classification_differences = []
        for case,first_case in zip(manifest['cases'],cases):
            statuses = [classify(e,other_observed[case['id']].get(e['check_id']),case,
                other_observed[case['board_id']+'-control'].get(e['check_id'])) for e in case['expected_checks']]
            if statuses!=[p['status'] for p in first_case['predictions']]:
                classification_differences.append(case['id'])
        comparison = {'same_check_results':not differences,'different_cases':differences,
            'same_check_semantics':all(d['semantic'] for d in witness_differences),
            'native_witness_differences':witness_differences,
            'same_check_assessments':not assessment_differences,'different_check_assessments':assessment_differences,
            'same_seed_classifications':not classification_differences,'different_seed_classifications':classification_differences,
            'normalization':'Exclude timing/candidate/run IDs and evidence hashes; sort native finding/item arrays only; preserve exact details/locators/numerical values.',
            'semantic_basis':'Only DRC unconnected_items witnesses may match semantically: on byte-identical captured boards, '
                             'both runs must name the same native copper islands per net, each as one spanning tree; '
                             'every other field must be exactly equal.'}
    return {'schema':'agent-ee.benchmark-report/1','synthetic':True,'model_pass':False,
        'expectation_commit':request['expectation_commit'],'image_id':request['image_id'],
        'per_kind':counts,'cases':cases,'controls':controls,
        'seeded_detection':{'detected':detected,'all_seeds':len(cases),'evaluated_seeds':evaluated,
            'all_seed_fraction':detected/len(cases),'evaluated_fraction':detected/evaluated if evaluated else None},
        'false_alarms':false_alarms(adjudication,unresolved,counts),'control_adjudication':adjudication,
        'native_visibility':visibility(cases),'rerun':comparison,'limitations':manifest['limitations']}


# Native library-resolution messages that describe the review sandbox's library
# tables, not the design. Matched exactly; any other wording needs judgement.
LIBRARY_ABSENT = re.compile(r"The current configuration does not include the (symbol|footprint) library '([^']+)'")
LIBRARY_RULES = {('erc','lib_symbol_issues'):'symbol',('erc','footprint_link_issues'):'footprint',
                 ('drc','lib_footprint_issues'):'footprint'}
ADJUDICATION_RULES = {
    'environment-library-table': 'false alarm: the native message names a library nickname that neither the '
        "check's retained hermetic library table nor the captured project table defines; it reports the review "
        'sandbox configuration of an unmodified control, not a design defect',
    'frozen-invariant-satisfied': 'true negative: an evaluated hardware-interface invariant, frozen from this '
        'unmodified control, is satisfied',
    'frozen-invariant-violated': 'false alarm: an evaluated hardware-interface invariant, frozen from this '
        'unmodified control, is reported violated',
    'not-executed': 'not evaluated: the check or invariant did not execute, so there is no opportunity',
    'requires-judgement': 'unadjudicated: deciding whether this native record is a genuine control defect '
        'requires engineering judgement; it is not counted as either outcome',
}


def library_nicknames(path):
    """Library nicknames a KiCad library table defines; an absent table defines none."""
    from .symbol_library import children as nodes, field, parse_sexpr
    if not path.is_file():
        return set()
    return {field(lib,'name',1)[0] for lib in nodes(parse_sexpr(path.read_bytes()),'lib')}


def native_control_rows(run, check):
    """(category, raw locator, record, sheet) for every retained native ERC/DRC record."""
    raw = read_json(Path(run)/'work'/check['id']/(check['kind']+'.json'))
    if check['kind']=='erc':
        rows = [('violations',f'/sheets/{i}/violations/{j}',v,s) for i,s in enumerate(raw['sheets'])
                for j,v in enumerate(s['violations'])]
    else:
        rows = [(k,f'/{k}/{j}',v,None) for k in ('violations','unconnected_items','schematic_parity')
                for j,v in enumerate(raw[k])]
    return raw, rows


def adjudicate_controls(manifest, first):
    """Deterministic benchmark classification of every retained control record.

    This never edits the sealed runs, makes no model call and never changes a
    finding disposition: the central adjudicator treats check defects as
    authoritative. Records the fixed rules cannot settle stay unadjudicated.
    """
    from .violation_waivers import violation_key
    per_kind, controls = {}, []
    for board in manifest['boards']:
        cid = board['id']+'-control';run = Path(first)/'runs'/cid
        result = read_json(run/'result.json');candidate = read_json(run/'candidate.json')
        scope = candidate['scope'];rows = []
        for check in result['checks']:
            kind = check['kind']
            tally = per_kind.setdefault(kind,{'records':0,'false_alarm':0,'true_negative':0,'unadjudicated':0,
                                              'not_evaluated':0,'checks_not_ok':0,'by_rule':{}})
            if check['execution']!='ok':
                # No executed check, no opportunity: never a true negative.
                tally['checks_not_ok'] += 1
                tally['not_evaluated'] += len(check.get('details',{}).get('invariants',[]))
                rows.append({'check_id':check['id'],'kind':kind,'execution':check['execution'],
                             'verdict':'not_evaluated','rule':'not-executed'})
                continue
            decided = []
            if kind=='hardware_interface':
                for invariant in check.get('details',{}).get('invariants',[]):
                    status = invariant['status']
                    if status not in ('satisfied','violation'):
                        tally['not_evaluated'] += 1
                        rows.append({'check_id':check['id'],'kind':kind,'invariant':invariant['id'],'status':status,
                                     'verdict':'not_evaluated','rule':'not-executed'})
                    elif status=='satisfied':
                        decided.append(({'invariant':invariant['id']},'true_negative','frozen-invariant-satisfied'))
                    else:
                        decided.append(({'invariant':invariant['id']},'false_alarm','frozen-invariant-violated'))
            elif kind in ('erc','drc'):
                raw, records = native_control_rows(run,check)
                subject = scope['schematic' if kind=='erc' else 'board']
                tables = {name:library_nicknames(run/'work'/check['id']/'kicad-config'/'10.0'/f'{name}-lib-table')
                          | library_nicknames(run/'inputs'/Path(subject).parent/f'{name}-lib-table')
                          for name in ('sym','fp')}
                for category,locator,record,sheet in records:
                    key = violation_key(kind,subject,category,raw,record,sheet)
                    library = LIBRARY_RULES.get((kind,record.get('type')))
                    match = LIBRARY_ABSENT.fullmatch(record.get('description',''))
                    entry = {'raw_locator':locator,'type':record.get('type'),'severity':record.get('severity'),
                             'location_hash':key['location_hash'] if key else None}
                    if (library and match and match.group(1)==library and
                            match.group(2) not in tables['sym' if library=='symbol' else 'fp']):
                        decided.append(({**entry,'library':match.group(2)},'false_alarm','environment-library-table'))
                    else:
                        decided.append((entry,'unadjudicated','requires-judgement'))
            for entry,verdict,rule in decided:
                tally['records'] += 1;tally[verdict] += 1
                tally['by_rule'][rule] = tally['by_rule'].get(rule,0)+1
                rows.append({'check_id':check['id'],'kind':kind,**entry,'verdict':verdict,'rule':rule})
        controls.append({'id':cid,'records':rows})
    for kind,tally in per_kind.items():
        n, fa, tn, open_ = tally['records'], tally['false_alarm'], tally['true_negative'], tally['unadjudicated']
        if kind=='hardware_interface':
            # Each evaluated frozen invariant is one negative opportunity.
            tally['negative_opportunities'] = fa+tn
            tally['rate'] = fa/(fa+tn) if fa+tn else None
        else:
            # Native records have no negative opportunity count: report the share
            # of control records that are false alarms, bounded by the open ones.
            tally['negative_opportunities'] = None
            tally['rate'] = fa/n if n and not open_ else None
            tally['share_bounds'] = [fa/n,(fa+open_)/n] if n else None
    return {'rules':ADJUDICATION_RULES,'per_kind':per_kind,'controls':controls,
        'status':'Deterministic rules only. Unadjudicated records are neither false alarms nor genuine defects; '
                 'a native share bound spans every possible decision of them.'}


def visibility(cases):
    """Separate seeds whose real defect native ERC/DRC can see from the rest."""
    rows = [c for c in cases if 'native_visibility' in c]
    if not rows:
        return None
    def group(visible):
        selected = [c for c in rows if c['native_visibility']['real_defect'] is visible]
        return {'cases':[c['id'] for c in selected],
            'native_prediction_detected':[c['id'] for c in selected if c['native_visibility']['native_prediction_detected']],
            'any_new_native_record':[c['id'] for c in selected if any(v for v in c['native_visibility']['new_native_records'].values())],
            'native_unavailable':[c['id'] for c in selected if None in c['native_visibility']['new_native_records'].values()],
            'detected_by_any_prediction':[c['id'] for c in selected if any(p['status']=='detection' for p in c['predictions'])]}
    return {'native_visible_real_defect':group(True),'native_invisible_real_defect':group(False),
        'basis':'Catalogue judgement of the real defect, not the seed. Schematic-only seeds keep PCB copper and can '
                'add parity records that a synchronized real defect would not; those are seed artifacts.'}


def false_alarms(adjudication, unresolved, counts):
    rows = {}
    for kind in counts:
        tally = adjudication['per_kind'].get(kind,{})
        rows[kind] = {'confirmed':tally.get('false_alarm',0),'unresolved_control_findings':unresolved[kind],
            'unadjudicated_records':tally.get('unadjudicated',0),'adjudicated_records':
                tally.get('false_alarm',0)+tally.get('true_negative',0),
            'negative_opportunities':tally.get('negative_opportunities'),'rate':tally.get('rate'),
            'share_bounds':tally.get('share_bounds')}
    return {'per_kind':rows,'status':adjudication['status']}


def finding_count(record):
    check,raw = record['check'],record['native']
    if check['kind']=='hardware_interface':
        return sum(r['status']=='violation' for r in check.get('details',{}).get('invariants',[]))
    if check['kind']=='drc' and raw is not None:
        return sum(len(raw.get(k,[])) for k in ('violations','unconnected_items','schematic_parity'))
    if check['kind']=='erc' and raw is not None:
        return sum(len(s.get('violations',[])) for s in raw.get('sheets',[]))
    return 0


def review_case(scope, run):
    from .cli import run_review, verify
    exit_code = run_review(scope,run,1800,checks_only=True)
    verified = verify(run)
    if exit_code != (0 if verified['outcome']=='criteria_satisfied' else 1):
        raise ValueError('review exit code contradicts verified outcome')
    expected = {c['id'] for c in read_json(scope)['checks']}
    if {c['id'] for c in verified['checks']}!=expected or verified.get('error'):
        raise ValueError('review failed before all declared checks were retained')
    return verified


def canonical_native(value):
    """Ignore native record ordering, preserving every witness/value/locator."""
    if isinstance(value,list):
        return [canonical_native(v) for v in value]
    if not isinstance(value,dict):
        return value
    result = {k:canonical_native(v) for k,v in value.items()}
    for key in ('violations','unconnected_items','schematic_parity','items','ignored_checks','sheets'):
        if isinstance(result.get(key),list):
            result[key] = sorted(result[key],key=canonical)
    return result


CLOSURE_SCHEMA = 'agent-ee.benchmark-dependency-closure/2'
BENCHMARK_INPUTS = ('benchmark/requirements.json','benchmark/contract.json')
# Native binaries a retained run records by content; the closure pins each one.
NATIVE_RUNTIME = ('kicad-cli','bwrap','/usr/bin/python3','pcbnew')
# Where each closure tool lives. The closure lists exactly these tools; only the
# container runtime that launches the image is outside it.
TOOL_LOCATIONS = {**{name:'runtime' for name in NATIVE_RUNTIME},'git':'runtime','docker':'outer-host'}
FONT_PACKAGES = frozenset({'fonts-dejavu-core','fonts-dejavu-mono','fontconfig'})
# Positive evidence of running inside an agent-ee image: Docker's container
# marker and the image context record the Dockerfile installs.
CONTAINER_MARKER = '/.dockerenv'
IMAGE_CONTEXT = '/usr/local/share/agent-ee/image-context.json'
IMAGE_CONTEXT_RECORD = {'schema':'agent-ee.image-context/1','outer_host_tools':['docker']}
# Every consumed library copy is embedded in, or captured beside, a pinned native file.
LIBRARY_LICENCES = {'kicad-stock':'CC-BY-SA-4.0 with KiCad design-use exception (embedded copy)',
                    'project':'board hardware grant (captured project file)',
                    'third-party-custom':'board hardware grant (embedded copy; source library not acquired)',
                    'none':'board hardware grant (embedded copy)'}


def design_dependencies(board, tree):
    """Every library nickname, 3D-model root and project table one pinned flat board names."""
    from .benchmark import children, one, parse, values
    from .symbol_library import children as nodes, field, parse_sexpr
    native = board['native_files'];hashes = board['native_file_sha256']
    schematic = parse(checked_read(tree,native['schematic'],hashes[native['schematic']]))
    pcb = parse(checked_read(tree,native['pcb'],hashes[native['pcb']]))
    if children(schematic,'sheet'):
        raise ValueError('dependency closure covers flat boards only')
    embedded = {values(s)[0] for lib in children(schematic,'lib_symbols') for s in children(lib,'symbol')}
    symbols = set()
    for symbol in children(schematic,'symbol'):
        lib_id = values(one(symbol,'lib_id'))[0]
        name = values(one(symbol,'lib_name'))[0] if children(symbol,'lib_name') else lib_id
        if name not in embedded:
            raise ValueError('placed symbol has no embedded definition: '+lib_id)
        symbols.add(lib_id.split(':',1)[0] if ':' in lib_id else '')
    footprints = {values(f)[0].split(':',1)[0] if ':' in values(f)[0] else '' for f in children(pcb,'footprint')}
    models = {values(m)[0].rsplit('/',1)[0] for f in children(pcb,'footprint') for m in children(f,'model')}
    tables = {}
    for rel,expected in sorted(board['captured_dependency_sha256'].items()):
        if Path(rel).name in ('fp-lib-table','sym-lib-table'):
            data = safe_path(tree,rel).read_bytes()
            if sha(data)!=expected:
                raise ValueError('captured library table changed: '+rel)
            tables[rel] = {field(lib,'name',1)[0]:field(lib,'uri',1)[0] for lib in nodes(parse_sexpr(data),'lib')}
    return {'symbol_libraries':sorted(symbols),'footprint_libraries':sorted(footprints),
            'model_roots':sorted(models),'project_tables':tables}


def library_origin(kind, nickname, tables, stock):
    """Where a nickname would resolve: the installed stock set, a captured project table, or nowhere."""
    if not nickname:
        return 'none'
    if any(nickname in rows and rows[nickname].startswith('${KIPRJMOD}') for rows in tables.values()):
        return 'project'
    path = Path(stock)/('symbols' if kind=='symbol' else 'footprints')/(nickname+('.kicad_sym' if kind=='symbol' else '.pretty'))
    return 'kicad-stock' if path.exists() else 'third-party-custom'


def tool_licences(tools):
    """Verify each tool's licence file by hash where that tool lives; return the explicit result per tool.

    'runtime' evidence belongs to the executing runtime (the pinned image, or a
    native host with the same toolchain) and must match byte for byte. 'outer-host'
    evidence belongs to the container runtime that launches the image: it is
    pinned by hash and path, verified wherever its binary or licence file exists,
    and reported absent where neither file exists instead of being skipped
    silently. Absence alone does not prove the image: verify_closure keeps
    'outer-host-absent' only with positive image context, otherwise 'unknown'.
    The tool inventory must match TOOL_LOCATIONS exactly, with no duplicates;
    every tool must carry its policy location.
    """
    results = {}
    for tool in tools:
        name = tool.get('name') or '?';evidence = tool.get('licence_evidence') or {}
        location = evidence.get('location')
        if (not tool.get('licence_class') or location not in ('runtime','outer-host') or
                not all(str(path or '').startswith('/') for path in (tool.get('path'),evidence.get('path'))) or
                not re.fullmatch(r'[a-f0-9]{64}',evidence.get('sha256') or '')):
            raise ValueError('tool licence evidence missing: '+name)
        if TOOL_LOCATIONS.get(name,location)!=location:
            raise ValueError(('runtime licence evidence must be verified in the runtime: ' if TOOL_LOCATIONS[name]=='runtime'
                              else 'outer-host licence evidence must be verified on the outer host: ')+name)
        present = location=='runtime' or Path(tool['path']).exists() or Path(evidence['path']).exists()
        if not present:
            results[name] = 'outer-host-absent'
            continue
        if not Path(evidence['path']).is_file() or digest(evidence['path'])!=evidence['sha256']:
            raise ValueError('tool licence evidence missing or changed: '+name)
        results[name] = 'verified'
    names = [tool.get('name') for tool in tools]
    if len(set(names))!=len(names) or set(names)!=set(TOOL_LOCATIONS):
        raise ValueError('closure tool set differs from policy: listed '+', '.join(sorted(map(str,names)))+
                         '; expected '+', '.join(sorted(TOOL_LOCATIONS)))
    return results


def image_context(tools, *, container=CONTAINER_MARKER, record=IMAGE_CONTEXT):
    """Positive evidence that this process runs inside an agent-ee image carrying the pinned runtime.

    All of: Docker's container marker, the image context record with the exact
    expected content, and every runtime tool binary matching its closure hash.
    Returns the problems; an empty list is the evidence.
    """
    problems = []
    if not Path(container).is_file():
        problems.append('no container marker '+str(container))
    try:
        if json.loads(Path(record).read_text())!=IMAGE_CONTEXT_RECORD:
            problems.append('image context record differs: '+str(record))
    except (OSError,ValueError):
        problems.append('no image context record '+str(record))
    for tool in tools:
        if TOOL_LOCATIONS.get(tool.get('name'))=='runtime':
            path = Path(tool.get('path') or '')
            if not path.is_file() or digest(path)!=tool.get('sha256'):
                problems.append('runtime binary differs from closure: '+tool['name'])
    return problems


def font_licences(packages, in_image):
    """Digest each image font licence file where it exists; it must exist inside the image.

    A present file must match byte for byte. Outside the image an absent file is
    'unknown', never verified. The package set must match FONT_PACKAGES exactly.
    """
    results = {}
    for package in packages:
        name = package.get('package') or '?';evidence = package.get('licence_evidence') or {}
        if (not package.get('licence_class') or evidence.get('location')!='image' or
                not str(evidence.get('path') or '').startswith('/') or
                not re.fullmatch(r'[a-f0-9]{64}',evidence.get('sha256') or '')):
            raise ValueError('font licence evidence missing: '+name)
        if name in results:
            raise ValueError('font package listed twice: '+name)
        path = Path(evidence['path'])
        if not path.is_file() and not in_image:
            results[name] = 'unknown'
            continue
        if not path.is_file() or digest(path)!=evidence['sha256']:
            raise ValueError('font licence evidence missing or changed: '+name)
        results[name] = 'verified'
    if set(results)!=FONT_PACKAGES:
        raise ValueError('closure font package set differs from policy: listed '+', '.join(sorted(results))+
                         '; expected '+', '.join(sorted(FONT_PACKAGES)))
    return results


def closure_evidence(closure, **context_paths):
    """The closure's tools against the code-owned location policy, and every licence result."""
    licences = tool_licences(closure['tools'])
    tools = {t['name']:t for t in closure['tools']}
    if any(not re.fullmatch(r'[a-f0-9]{64}',tools[name].get('sha256') or '') for name in TOOL_LOCATIONS):
        raise ValueError('native runtime not pinned in closure')
    context = image_context(closure['tools'],**context_paths)
    for name,result in licences.items():
        if result=='outer-host-absent' and context:
            licences[name] = 'unknown'
    fonts = font_licences(closure['fonts']['packages'],not context)
    return {'licence_evidence':licences,'font_licence_evidence':fonts,
            'image_context':{'established':not context,'problems':context},
            'unverified':sorted(n for n,r in {**licences,**fonts}.items() if r!='verified')}


def verify_closure(manifest_path, cache, closure_path, runs=(), *, stock='/usr/share/kicad'):
    """Refuse unless the committed closure lists exactly what the pinned inputs and runs depend on."""
    manifest = load_manifest(manifest_path);cache = external(cache)
    closure = read_json(closure_path)
    if closure.get('schema')!=CLOSURE_SCHEMA or closure.get('manifest_sha256')!=digest(manifest_path):
        raise ValueError('dependency closure belongs to a different manifest')
    if closure['image_id']!=manifest['image_id']:
        raise ValueError('closure image differs from the pinned image')
    listed = {b['board_id']:b for b in closure['boards']}
    if set(listed)!={b['id'] for b in manifest['boards']}:
        raise ValueError('closure board set differs from manifest')
    counts = {}
    for board in manifest['boards']:
        if owner_source(board):
            raise ValueError('dependency closure covers the public corpus only')
        entry = listed[board['id']];found = design_dependencies(board,cache/board['id']/'tree')
        if entry['hardware_grant']!=board['license']['hardware_grant']:
            raise ValueError('closure hardware grant differs: '+board['id'])
        for key,kind in (('symbol_libraries','symbol'),('footprint_libraries','footprint')):
            names = [row['nickname'] for row in entry[key]]
            if names!=found[key]:
                raise ValueError(f'{board["id"]} {key} not closed: listed {names}, found {found[key]}')
            for row in entry[key]:
                if (row['origin']!=library_origin(kind,row['nickname'],found['project_tables'],stock) or
                        row.get('licence_class')!=LIBRARY_LICENCES[row['origin']]):
                    raise ValueError(f'{board["id"]} {kind} library origin or licence differs: {row["nickname"]}')
        if entry['model_roots']!=found['model_roots'] or entry['project_tables']!=found['project_tables']:
            raise ValueError(f'{board["id"]} model roots or project tables not closed')
        counts[board['id']] = {k:len(found[k]) for k in found}
    alternates = sorted({(c['mutation']['alternate_source'].rsplit('/',1)[-1],c['mutation']['alternate_sha256'])
        for c in manifest['cases'] if c['mutation']['operation']=='replace_embedded_physical_footprint'
        and 'alternate_git' not in c['mutation']})
    library = closure['alternate_library']
    if [(a['name'],a['sha256']) for a in library['footprints']]!=alternates:
        raise ValueError('alternate footprint closure differs from mutations')
    record = Path(manifest_path).parent/'alternate-library.json'
    if digest(record)!=library['record_sha256']:
        raise ValueError('alternate library record changed')
    for name,expected in alternates:
        if digest(safe_path(cache/'alternate-footprints',name))!=expected:
            raise ValueError('cached alternate footprint differs: '+name)
    if digest(cache/'alternate-footprints'/'LICENSE.md')!=read_json(record)['license_sha256']:
        raise ValueError('cached alternate library license differs')
    tools = {t['name']:t for t in closure['tools']}
    evidence = closure_evidence(closure)
    substitutions = {}
    checked = []
    for run in runs:
        run = Path(run);request = read_json(run/'request.json')
        if request['image_id']!=closure['image_id'] or request['manifest_sha256']!=closure['manifest_sha256']:
            raise ValueError('run image or manifest differs from closure')
        boards = {b['id']:b for b in manifest['boards']}
        for entry in read_json(run/'runs/index.json')['records']:
            result = read_json(run/'runs'/entry['id']/'result.json')
            scope = read_json(run/'runs'/entry['id']/'candidate.json')
            board = boards[scope['scope']['name']]
            allowed = {*board['native_file_sha256'],*board['captured_dependency_sha256'],*BENCHMARK_INPUTS}
            if not set(scope['files'])<=allowed:
                raise ValueError('run consumed an input outside the closure: '+', '.join(sorted(set(scope['files'])-allowed)))
            required = set(result['launch_expectation']['required_runtime'])
            if not required <= set(tools):
                raise ValueError('run required an unlisted runtime: '+', '.join(sorted(required-set(tools))))
            for name in NATIVE_RUNTIME:
                if result['runtime_files'][name]['sha256']!=tools[name]['sha256']:
                    raise ValueError(f'run runtime {name} differs from closure')
            for check in result['checks']:
                version = check.get('details',{}).get('kicad_version')
                if check['kind'] in ('erc','drc') and version!=tools['kicad-cli']['version']:
                    raise ValueError('native KiCad version differs from closure')
                if check['kind'] in ('erc','drc'):
                    substitutions.update(font_substitutions(run/'runs'/entry['id'],check['id']))
        checked.append(str(run))
    if runs and substitutions!=closure['fonts']['substitutions']:
        raise ValueError(f'run font substitutions {substitutions} differ from closure')
    return {'closed':not evidence['unverified'],'boards':counts,'alternate_footprints':len(alternates),'tools':sorted(tools),
            **evidence,'runs':checked}
