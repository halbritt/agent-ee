"""Native subscription adapters adapted from Relay/Council's observed CLI contracts."""
import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path
from uuid import UUID


from . import native_run
from .storage import digest, write_json
from .review_run import OutputFormatRefusal


def structured_payload(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise OutputFormatRefusal(str(exc)) from exc


def executable_for(harness):
    override = os.environ.get('AGENT_EE_' + harness.upper() + '_BINARY')
    if override:
        return str(Path(override).expanduser().resolve())
    if harness == 'codex':
        # The host's codex command is a coordination wrapper. Mount the native
        # executable alone so no host session hooks or memory run in reviewers.
        native = Path.home() / '.npm-global/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex'
        if native.is_file():
            return str(native)
    return shutil.which(harness)


def parse_stream(harness, text, model, tools=True):
    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    if any(not isinstance(e, dict) for e in events):
        raise ValueError('non-object harness event')
    if any(e.get('type') in ('error', 'turn.failed') or e.get('event') == 'error' for e in events):
        raise RuntimeError('native provider error; no fallback attempted')
    def unique(key, value):
        found = [e for e in events if e.get(key) == value]
        if len(found) != 1:
            raise ValueError('missing or duplicate terminal/session event')
        return found[0]
    if harness == 'codex':
        start = unique('type', 'thread.started')
        end = unique('type', 'turn.completed')
        answers = []
        for e in events:
            if e.get('type') in ('item.started', 'item.updated', 'item.completed'):
                item = e['item']
                if item.get('type') == 'error' and item.get('message', '').startswith('Code Mode is unavailable because code-mode host is disabled.'):
                    continue
                if not tools and item.get('type') not in ('agent_message', 'reasoning'):
                    raise ValueError('unexpected harness tool activity')
                if e['type'] == 'item.completed' and item['type'] == 'agent_message':
                    answers.append(item['text'])
        if not answers or not start.get('thread_id') or not isinstance(end.get('usage'), dict):
            raise ValueError('incomplete Codex response')
        return structured_payload(answers[-1]), {'harness_session': start['thread_id'], 'usage': end['usage'],
            'model_observed': None, 'identity_limitation': 'Codex JSONL does not attest the server model; requested model recorded.'}
    start = unique('event', 'init')
    end = unique('event', 'result')['result']
    if end.get('status') != 'SUCCESS':
        raise RuntimeError('Agy terminal status was not SUCCESS; no fallback attempted')
    if start['init']['model'] != model or not start.get('conversation_id') or start['conversation_id'] != end.get('conversation_id'):
        raise ValueError('Agy model/session mismatch')
    for e in events:
        if not tools and e.get('event') == 'step_update' and e['step_update'].get('step_type') not in ('user_input', 'agent_response'):
            raise ValueError('unexpected harness tool activity')
    value = end['structured_output'] if 'structured_output' in end else structured_payload(end['response'])
    return value, {'harness_session': start['conversation_id'],
        'model_observed': start['init']['model'], 'usage': end.get('usage', {})}


def codex_model_identity(home, session):
    """Project CLI metadata only; never retain rollout content or infer from argv."""
    evidence = {'schema': 'agent-ee.codex-model/1', 'harness_session': session,
        'model_observed': None, 'identity_limitation': 'Codex session model metadata unavailable; observed model is UNKNOWN.'}
    def unknown(reason):
        evidence['identity_limitation'] = reason + '; observed model is UNKNOWN.'
        return evidence
    try:
        if str(UUID(session)) != session:
            return unknown('Invalid Codex session identifier')
        sessions = home / '.codex/sessions'
        paths = list(sessions.rglob('rollout-*-' + session + '.jsonl'))
        if len(paths) != 1:
            return unknown('Missing or ambiguous Codex session rollout')
        path = paths[0]
        # Refuse links in every path component, including the sessions directory.
        if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(home)):
            return unknown('Linked Codex session metadata refused')
        metas, models, sha = [], set(), hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return unknown('Non-regular Codex session metadata refused')
            for line in stream:
                sha.update(line)
                row = json.loads(line)
                if not isinstance(row, dict):
                    return unknown('Malformed Codex session metadata')
                if row.get('type') == 'session_meta':
                    metas.append(row['payload'])
                elif row.get('type') == 'turn_context':
                    model = row['payload']['model']
                    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', model):
                        return unknown('Invalid Codex turn model metadata')
                    models.add(model)
        if len(metas) != 1 or not isinstance(metas[0], dict) or metas[0].get('id') != session:
            return unknown('Codex rollout session mismatch or ambiguity')
        if len(models) != 1:
            return unknown('Missing or conflicting Codex turn model metadata')
        version = metas[0].get('cli_version')
        if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9_.+-]{1,128}', version):
            return unknown('Missing or invalid Codex CLI version metadata')
        provider = metas[0].get('model_provider')
        if provider is None:
            return unknown('Missing Codex provider metadata')
        if provider != 'openai':
            evidence['identity_error'] = 'Codex session provider differs from the subscription route; no fallback attempted'
            return unknown('Unexpected Codex provider metadata')
        evidence.update(model_observed=models.pop(), cli_version=version, model_provider='openai',
            rollout_sha256=sha.hexdigest(),
            identity_limitation='Observed CLI turn-context model; this does not attest the server deployment.')
        return evidence
    except (OSError, ValueError, TypeError, KeyError):
        return unknown('Unreadable or malformed Codex session metadata')


