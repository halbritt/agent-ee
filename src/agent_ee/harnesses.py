"""Native subscription adapters adapted from Relay/Council's observed CLI contracts."""
import json
import os
import shutil
from pathlib import Path

from .executor import execute
from .storage import digest, write_json


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
        return json.loads(answers[-1]), {'harness_session': start['thread_id'], 'usage': end['usage'],
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
    value = end['structured_output'] if 'structured_output' in end else json.loads(end['response'])
    return value, {'harness_session': start['conversation_id'],
        'model_observed': start['init']['model'], 'usage': end.get('usage', {})}


def native_call(run_dir, label, definition, prompt, schema, timeout, *, cancellation=None):
    harness = definition['harness']
    model, effort = definition['model'], definition['effort']
    tools = definition.get('tool_mode', 'investigate') == 'investigate'
    work = Path(run_dir) / 'review-work' / label
    home = work / 'home'
    home.mkdir(parents=True)
    (work / 'experiments').mkdir()
    record = {'backend': harness, 'model_requested': model, 'effort': effort,
        'billing_route': 'subscription', 'execution': 'unavailable', 'limitations': []}
    try:
        executable = executable_for(harness)
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
            argv = ['/reviewer', 'exec', '--json', '--ignore-user-config', '--ignore-rules', '--ephemeral',
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
        record.update(execute(Path(run_dir) / 'inputs', work, argv, stdin=stdin, timeout=timeout,
            network=True, extra_ro=mounts, cancellation=cancellation))
        if record['execution'] != 'ok':
            record['error'] = 'harness execution failed; inspect retained console; no fallback attempted'
            return record, None
        try:
            value, observed = parse_stream(harness, (work / 'stdout.log').read_text(), model, tools)
            if not isinstance(value, dict):
                raise ValueError('review response must be an object')
            if harness == 'codex' and isinstance(value.get('decisions'), list):
                for decision in value['decisions']:
                    if decision.get('duplicate_of') is None:
                        decision.pop('duplicate_of', None)
            record.update(observed)
            return record, value
        except RuntimeError as exc:
            record.update(execution='failed', error=str(exc))
        except (ValueError, KeyError, TypeError) as exc:
            record.update(execution='malformed', error=str(exc))
        return record, None
    finally:
        shutil.rmtree(home)


def evidence_mounts(run_dir, label):
    """Completed native checks and, only for adjudication, reviewer experiments."""
    run = Path(run_dir)
    mounts = [(run / name, '/evidence/' + name) for name in ('checks', 'work') if (run / name).is_dir()]
    if (run / 'baseline.json').is_file():
        mounts.append((run / 'baseline.json', '/evidence/baseline.json'))
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
