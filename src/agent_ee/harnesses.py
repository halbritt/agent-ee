"""Native subscription adapters adapted from Relay/Council's observed CLI contracts."""
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
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


class StreamIdleTimeout(RuntimeError):
    """A matching AGY session reported an idle response stream."""

    failure_kind = 'stream_idle_timeout'


def validate_provider_schema(schema):
    """Refuse expensive provider grammars; never relax arbitrary local constraints."""
    if isinstance(schema, dict):
        if isinstance(schema.get('pattern'), str) and len(schema['pattern']) > 512:
            raise OutputFormatRefusal('provider schema pattern exceeds 512 characters')
        for child in schema.values():
            validate_provider_schema(child)
    elif isinstance(schema, list):
        for child in schema:
            validate_provider_schema(child)


def provider_output_refusal(console):
    """Recognize explicit schema/output-limit errors, never text in model/tool answers.

    Native CLIs can exit nonzero before their stream parser runs. Inspect only
    error events or unsuccessful terminal results; unknown failures stay failures.
    """
    errors = []
    try:
        events = [json.loads(console)]
    except ValueError:
        events = []
        for line in console.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get('type') in ('error', 'turn.failed') or event.get('event') == 'error':
            errors.append(event.get('error', event))
        elif event.get('event') == 'result' and isinstance(event.get('result'), dict):
            result = event['result']
            if result.get('status') == 'ERROR':
                errors.append(result.get('error'))
        elif event.get('type') == 'result' and event.get('is_error'):
            if isinstance(event.get('errors'), list):
                errors.extend(event['errors'])
            else:
                return None
    refusal = None
    for error in errors:
        message = error.get('message', '') if isinstance(error, dict) else error
        code = error.get('code') if isinstance(error, dict) else None
        if not isinstance(message, str):
            return None
        if (code in ('invalid_json_schema', 'invalid_response_format', 'max_output_tokens', 'max_tokens')
                or re.search(r'\bIncomplete response returned, reason: max_output_tokens\b', message, re.I)
                or re.search(r'\bInvalid (?:JSON schema|schema for (?:response_format|text\.format))\b', message, re.I)):
            refusal = message or code
        else:
            return None
    return refusal


def parse_stream(harness, text, model, tools=True):
    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    if any(not isinstance(e, dict) for e in events):
        raise ValueError('non-object harness event')
    refusal = provider_output_refusal(text)
    if refusal is not None:
        raise OutputFormatRefusal(refusal)
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
    if start['init']['model'] != model or not start.get('conversation_id') or start['conversation_id'] != end.get('conversation_id'):
        raise ValueError('Agy model/session mismatch')
    reason = end.get('finished_reason')
    if reason is not None and reason != 'error':
        raise RuntimeError('Agy turn did not finish: ' + str(reason))
    if end.get('status') != 'SUCCESS':
        error = end.get('error')
        if end.get('status') == 'ERROR' and isinstance(error, str) and re.fullmatch(
                r'API error \(attempt [1-9][0-9]*\): request failed: stream idle timeout '
                r'\((?:[0-9]+(?:\.[0-9]+)?[hms])+\): context deadline exceeded', error):
            raise StreamIdleTimeout(error)
        raise RuntimeError('Agy terminal status was not SUCCESS; no fallback attempted')
    if end.get('finished_reason') is not None:
        raise RuntimeError('Agy turn did not finish: ' + str(end['finished_reason']))
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