def native_call(run_dir, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
    harness = definition['harness']
    model, effort = definition['model'], definition['effort']
    tools = definition.get('tool_mode', 'investigate') == 'investigate'
    work = Path(run_dir) / 'review-work' / label
    home = work / 'home'
    home.mkdir(parents=True)
    (work / 'experiments').mkdir(exist_ok=True)
    record = {'backend': harness, 'model_requested': model, 'effort': effort,
        'billing_route': 'subscription', 'execution': 'unavailable', 'limitations': []}
    try:
        executable = executable or executable_for(harness)
        if not executable or not Path(executable).is_file():
            record['error'] = 'native executable unavailable; no fallback attempted'
            return record, None
        if Path(executable).read_bytes()[:4] != b'\x7fELF':
            record['error'] = 'native ELF binary required; set AGENT_EE_' + harness.upper() + '_BINARY (host wrappers refused)'
            return record, None
        if harness == 'codex':
            auth_home = Path(os.environ.get('AGENT_EE_CODEX_AUTH_HOME', str(Path.home() / '.codex'))).expanduser()
            credential = auth_home / 'auth.json'
            guest = '/work/home/.codex/auth.json'
            (home / '.codex').mkdir()
            write_json(work / 'schema.json', strict_schema(schema))
            argv = ['/reviewer', 'exec', '--json', '--ignore-user-config', '--ignore-rules',
                '--sandbox', 'danger-full-access' if tools else 'read-only', '--skip-git-repo-check', '--color', 'never', '--model', model,
                '--output-schema', '/work/schema.json']
            configs = ['approval_policy="never"', 'forced_login_method="chatgpt"', 'web_search=' + json.dumps('live' if tools else 'disabled'),
                'project_doc_max_bytes=0', 'model_reasoning_effort=' + json.dumps(effort),
                'features.skip_host_skill_discovery=true', 'suppress_unstable_features_warning=true']
            configs += ['features.' + f + '=false' for f in ('apps', 'hooks',
                'multi_agent', 'browser_use', 'computer_use', 'image_generation', 'code_mode', 'code_mode_only',
                'code_mode_host', 'plugins', 'skill_search', 'skill_mcp_dependency_install')]
            if tools:
                configs += ['features.code_mode_host=true']
            if not tools:
                configs += ['features.shell_tool=false', 'features.unified_exec=false']
            for config in configs:
                argv += ['-c', config]
            argv += ['-']
            stdin = prompt
        else:
            credential = Path.home() / '.gemini/antigravity-cli/antigravity-oauth-token'
            guest = '/work/home/.gemini/antigravity-cli/antigravity-oauth-token'
            config = home / '.gemini/antigravity-cli'
            config.mkdir(parents=True)
            write_json(config / 'settings.json', {'model': model, 'toolPermission': 'request-review'})
            argv = ['/reviewer', '--input-format', 'stream-json', '--output-format', 'stream-json',
                '--disable-slash-commands', '--model', model, '--effort', effort, '--mode', 'accept-edits' if tools else 'plan',
                '--json-schema', json.dumps(schema, separators=(',', ':'))]
            if tools:
                argv += ['--dangerously-skip-permissions']
            stdin = json.dumps({'event': 'user', 'message': {'content': prompt}}) + '\n'
            record['limitations'].append('Installed tools and network are available; outer mounts protect the candidate. No hermetic environment is claimed.')
        if not credential.is_file():
            record['error'] = 'subscription credential unavailable; no fallback attempted'
            return record, None
        mounts = [(executable, '/reviewer'), (credential, guest), *evidence_mounts(run_dir, label)]
        if harness == 'codex' and tools:
            host = Path(executable).parent / 'codex-code-mode-host'
            if not host.is_file():
                record['error'] = 'Codex tool host unavailable beside binary'
                return record, None
            mounts += [(host, '/codex-code-mode-host')]
            record['tool_host_sha256'] = digest(host)
        record['harness_binary_sha256'] = digest(executable)
        record.update(
            native_run.run(
                native_run.Intent('model', argv, stdin=stdin, timeout=timeout, network=True),
                inputs=Path(run_dir) / 'inputs',
                work=work,
                mounts=mounts,
                cancel=cancellation,
            )
        )
        if record['execution'] != 'ok':
            record['error'] = 'harness execution failed; inspect retained console; no fallback attempted'
            return record, None
        try:
            value, observed = parse_stream(harness, (work / 'stdout.log').read_text(), model, tools)
            record.update(observed)
            if harness == 'codex':
                identity = codex_model_identity(home, observed['harness_session'])
                identity_path = work / 'model-identity.json'
                write_json(identity_path, identity)
                record.update(model_observed=identity['model_observed'],
                    identity_limitation=identity['identity_limitation'],
                    model_evidence={'path': str(identity_path.relative_to(run_dir)), 'sha256': digest(identity_path)})
                if identity.get('identity_error'):
                    raise RuntimeError(identity['identity_error'])
                if identity['model_observed'] is not None and identity['model_observed'] != model:
                    raise RuntimeError('Codex observed model differs from requested model; no fallback attempted')
            if not isinstance(value, dict):
                raise OutputFormatRefusal('review response must be an object')
            if harness == 'codex' and isinstance(value.get('decisions'), list):
                for decision in value['decisions']:
                    if decision.get('duplicate_of') is None:
                        decision.pop('duplicate_of', None)
            return record, value
        except OutputFormatRefusal as exc:
            record.update(execution='malformed', refusal_kind='structured_output', error=str(exc))
        except RuntimeError as exc:
            record.update(execution='failed', error=str(exc))
        except (ValueError, KeyError, TypeError) as exc:
            record.update(execution='malformed', error=str(exc))
        return record, None
    finally:
        shutil.rmtree(home)


def evidence_mounts(run_dir, label):
    """The review packet, completed native checks and, only for adjudication, reviewer experiments."""
    run = Path(run_dir)
    mounts = [(run / name, '/evidence/' + name) for name in ('checks', 'work') if (run / name).is_dir()]
    if (run / 'packet.json').is_file():
        mounts.append((run / 'packet.json', '/evidence/packet.json'))
    if (run / 'baseline.json').is_file():
        mounts.append((run / 'baseline.json', '/evidence/baseline.json'))
        if label == 'implementation':
            from .violation_waivers import writer_mounts
            mounts += writer_mounts(run)
    control = run / 'review-validation' / label
    if control.is_dir():
        mounts += [(control, '/review-validation'),
                   (run / 'inputs', '/evidence/inputs'),
                   (run / 'candidate.json', '/evidence/candidate.json')]
        experiments = run / 'review-work' / label / 'experiments'
        for parent in (run / 'review-work', experiments.parent, experiments):
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise ValueError('review experiment mount requires an unaliased directory')
        experiments.mkdir(parents=True, exist_ok=True)
        mounts.append((experiments, f'/evidence/review-work/{label}/experiments'))
    if label == 'adjudicator':
        mounts += [(p, '/experiments/' + p.parent.name) for p in (run / 'review-work').glob('*/experiments')]
    return mounts


def strict_schema(schema):
    """Codex requires every object property required; optional values admit null."""
    import copy
    result = copy.deepcopy(schema)
    def visit(node):
        if not isinstance(node, dict):
            return
        if node.get('type') == 'object':
            properties = node.get('properties', {})
            required = node.get('required', [])
            for key, value in properties.items():
                visit(value)
                if key not in required:
                    properties[key] = {'anyOf': [value, {'type': 'null'}]}
            node['required'] = list(properties)
            node['additionalProperties'] = False
        if 'items' in node:
            visit(node['items'])
    visit(result)
    return result
