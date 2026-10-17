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
from .storage import digest, identity, read_json, write_json

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
    for b in boards:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',b['repo']) or not re.fullmatch(r'[a-f0-9]{40}',b['commit']):
            raise ValueError('unpinned upstream source')
        if not b['license']['hardware_grant'] or b['license']['publication'] != 'not authorized; no repository copy':
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
    return manifest


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


def fetch(manifest_path, cache, *, archives=None, footprint_library='/usr/share/kicad/footprints/Connector_PinHeader_1.27mm.pretty'):
    manifest = load_manifest(manifest_path)
    cache = fresh(cache)
    for board in manifest['boards']:
        target = cache/board['id'];target.mkdir()
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
    for case in manifest['cases']:
        m = case['mutation']
        if m['operation'] == 'replace_embedded_physical_footprint':
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
                ref = predicate.pop('reference')
                selector = next(s for s in case['source_selectors'] if s['reference']==ref)
                contract['components'][ref] = {'locations':[{'sheet':'/','reference':ref}],
                    'symbols':[selector['schematic']['lib_id']]}
                pin = predicate.pop('pin',None)
                if pin is None:
                    predicate['component'] = ref
                else:
                    tid = f'{ref}-{pin}'
                    contract['terminals'][tid] = {'component':ref,'pin':pin}
                    predicate['terminal'] = tid
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
                'scope':'Synthetic assembly, schematic wiring and physical pad geometry benchmark; no functional qualification.',
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


def committed_manifest(manifest_path, commit, repository=REPOSITORY):
    path = Path(manifest_path).resolve();repository = Path(repository).resolve()
    if not re.fullmatch(r'[a-f0-9]{40}',commit):
        raise ValueError('expectation commit must be a full commit identity')
    rel = str(path.relative_to(repository))
    subprocess.run(['git','-C',str(repository),'merge-base','--is-ancestor',commit,'HEAD'],check=True,capture_output=True)
    original = subprocess.run(['git','-C',str(repository),'show',commit+':'+rel],check=True,capture_output=True).stdout
    if original != path.read_bytes():
        raise ValueError('expectation bytes differ from committed preregistration')
    manifest = load_manifest(path)
    if len(manifest['boards'])<5 or len(manifest['cases'])<30:
        raise ValueError('five boards and thirty committed mutations required')
    return manifest


def check_prepared(prepared, manifest_path):
    prepared = external(prepared)
    record = read_json(prepared/'prepared.json')
    if record['manifest_sha256']!=digest(manifest_path):
        raise ValueError('prepared manifest mismatch')
    actual = {str(p.relative_to(prepared)):digest(p) for p in prepared.rglob('*') if p.is_file() and p!=prepared/'prepared.json'}
    for path in prepared.rglob('*'):
        if path.is_symlink():
            raise ValueError('prepared directory gained symlink')
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


def run(manifest_path, cache_prepared, output, expectation_commit, image):
    manifest = committed_manifest(manifest_path,expectation_commit)
    prepared = external(cache_prepared);record = check_prepared(prepared,manifest_path)
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image) or image!=manifest['image_id']:
        raise ValueError('image differs from pinned immutable image identity')
    actual = subprocess.run(['docker','image','inspect',image,'--format','{{.Id}}'],capture_output=True,text=True,check=True).stdout.strip()
    if actual!=image:
        raise ValueError('Docker image identity mismatch')
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
    request = {'schema':'agent-ee.benchmark-request/1','expectation_commit':expectation_commit,
        'manifest_sha256':digest(manifest_path),'implementation_commit':implementation,
        'implementation_archive_sha256':sha(archive),'image_id':image,'prepared_sha256':digest(prepared/'prepared.json'),
        'strict_toolchain':True,'model_pass':False}
    write_json(output/'request.json',request)
    command = ['docker','run','--rm','--init','--name','agent-ee-21-'+output.name,
        '--network','none','--user',f'{os.getuid()}:{os.getgid()}','-e','HOME=/tmp',
        '-e','AGENT_EE_REQUIRE_TOOLCHAIN=1','--security-opt','seccomp=unconfined',
        '--security-opt','apparmor=agent-ee-container','--security-opt','systempaths=unconfined',
        '-v',f'{source}:/opt/agent-ee:ro','-v',f'{prepared}:{prepared}:ro',
        '-v',f'{output}:{output}','--entrypoint','/usr/bin/python3',image,
        '/opt/agent-ee/scripts/seeded_benchmark.py','_execute',str(prepared),str(output/'runs')]
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
    if check_prepared(prepared,manifest_path)!=record:
        raise ValueError('prepared input changed during execution')
    return output


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
        raw = None
        if check['kind'] in ('drc','erc'):
            path = Path(run)/'work'/cid/(check['kind']+'.json')
            if path.exists():
                raw = read_json(path)
                # These native fields are provenance/time only. Preserve all
                # actual violation geometry, item UUIDs, rule types and values.
                for key in ('date','kicad_version','source'):
                    raw.pop(key,None)
        records[cid] = {'check':canonical_check(check),'native':raw}
    return records