def native_call(run_dir, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None,
                log_bytes=None, extra_mounts=()):
    """One subscription seat turn. log_bytes bounds the console the seat may write (and this
    call reads back): past it the turn stops as output_limit. extra_mounts are further
    read-only (host path, sandbox path) binds. Both default to the unbounded, unchanged call."""
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
        try:
            validate_provider_schema(schema)
        except OutputFormatRefusal as exc:
            record.update(execution='malformed', refusal_kind='structured_output', error=str(exc))
            return record, None
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
        mounts = [(executable, '/reviewer'), *evidence_mounts(run_dir, label), *extra_mounts]
        if harness == 'codex' and tools:
            host = Path(executable).parent / 'codex-code-mode-host'
            if not host.is_file():
                record['error'] = 'Codex tool host unavailable beside binary'
                return record, None
            mounts += [(host, '/codex-code-mode-host')]
            record['tool_host_sha256'] = digest(host)
        record['harness_binary_sha256'] = digest(executable)
        with seat_credential(credential) as mounted:
            record.update(
                native_run.run(
                    native_run.Intent('model', argv, stdin=stdin, timeout=timeout, network=True,
                                      log_bytes=log_bytes),
                    inputs=Path(run_dir) / 'inputs',
                    work=work,
                    mounts=[(mounted, guest), *mounts],
                    cancel=cancellation,
                )
            )
        if record['execution'] != 'ok':
            record['error'] = 'harness execution failed; inspect retained console; no fallback attempted'
            if record['execution'] == 'failed':
                refusal = provider_output_refusal((work / 'stdout.log').read_text())
                if refusal is not None:
                    record.update(execution='malformed', refusal_kind='structured_output', error=refusal)
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
            if harness == 'codex':
                value = unstrict_value(value, schema)
            return record, value
        except OutputFormatRefusal as exc:
            record.update(execution='malformed', refusal_kind='structured_output', error=str(exc))
        except StreamIdleTimeout as exc:
            record.update(execution='failed', failure_kind=exc.failure_kind, error=str(exc))
        except RuntimeError as exc:
            record.update(execution='failed', error=str(exc))
        except (ValueError, KeyError, TypeError) as exc:
            record.update(execution='malformed', error=str(exc))
        return record, None
    finally:
        shutil.rmtree(home)


@contextlib.contextmanager
def seat_credential(path):
    """A RAM-only copy of a login credential for one seat to mount read-only.

    A login refresh replaces the credential file. A container's single-file bind then
    holds the unlinked inode, which still reads but can no longer be bind-mounted, so
    every seat started after the refresh would fail. The copy is never in a retained run.
    """
    with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='agent-ee-credential-') as directory:
        copy = Path(directory) / Path(path).name
        shutil.copyfile(path, copy)
        copy.chmod(0o400)
        yield copy


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
        if (run / 'result.json').is_file():
            mounts.append((run / 'result.json', '/evidence/result.json'))
        from . import judgment_context
        if judgment_context.read(run) is not None:
            mounts.append((run / judgment_context.DIRECTORY, '/evidence/' + judgment_context.DIRECTORY))
        mounts += [(p, '/experiments/' + p.parent.name) for p in (run / 'review-work').glob('*/experiments')]
    return mounts


def strict_schema(schema):
    """Codex requires every object property required; optional values admit null."""
    import copy
    result = copy.deepcopy(schema)
    seen = set()

    def visit(node):
        if not isinstance(node, dict):
            return
        node_id = id(node)
        if node_id in seen:
            return
        seen.add(node_id)
        if node.get('type') == 'object' or 'properties' in node:
            properties = node.setdefault('properties', {}) if node.get('type') == 'object' else node.get('properties', {})
            required = node.get('required', [])
            for key, value in list(properties.items()):
                visit(value)
                if key not in required:
                    properties[key] = {'anyOf': [value, {'type': 'null'}]}
            node['required'] = list(properties.keys())
            node['additionalProperties'] = False
        for defs_key in ('$defs', 'definitions'):
            if defs_key in node and isinstance(node[defs_key], dict):
                for def_schema in node[defs_key].values():
                    visit(def_schema)
        for union_key in ('anyOf', 'oneOf', 'allOf'):
            if union_key in node and isinstance(node[union_key], list):
                for branch in node[union_key]:
                    visit(branch)
        if 'items' in node:
            if isinstance(node['items'], dict):
                visit(node['items'])
            elif isinstance(node['items'], list):
                for item in node['items']:
                    visit(item)
        if 'prefixItems' in node and isinstance(node['prefixItems'], list):
            for item in node['prefixItems']:
                visit(item)

    visit(result)
    return result


