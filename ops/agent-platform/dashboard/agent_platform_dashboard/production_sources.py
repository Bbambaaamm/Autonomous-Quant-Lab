"""Exporter-only fixed read projections. Never imported by the HTTP application."""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import selectors
import signal
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import quote

from . import production_contract as c
from .production_io import read, regular

SAFE_ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'HOME': '/nonexistent', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}


def command(argv, *, env=None, limit=65536, timeout=3):
    """Bound bytes while draining; no communicate() allocation of unlimited output."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=SAFE_ENV if env is None else env,
                               start_new_session=True, close_fds=True)
    deadline = time.monotonic() + timeout
    result = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                left = deadline - time.monotonic()
                c.need(left > 0)
                if not selector.select(left):
                    raise ValueError('source_timeout')
                chunk = os.read(process.stdout.fileno(), min(8192, limit + 1 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
                c.need(len(result) <= limit)
        c.need(process.wait(timeout=max(0.01, deadline - time.monotonic())) == 0)
        return bytes(result)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=1)
        process.stdout.close()


MAX_DATABASE_BYTES = 64 * 1024 * 1024


def _same_file(before, after):
    return (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)


def _sidecar(stack, path):
    try:
        stack.enter_context(regular(path))
        return True
    except FileNotFoundError:
        return False


@contextmanager
def _clean_wal_copy(path, fd, before):
    """Copy a checkpointed WAL main file without touching its read-only source."""
    c.need(0 < before.st_size <= MAX_DATABASE_BYTES)
    with tempfile.TemporaryDirectory(prefix='agent-platform-router-') as folder:
        copied = os.path.join(folder, 'router.db')
        target = os.open(copied, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        offset = 0
        try:
            with os.fdopen(target, 'wb') as stream:
                while offset < before.st_size:
                    chunk = os.pread(fd, min(1024 * 1024, before.st_size - offset), offset)
                    c.need(bool(chunk))
                    stream.write(chunk)
                    offset += len(chunk)
                stream.flush()
            c.need(offset == before.st_size and _same_file(before, os.fstat(fd)))
            with ExitStack() as check:
                c.need(not _sidecar(check, path + '-wal') and not _sidecar(check, path + '-shm'))
            yield copied
        except BaseException:
            if offset < before.st_size:
                try:
                    os.close(target)
                except OSError:
                    pass
            raise


@contextmanager
def _authorized(path, table, columns):
    connection = sqlite3.connect('file:' + quote(path, safe='/') + '?mode=ro', uri=True, timeout=0.25)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        c.need(connection.execute('PRAGMA query_only').fetchone() == (1,))
        c.need(connection.execute('PRAGMA trusted_schema').fetchone() == (0,))
        c.need(connection.execute('SELECT type FROM sqlite_schema WHERE name=?', (table,)).fetchone() == ('table',))
        # table names are module constants, never supplied by config or HTTP.
        c.need(columns <= {r[1] for r in connection.execute('PRAGMA table_info(' + table + ')')})
        deadline = time.monotonic() + 1
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        allowed_functions = {'count', 'sum', 'max', 'min', 'coalesce', 'typeof'}
        def authorize(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and arg1 == table and arg2 in columns and database == 'main' and trigger is None:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and arg2 in allowed_functions:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize)
        yield connection
    finally:
        connection.close()


@contextmanager
def readonly(path, table, columns):
    """Read a SQLite source without mutating it; copy a clean WAL checkpoint privately."""
    with regular(path) as (fd, before):
        c.need(before.st_size <= MAX_DATABASE_BYTES)
        if os.pread(fd, 20, 0)[18:20] == b'\x02\x02':
            with ExitStack() as sidecars:
                present = tuple(_sidecar(sidecars, path + suffix) for suffix in ('-wal', '-shm'))
                if any(present):
                    c.need(all(present))
                    with _authorized(path, table, columns) as connection:
                        yield connection
                else:
                    with _clean_wal_copy(path, fd, before) as copied:
                        with _authorized(copied, table, columns) as connection:
                            yield connection
        else:
            with _authorized(path, table, columns) as connection:
                yield connection
        c.need(_same_file(before, os.stat(path, follow_symlinks=False)))


ROUTER_COLUMNS = {'id', 'task_id', 'started_at', 'ended_at', 'input_tokens', 'output_tokens',
                  'cost_usd', 'actual_model', 'provider', 'fallback_used', 'success', 'duration_s'}
ROUTER_SQL = '''SELECT task_id,actual_model,provider,count(*),
CASE WHEN count(input_tokens)=count(*) THEN sum(input_tokens) END,
CASE WHEN count(output_tokens)=count(*) THEN sum(output_tokens) END,
CASE WHEN count(cost_usd)=count(*) THEN sum(cost_usd) END,
sum(coalesce(fallback_used,0)),
sum(CASE WHEN success=1 THEN 1 ELSE 0 END),
CASE WHEN count(duration_s)=count(*) THEN sum(duration_s) END,
max(coalesce(ended_at,started_at)) FROM
(SELECT id,task_id,actual_model,provider,started_at,ended_at,input_tokens,output_tokens,
        cost_usd,fallback_used,success,duration_s
 FROM requests ORDER BY id DESC LIMIT 1000)
GROUP BY task_id,actual_model,provider LIMIT 51'''


def router(path, profile):
    with readonly(path, 'requests', ROUTER_COLUMNS) as db:
        records = db.execute(ROUTER_SQL).fetchall()
    c.need(len(records) <= 50)
    rows, timestamps = [], []
    for task, model, provider, count, inputs, outputs, cost, fallbacks, successes, duration, stamp in records:
        c.need(cost is None or type(cost) in (int, float) and 0 <= cost < 10**8)
        c.need(duration is None or type(duration) in (int, float) and 0 <= duration < 10**9)
        item = dict(task_id=None if task is None else c.identity(profile, task),
                    actual_model=model, provider=provider, requests=count,
                    input_tokens=inputs, output_tokens=outputs,
                    cost_microusd=None if cost is None else round(cost * 1000000),
                    fallback_count=fallbacks, successful_requests=successes,
                    duration_ms=None if duration is None else round(duration * 1000))
        c.row('router', item)
        rows.append(item)
        if stamp is not None:
            c.need(type(stamp) in (int, float) and 0 <= stamp < 2**53)
            timestamps.append(int(stamp))
    return rows, max(timestamps, default=None)


def kanban(source, profile):
    with readonly(source['path'], 'tasks', {'id', 'status', 'current_run_id', 'created_at'}) as db:
        records = db.execute('SELECT id,status,current_run_id,created_at FROM tasks ORDER BY created_at DESC,id LIMIT 51').fetchall()
    c.need(len(records) <= 50)
    rows, timestamps = [], []
    for task, status, run, stamp in records:
        item = dict(task_id=c.identity(profile, task), status=status, run_id=run)
        c.row('kanban', item)
        c.need(c.number(stamp))
        rows.append(item)
        timestamps.append(stamp)
    return rows, max(timestamps, default=None)


def git(path, profile):
    # Explicit registry path; never use git discovery from process cwd.
    c.need(Path(path).is_dir() and Path(path).resolve().as_posix() == path)
    args = ['/usr/bin/git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', '-C', path]
    commit = command(args + ['rev-parse', '--verify', 'HEAD'], limit=128).decode('ascii').strip()
    dirty = bool(command(args + ['status', '--porcelain=v1', '-z', '--untracked-files=normal', '--ignore-submodules=all']))
    item = dict(commit=commit, dirty=dirty)
    c.row('git', item)
    return [item], None


def tests(path, profile):
    raw = c.parse(read(path, 4096), 4096)
    c.keys(raw, 'version profile observed_at passed failed artifact_digest')
    c.need(type(raw['version']) is int and raw['version'] == 1 and raw['profile'] == profile and c.number(raw['observed_at']))
    item = {k: raw[k] for k in ('passed', 'failed', 'artifact_digest')}
    c.row('tests', item)
    return [item], raw['observed_at']


class NotConfigured(ValueError):
    pass


def herdr(path, profile, now):
    raw = c.parse(read(path, 8192), 8192)
    c.keys(raw, 'version observed_at profiles agents')
    c.need(type(raw['version']) is int and raw['version'] == 1 and c.number(raw['observed_at'])
           and 0 <= now - raw['observed_at'] <= 90 and type(raw['agents']) is list and len(raw['agents']) <= 4)
    c.need(type(raw['profiles']) is list and 1 <= len(raw['profiles']) <= 2
           and all(type(p) is str and p in c.PROFILES for p in raw['profiles'])
           and len(set(raw['profiles'])) == len(raw['profiles']))
    for item in raw['agents']:
        c.row('herdr', item)
    c.need({r['agent'].split('-')[0] for r in raw['agents']} == set(raw['profiles']))
    if profile not in raw['profiles']:
        raise NotConfigured('profile_not_configured')
    c.need(len({r['agent'] for r in raw['agents']}) == len(raw['agents']))
    return [r for r in raw['agents'] if r['agent'].startswith(profile + '-')], raw['observed_at']
