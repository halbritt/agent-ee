"""Explicit, versioned subscription routes; no implicit model or fallback."""
import math
import re


def validate_profile(profile):
    if not isinstance(profile, dict) or profile.get('schema') != 'agent-ee.profile/1':
        raise ValueError('explicit agent-ee.profile/1 review profile required')
    if not isinstance(profile.get('name'), str) or not profile['name']:
        raise ValueError('profile name required')
    if profile.get('tool_mode', 'investigate') not in ('investigate', 'fixed_packet'):
        raise ValueError('tool_mode must be investigate or fixed_packet')
    reviewers = profile.get('reviewers')
    if not isinstance(reviewers, list) or len(reviewers) < 2:
        raise ValueError('profile requires at least two reviewers')
    roles = set()
    for reviewer in reviewers:
        role = reviewer.get('role', '')
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', role) or role == 'adjudicator' or role in roles:
            raise ValueError('invalid or duplicate profile reviewer role')
        roles.add(role)
    for definition in [*reviewers, profile.get('adjudicator', {})]:
        harness = definition.get('harness')
        if harness not in ('claude', 'codex', 'agy'):
            raise ValueError('profile harness must be claude, codex or agy')
        model = definition.get('model')
        if not isinstance(model, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}', model):
            raise ValueError('explicit native model name required')
        efforts = {'claude': ('low', 'medium', 'high', 'max'),
                   'codex': ('low', 'medium', 'high', 'xhigh', 'max'),
                   'agy': ('low', 'medium', 'high', 'xhigh', 'max')}
        if definition.get('effort') not in efforts[harness]:
            raise ValueError('explicit supported effort required')
        if harness == 'agy' and not model.endswith('-' + definition['effort']):
            raise ValueError('agy effort must match model tier')
        seconds = definition.get('timeout_s')
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 1 <= seconds <= 3600:
            raise ValueError('timeout_s must be finite and between 1 and 3600')
    return profile