def classify(expected, record, case, control=None):
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
        target = {s['pcb']['footprint_uuid'] for s in case['source_selectors']}
        if section=='unconnected_items':
            target = {p['uuid'] for p in case['mutation']['before_pad_geometry'] if p['number']!='1'}
        def identities(row):
            return {item['uuid'] for item in row.get('items',[]) if 'uuid' in item}
        baseline = control['native'][section] if control and control['native'] is not None else []
        if control is not None and (control['native'] is None or control['check']['execution']!='ok'):
            return 'control_unresolved'
        matched = [r for r in raw[section] if identities(r)&target and r not in baseline]
        return 'detection' if matched else 'miss'
    raise ValueError('no qualified matcher for expected check kind')


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
    counts = {k:{'detection':0,'miss':0,'unsupported':0,'not_evaluated':0,'execution_error':0,'control_unresolved':0} for k in ('hardware_interface','drc','erc','netlist')}
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
        statuses = {p['status'] for p in predictions}
        detected += 'detection' in statuses
        evaluated += bool(statuses & {'detection','miss'})
        cases.append({'id':case['id'],'predictions':predictions})
    controls = []
    unresolved = {k:0 for k in counts}
    for board in manifest['boards']:
        control = observed[board['id']+'-control']
        for r in control.values():
            unresolved[r['check']['kind']] += finding_count(r)
        controls.append({'id':board['id']+'-control','checks':[
            {'id':cid,'kind':r['check']['kind'],'execution':r['check']['execution'],
             'outcome':r['check']['outcome'],'finding_count':finding_count(r),'adjudication':'unresolved; not automatically a false alarm'}
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
        for entry in other_index['records']:
            run = Path(second)/'runs'/entry['id']
            if digest(run/'result.json')!=entry['result_sha256']:
                raise ValueError('rerun result changed')
            if observations(run)!=observed[entry['id']]:
                differences.append(entry['id'])
        comparison = {'same_check_results':not differences,'different_cases':differences,
            'normalization':'Exclude timing/candidate/run IDs and evidence hashes; compare parsed native records and exact details/locators/numerical values.'}
    return {'schema':'agent-ee.benchmark-report/1','synthetic':True,'model_pass':False,
        'expectation_commit':request['expectation_commit'],'image_id':request['image_id'],
        'per_kind':counts,'cases':cases,'controls':controls,
        'seeded_detection':{'detected':detected,'all_seeds':len(cases),'evaluated_seeds':evaluated,
            'all_seed_fraction':detected/len(cases),'evaluated_fraction':detected/evaluated if evaluated else None},
        'false_alarms':{'per_kind':{k:{'confirmed':0,'unresolved_control_findings':unresolved[k],'negative_opportunities':0,'rate':None} for k in counts},
            'status':'No control findings independently adjudicated; zero confirmed is not proof of no false alarms.'},
        'rerun':comparison,'limitations':manifest['limitations']}


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
