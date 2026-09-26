"""Closed production snapshot schema; no I/O or fixture fallback."""
import hashlib
import json

PROFILES = ('majak', 'quantlab')
KINDS = ('herdr', 'kanban', 'router', 'git', 'tests')
MAX_BYTES = 131072


def need(ok):
    if not ok:
        raise ValueError('invalid_metadata')


def keys(value, names):
    need(type(value) is dict and set(value) == set(names.split()))


def number(value):
    return type(value) is int and 0 <= value < 2**53


def hex_id(value, lengths=(64,)):
    return type(value) is str and len(value) in lengths and all(c in '0123456789abcdef' for c in value)


def identifier(value, limit=128):
    return (value is None or type(value) is str and 0 < len(value) <= limit
            and all(c.isascii() and (c.isalnum() or c in '-_./:') for c in value))


def identity(profile, value):
    need(profile in PROFILES and type(value) is str and 0 < len(value) <= 256)
    return hashlib.sha256((profile + '\0' + value).encode()).hexdigest()


def pairs(items):
    out = {}
    for key, value in items:
        need(key not in out)
        out[key] = value
    return out


def reject(_):
    raise ValueError('invalid_metadata')


def parse(data, limit=MAX_BYTES):
    need(type(data) is bytes and len(data) <= limit)
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=reject)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('invalid_metadata') from None


def row(kind, value):
    fields = {'herdr': 'agent status', 'kanban': 'task_id run_id status',
              'router': ('task_id actual_model provider requests input_tokens output_tokens '
                         'cost_microusd fallback_count successful_requests duration_ms'),
              'git': 'commit dirty', 'tests': 'passed failed artifact_digest'}
    keys(value, fields[kind])
    if kind == 'herdr':
        need(value['agent'] in ('majak-hermes', 'majak-codex', 'quantlab-hermes', 'quantlab-codex'))
        need(value['status'] in ('idle', 'working', 'blocked', 'done', 'unknown'))
    elif kind == 'kanban':
        need(hex_id(value['task_id']))
        need(value['run_id'] is None or number(value['run_id']))
        need(value['status'] in ('todo', 'triage', 'ready', 'running', 'blocked', 'done', 'cancelled', 'unknown'))
    elif kind == 'router':
        need(value['task_id'] is None or hex_id(value['task_id']))
        need(identifier(value['actual_model']) and identifier(value['provider'], 64))
        need(number(value['requests']))
        need(all(v is None or number(v) for k, v in value.items()
                 if k not in ('task_id', 'actual_model', 'provider', 'requests')))
    elif kind == 'git':
        need(hex_id(value['commit'], (40, 64)) and type(value['dirty']) is bool)
    else:
        need(number(value['passed']) and number(value['failed']) and hex_id(value['artifact_digest']))


def validate(value):
    keys(value, 'version generated_at sources')
    need(type(value['version']) is int and value['version'] == 1 and number(value['generated_at']))
    need(type(value['sources']) is list and len(value['sources']) == 10)
    seen = set()
    for source in value['sources']:
        keys(source, 'profile kind observed_at data_at status reason rows board_id source_epoch')
        profile, kind = source['profile'], source['kind']
        need(type(profile) is str and profile in PROFILES and type(kind) is str and kind in KINDS)
        need((profile, kind) not in seen)
        seen.add((profile, kind))
        need(number(source['observed_at']) and source['observed_at'] <= value['generated_at'])
        need(source['data_at'] is None or number(source['data_at']))
        need(source['status'] in ('available', 'unavailable'))
        need(source['reason'] in ('ok', 'not_configured', 'source_failed', 'stale'))
        need(type(source['rows']) is list and len(source['rows']) <= 50)
        need((source['status'] == 'available') == (source['reason'] == 'ok'))
        if source['status'] == 'unavailable':
            need(source['rows'] == [] and source['data_at'] is None)
        for name in ('board_id', 'source_epoch'):
            need(source[name] is None or hex_id(source[name]))
        if kind == 'kanban' and source['status'] == 'available':
            need(hex_id(source['board_id']) and hex_id(source['source_epoch']))
        if kind != 'kanban':
            need(source['board_id'] is None and source['source_epoch'] is None)
        for item in source['rows']:
            row(kind, item)
            if kind == 'herdr':
                need(item['agent'].startswith(profile + '-'))
        need(len({json.dumps(r, sort_keys=True) for r in source['rows']}) == len(source['rows']))
    return value


def decode(data):
    try:
        return validate(parse(data))
    except (KeyError, TypeError, ValueError, RecursionError):
        raise ValueError('invalid_metadata') from None


def encode(value):
    data = json.dumps(validate(value), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    need(len(data) <= MAX_BYTES)
    return data


def unavailable(now):
    return {'version': 1, 'generated_at': now, 'sources': [
        dict(profile=p, kind=k, observed_at=now, data_at=None, status='unavailable',
             reason='not_configured', rows=[], board_id=None, source_epoch=None)
        for p in PROFILES for k in KINDS]}


def project(value, profiles, now):
    validate(value)
    need(type(profiles) is tuple and profiles and len(set(profiles)) == len(profiles)
         and all(p in PROFILES for p in profiles) and number(now))
    result = parse(encode(value))
    result['sources'] = [s for s in result['sources'] if s['profile'] in profiles]
    for source in result['sources']:
        if not 0 <= now - source['observed_at'] <= 90:
            source.update(status='unavailable', reason='stale', rows=[], data_at=None)
    return result
