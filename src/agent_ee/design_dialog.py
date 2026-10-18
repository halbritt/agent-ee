"""Dependency-aware, deterministic owner question capture; no model calls."""
import argparse
import hashlib
from pathlib import Path
import re

from .design_intent import load_json, write_artifacts
from .hardware_interface import exact_keys, text
from .storage import canonical


def stable_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,127}', value):
        raise ValueError('IDs must be stable ASCII names, starting with a letter')
    return value


def validate_requirements(raw):
    value = load_json(raw)
    exact_keys(value, 'schema requirements')
    if value['schema'] != 'agent-ee.requirements/1' or not isinstance(value['requirements'], list):
        raise ValueError('invalid original requirements')
    ids = set()
    for requirement in value['requirements']:
        exact_keys(requirement, 'id text source_question_ids')
        rid = stable_id(requirement['id'])
        if rid in ids:
            raise ValueError('duplicate original requirement ID')
        ids.add(rid)
        text(requirement['text'])
        sources = requirement['source_question_ids']
        if not isinstance(sources, list) or any(stable_id(q) != q for q in sources) or len(set(sources)) != len(sources):
            raise ValueError('invalid original requirement sources')
    return value['requirements']


def validate_questions(raw, original):
    value = load_json(raw)
    exact_keys(value, 'schema context questions')
    if value['schema'] != 'agent-ee.questions/1' or not isinstance(value['questions'], list):
        raise ValueError('invalid question batch')
    text(value['context'])
    questions, requirement_ids = {}, {r['id'] for r in original}
    for question in value['questions']:
        exact_keys(question, 'id question options default depends_on requirement_id')
        qid = stable_id(question['id'])
        rid = stable_id(question['requirement_id'])
        if qid in questions or rid in requirement_ids:
            raise ValueError('duplicate question ID or requirement ID collision')
        requirement_ids.add(rid)
        text(question['question'])
        options = question['options']
        if not isinstance(options, list) or not options or any(not isinstance(v, str) or not v.strip() for v in options):
            raise ValueError('options must be nonempty text list')
        if len(set(options)) != len(options) or question['default'] not in options:
            raise ValueError('duplicate options or default outside options')
        dependencies = question['depends_on']
        if not isinstance(dependencies, list) or any(stable_id(d) != d for d in dependencies) or len(set(dependencies)) != len(dependencies):
            raise ValueError('invalid dependencies')
        questions[qid] = question
    if any(d not in questions for q in questions.values() for d in q['depends_on']):
        raise ValueError('unknown question dependency')
    # Stable frontier order; source question ordering has no decision authority.
    batches, resolved = [], set()
    while len(resolved) < len(questions):
        batch = sorted(qid for qid, q in questions.items() if qid not in resolved and set(q['depends_on']) <= resolved)
        if not batch:
            raise ValueError('cyclic question dependencies')
        batches.append(batch)
        resolved.update(batch)
    return questions, batches


def resolve_answers(raw, hashes, questions):
    if raw is None:
        return {}, [{'code': 'answers_missing'}]
    try:
        value = load_json(raw)
        exact_keys(value, 'schema questions_sha256 idea_sha256 requirements_sha256 answers')
        if value['schema'] != 'agent-ee.answers/1' or not isinstance(value['answers'], dict):
            raise ValueError('invalid answer schema')
        if any(value[key] != expected for key, expected in hashes.items()):
            raise ValueError('answers do not bind to current inputs')
        if value['answers'].keys() - questions.keys():
            raise ValueError('answer refers to unknown question')
        answers, findings = {}, []
        for qid, question in questions.items():
            if qid not in value['answers']:
                findings.append({'code': 'answer_missing', 'question_id': qid})
                continue
            answer = value['answers'][qid]
            if isinstance(answer, dict) and set(answer) == {'use_default'} and answer['use_default'] is True:
                answers[qid] = {'value': question['default'], 'defaulted': True}
            elif isinstance(answer, dict) and set(answer) == {'value'} and answer['value'] in question['options']:
                answers[qid] = {'value': answer['value'], 'defaulted': False}
            else:
                findings.append({'code': 'answer_invalid', 'question_id': qid})
        return answers, findings
    except RecursionError:
        return {}, [{'code': 'answers_too_deep',
                     'reason': 'answers JSON nesting exceeds decoder recursion limit'}]
    except (ValueError, UnicodeError) as exc:
        return {}, [{'code': 'answers_invalid', 'reason': str(exc)}]


def capture_dialog(idea_raw, questions_raw, answers_raw, requirements_raw):
    """Freeze original inputs and derive only when every answer is explicit.

    The return value is a new artifact set, not a mutation of a scope baseline.
    Answer/schema failures confer no derived requirements and no acceptance.
    """
    text(idea_raw.decode('utf-8'))
    original = validate_requirements(requirements_raw)
    questions, batches = validate_questions(questions_raw, original)
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in [
        ('questions_sha256', questions_raw), ('idea_sha256', idea_raw), ('requirements_sha256', requirements_raw)]}
    answers, findings = resolve_answers(answers_raw, hashes, questions)
    artifacts = {'idea.md': idea_raw, 'questions.json': questions_raw,
                 'original-requirements.json': requirements_raw}
    if answers_raw is not None:
        artifacts['answers.json'] = answers_raw
    ready, satisfied = [], set()
    for batch in batches:
        for qid in batch:
            if all(dep in satisfied for dep in questions[qid]['depends_on']):
                if qid in answers:
                    satisfied.add(qid)
                else:
                    ready.append(qid)
    report = {'schema': 'agent-ee.dialog/1', **hashes,
              'answers_sha256': hashlib.sha256(answers_raw).hexdigest() if answers_raw is not None else None,
              'answers': answers, 'findings': findings, 'question_batches': batches,
              'ready_question_ids': ready, 'outcome': 'unknown' if findings else 'pass',
              'acceptance_ready': False}
    if not findings:
        requirements, ancestors = list(original), {}
        for batch in batches:
            for qid in batch:
                question = questions[qid]
                inherited = {source for dep in question['depends_on'] for source in ancestors[dep]}
                inherited.add(qid)
                sources = [key for group in batches for key in group if key in inherited]
                ancestors[qid] = sources
                requirements.append({'id': question['requirement_id'],
                    'text': question['question'] + '\nAnswer: ' + answers[qid]['value'],
                    'source_question_ids': sources})
        artifacts['requirements.json'] = canonical({'schema': 'agent-ee.requirements/1', 'requirements': requirements}) + b'\n'
        artifacts['requirements.md'] = ('\n\n'.join(f"{r['id']}: {r['text']}" for r in requirements) + '\n').encode('utf-8')
    artifacts['dialog.json'] = canonical(report) + b'\n'
    return artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--idea', required=True, type=Path)
    parser.add_argument('--questions', required=True, type=Path)
    parser.add_argument('--answers', required=True, type=Path)
    parser.add_argument('--requirements', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    try:
        try:
            answers = args.answers.read_bytes()
        except FileNotFoundError:
            answers = None
        artifacts = capture_dialog(args.idea.read_bytes(), args.questions.read_bytes(), answers,
                                   args.requirements.read_bytes())
        write_artifacts(artifacts, args.out)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'dialog capture unavailable: {exc}\n')
    return 0 if load_json(artifacts['dialog.json'])['outcome'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