def unstrict_value(value, original_schema):
    """Normalize structured outputs by stripping synthetic nulls from optional fields.

    Codex strict schema turns optional non-nullable properties into nullable fields
    (anyOf [orig, null]) and marks them required. When the model outputs null for
    these fields, unstrict_value removes them if the original schema did not allow
    null. If null is explicitly allowed in original_schema, None is preserved.
    """
    if original_schema is None or not isinstance(original_schema, dict):
        return value

    def is_nullable(schema, root, visited=None):
        if not isinstance(schema, dict):
            return False
        if visited is None:
            visited = set()
        schema_id = id(schema)
        if schema_id in visited:
            return False
        visited.add(schema_id)
        if '$ref' in schema:
            resolved = resolve_ref(schema['$ref'], root)
            if resolved is not None and resolved is not schema:
                return is_nullable(resolved, root, visited)
        if schema.get('type') == 'null':
            return True
        stype = schema.get('type')
        if isinstance(stype, list) and 'null' in stype:
            return True
        if 'enum' in schema and None in schema['enum']:
            return True
        if 'const' in schema and schema['const'] is None:
            return True
        for key in ('anyOf', 'oneOf'):
            if key in schema and isinstance(schema[key], list):
                if any(is_nullable(sub, root, visited) for sub in schema[key]):
                    return True
        if 'allOf' in schema and isinstance(schema['allOf'], list):
            if any(is_nullable(sub, root, visited) for sub in schema['allOf']):
                return True
        return False

    def resolve_ref(ref, root):
        if not isinstance(ref, str) or not ref.startswith('#/'):
            return None
        parts = ref[2:].split('/')
        cur = root
        for part in parts:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        return cur

    def find_branch(val, branches, root):
        if not isinstance(val, dict):
            return branches[0] if branches else None
        best_branch = None
        best_score = -1
        for branch in branches:
            seen_refs = set()
            while '$ref' in branch:
                ref = branch['$ref']
                if ref in seen_refs:
                    break
                seen_refs.add(ref)
                target = resolve_ref(ref, root)
                if target and isinstance(target, dict):
                    branch = target
                else:
                    break
            props = branch.get('properties', {})
            score = len(set(props) & set(val))
            if score > best_score:
                best_score = score
                best_branch = branch
        return best_branch or (branches[0] if branches else None)

    def walk(val, schema, root, visited_refs=None):
        if schema is None or not isinstance(schema, dict):
            return val
        if visited_refs is None:
            visited_refs = set()
        if '$ref' in schema:
            ref = schema['$ref']
            if ref in visited_refs:
                return val
            visited_refs = set(visited_refs)
            visited_refs.add(ref)
            target = resolve_ref(ref, root)
            if target:
                return walk(val, target, root, visited_refs)

        if isinstance(val, dict):
            for union_key in ('anyOf', 'oneOf'):
                if union_key in schema and isinstance(schema[union_key], list):
                    branch = find_branch(val, schema[union_key], root)
                    if branch:
                        schema = branch
                        break

            properties = dict(schema.get('properties', {}))
            required = set(schema.get('required', []))
            if 'allOf' in schema and isinstance(schema['allOf'], list):
                for sub in schema['allOf']:
                    if '$ref' in sub:
                        sub = resolve_ref(sub['$ref'], root) or sub
                    if isinstance(sub, dict):
                        properties.update(sub.get('properties', {}))
                        required.update(sub.get('required', []))

            result = {}
            for k, v in val.items():
                if k in properties:
                    prop_schema = properties[k]
                    if v is None and k not in required and not is_nullable(prop_schema, root):
                        continue
                    result[k] = walk(v, prop_schema, root)
                else:
                    add_schema = schema.get('additionalProperties')
                    if isinstance(add_schema, dict):
                        result[k] = walk(v, add_schema, root)
                    else:
                        result[k] = v
            return result

        elif isinstance(val, list):
            item_schema = schema.get('items')
            prefix_items = schema.get('prefixItems')
            if isinstance(item_schema, dict):
                return [walk(item, item_schema, root) for item in val]
            elif isinstance(item_schema, list):
                return [walk(item, item_schema[i] if i < len(item_schema) else schema.get('additionalItems', {}), root)
                        for i, item in enumerate(val)]
            elif isinstance(prefix_items, list):
                return [walk(item, prefix_items[i] if i < len(prefix_items) else schema.get('items', {}), root)
                        for i, item in enumerate(val)]
            return val

        return val

    return walk(value, original_schema, original_schema)

