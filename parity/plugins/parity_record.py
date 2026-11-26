"""Pytest plugin that records the calls v1's tests make, for the replay.

usage: <pytest of a v1 venv> -p parity_record --parity-record PLAN <tests>

v1's core tests call the engine in process, and its CLI tests call the
console script through one helper. The plugin records both kinds of call as
a command line: the arguments, the working directory, the environment and
the clock. Around each call it copies the folders the call names, so the
replay can run the command line on the tree the test's call found and hold
it to what the call did. It also records every call the format tests make
to a public format function, the notice and issue kinds each test and each
call meets, and the kinds the engine has.

``PLAN`` is a JSON object: ``store`` is the folder for the records and the
copies, ``wiki`` the engine class as ``module:Class``, ``issue`` the class of
a ``lint`` issue in the same form, ``format`` the module of the format
functions, ``formats`` the ids of the tests whose format calls are recorded
(each a prefix), and ``helper`` the module the CLI tests take the console
script from. The plugin runs inside a v1 side's interpreter, so
it imports nothing from ``parity``; its folder holds the harness's other
plugins and nothing else.
"""

from __future__ import annotations

import ast
import collections
import datetime as dt
import functools
import importlib
import inspect
import json
import locale
import os
import pathlib
import re
import shutil
import stat
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from types import ModuleType
from typing import Any, Optional

import parity_values
import pytest

__all__ = []

#: the variable that fixes an engine's clock
CLOCK = 'WIKI_FREEZE_TIME'
#: the stderr line v1's command line prints first when the clock is fixed
NOTICE = 'WIKI_FREEZE_TIME is set: timestamps use its fixed instant, not the clock.\n'

# the engine methods that are recorded: the ones the commands of the read,
# search, match, map, lint, update and new call
_METHODS = ('update', 'lint', 'read', 'search', 'match', 'map', 'new')
# the method that reads the engine's clock, and the property that holds the
# zone and the format a stamp is rendered in
_NOW = '_utc_now'
_POLICY = '_timestamp_policy'
# the prefix of the engine's notice hooks, and the hook they all hand on to
_HOOK = 'on_'
_FUNNEL = 'on_notice'
# the format functions that take a function, which has no JSON form
_DECORATORS = ('run_scoped',)
# the units `read` slices by
_UNITS = ('lines', 'words', 'chars')
# the names the helper holds the console script and the module it starts
# commands with under
_WIKI = 'WIKI'
_STARTER = 'subprocess'
# the scratch home of one process of the run, below its temporary folder,
# so the copies of a call hold whatever the call writes there
_HOME = 'parity_home'
# the files of one process's records, below its folder of the store
_CALLS = 'calls.jsonl'
_FORMATS = 'formats.jsonl'
_TESTS = 'tests.jsonl'
_KINDS = 'kinds.json'
# the keyword that names the kind of a `lint` issue where one is made
_KIND = 'kind'
# the scope of a fixture that one test alone sets up
_OWN = 'function'
# the name of the process in the store, where no worker runs
_MAIN = 'main'
# the permission bits a copy needs to be read again: of a folder, of a file
_OPEN_FOLDER = 0o700
_OPEN_FILE = 0o400

_RECORDER = pytest.StashKey['_Recorder']()


class _Unformed(Exception):
    """A call that no command line states; the text says why."""


class _Recorder:
    """Records the calls of one process of the run."""

    def __init__(self: _Recorder, plan: Mapping[str, Any]) -> None:
        """Initialize the recorder of the run that ``plan`` describes."""
        self.plan = plan
        # the folder of the process in the store, once pytest names the process
        self.folder = pathlib.Path(plan['store'])
        # the temporary folder of the process, once pytest has made it
        self.base: Optional[pathlib.Path] = None
        # the test under way, the top entries the temporary folder held when it
        # started, each with its modification time, and what the test met
        self.test = ''
        self.held: dict[str, int] = {}
        self.formats = False
        self.kinds: dict[str, collections.Counter[str]] = {}
        self.calls = 0
        # the calls recorded so far, and the format calls by their JSON text
        self.count = 0
        self.seen: dict[str, dict[str, Any]] = {}
        # the recorded call under way: its thread, the notices it emits and
        # the kind of each
        self.owner: Optional[int] = None
        self.notices: Optional[list[str]] = None
        self.noticed: list[str] = []
        self.formatting = threading.local()
        self.original: Optional[Callable[..., Any]] = None
        # the kinds the engine has
        self.known: dict[str, list[str]] = {}
        # the fixture names of the test under way, the shared fixture being
        # set up, and each shared fixture that made a call, with the start of
        # the test ids it serves and its name
        self.names: frozenset[str] = frozenset()
        self.shared = ''
        self.serves: tuple[str, str] = ('', '')
        self.fixtures: dict[str, tuple[str, str]] = {}

    def open(self: _Recorder, worker: str) -> None:
        """Make the folder of the process ``worker`` in the store, with the engine's kinds."""
        self.folder = pathlib.Path(self.plan['store']) / worker
        self.folder.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.known) + '\n'
        (self.folder / _KINDS).write_text(text, encoding='utf-8')

    def start(self: _Recorder, test: str, names: Sequence[str] = ()) -> None:
        """Start recording for the test ``test``, which asks for the fixtures ``names``."""
        self.finish()
        self.test = test
        self.names = frozenset(names)
        self.held = _entries(self.base) if self.base else {}
        self.formats = any(test.startswith(prefix) for prefix in self.plan['formats'])
        self.kinds = {'notices': collections.Counter(), 'issues': collections.Counter()}
        self.calls = 0

    def finish(self: _Recorder) -> None:
        """Write what the test under way met, if one is under way."""
        if not self.test:
            return
        row = {
            'test': self.test,
            'calls': self.calls,
            'kinds': {
                name: dict(sorted(count.items())) for name, count in self.kinds.items()
            },
            'shared': sorted(
                fixture
                for fixture, (base, name) in self.fixtures.items()
                if (name in self.names) and self.test.startswith(base)
            ),
        }
        _append(self.folder / _TESTS, row)
        self.test = ''

    def close(self: _Recorder) -> None:
        """Write the last test and the format calls of the process."""
        self.finish()
        with open(self.folder / _FORMATS, 'w', encoding='utf-8') as handle:
            for entry in self.seen.values():
                handle.write(json.dumps(entry) + '\n')

    def engine(
        self: _Recorder,
        method: str,
        original: Callable[..., Any],
        wiki: Any,
        args: Sequence[Any],
        kwargs: Mapping[str, Any],
    ) -> Any:
        """Make one call of an engine method, recorded if it is the outermost.

        A call a recorded call makes is part of it. So is a call from
        another thread while one is under way: its tree is changing.
        """
        if (self.owner is not None) or not self.test:
            return original(wiki, *args, **kwargs)
        self.calls += 1
        index = self.count
        self.count += 1
        record: dict[str, Any] = {
            'test': self.test,
            'index': index,
            'source': 'core',
            'command': method,
            'shared': self.shared,
            'root': f'{wiki._root}',
        }
        # state the call as a command line, and copy the trees it names
        try:
            signature = inspect.signature(original)
            bound = signature.bind(wiki, *args, **kwargs)
            bound.apply_defaults()
            arguments = dict(list(bound.arguments.items())[1:])
            form, stated = _FORMS[method]
            for name, value in arguments.items():
                default = signature.parameters[name].default
                if (name not in stated) and (value != default):
                    raise _Unformed(f'no option states the argument {name}')
            options, positionals = form(arguments)
            argv = [method, *options, '--path', record['root']]
            if positionals:
                argv += ['--', *positionals]
            cwd = os.getcwd()
            env = dict(os.environ)
            trees = self._trees([cwd, record['root'], *argv, *env.values()])
            self._inside(record['root'], 'wiki')
            record.update(argv=argv, cwd=cwd, env=env, trees=trees)
            self._copy(index, 'before', record)
        except (_Unformed, OSError, TypeError) as e:
            record['unformed'] = f'{e}'
        # make the call under a fixed clock, and keep what it did
        clock = _Clock(wiki, self.original)
        self.owner = threading.get_ident()
        self.notices = []
        self.noticed = []
        issues: list[str] = []
        try:
            with clock:
                result = original(wiki, *args, **kwargs)
            record['outcome'] = _summary(method, result)
            if method == 'lint':
                issues = [issue.kind for issue in result]
                self.kinds['issues'].update(issues)
            return result
        except BaseException as e:
            record['outcome'] = {
                'raised': type(e).__name__,
                'text': f'{e}',
                'error': isinstance(e, Exception),
            }
            raise
        finally:
            record['notices'] = self.notices
            record['kinds'] = {'notices': self.noticed, 'issues': issues}
            self.owner = None
            self.notices = None
            # the instant the call ran at, read from the clock it saw
            try:
                instant = clock.instant()
            except _Unformed as e:
                record.setdefault('unformed', f'{e}')
            else:
                if (instant is not None) and ('env' in record):
                    record['env'][CLOCK] = instant
            self._close(index, record)

    def spawn(
        self: _Recorder,
        run: Callable[..., Any],
        argv: Sequence[str],
        kwargs: Mapping[str, Any],
    ) -> Any:
        """Run one command of the console script for the helper, recorded.

        The command runs under a fixed clock unless the test set the
        variable itself. The notice the recorder's clock adds to stderr is
        taken out of what the test reads, and stays in the record. A blank
        value of the test leaves the clock running: the command runs as
        the test started it, and no command line states the call, since a
        replay would write the stamps of its own moment.
        """
        if (self.owner is not None) or not self.test:
            return run(argv, **kwargs)
        self.calls += 1
        index = self.count
        self.count += 1
        args = [f'{item}' for item in argv[1:]]
        env = dict(kwargs.get('env') or os.environ)
        pinned = CLOCK not in env
        if pinned:
            env[CLOCK] = _instant(dt.datetime.now(dt.UTC))
        cwd = f'{kwargs.get("cwd") or os.getcwd()}'
        record: dict[str, Any] = {
            'test': self.test,
            'index': index,
            'source': 'cli',
            'command': args[0] if args else '',
            'shared': self.shared,
            'argv': args,
            'cwd': cwd,
            'env': env,
        }
        try:
            if not env[CLOCK].strip():
                raise _Unformed('the test leaves the clock running')
            record['trees'] = self._trees([cwd, *args, *env.values()])
            self._inside(cwd, 'working directory')
            self._copy(index, 'before', record)
        except (_Unformed, OSError) as e:
            record['unformed'] = f'{e}'
        try:
            done = run(argv, **{**kwargs, 'env': env})
        except BaseException as e:
            record['unformed'] = f'the command did not end: {type(e).__name__}'
            self._close(index, record)
            raise
        # keep the streams as the command wrote them, and hand the test the
        # ones a command with no fixed clock writes
        text = isinstance(done.stderr, str)
        streams = {}
        for name in ('stdout', 'stderr'):
            stream = getattr(done, name)
            if stream is None:
                record['unformed'] = f'the helper does not capture {name}'
                streams[name] = ''
            else:
                streams[name] = stream if text else stream.decode('latin-1')
        noticed = streams['stderr'].startswith(NOTICE)
        if pinned and noticed and (done.stderr is not None):
            notice = NOTICE if text else NOTICE.encode('latin-1')
            done.stderr = done.stderr[len(notice) :]
        record['outcome'] = {
            'code': done.returncode,
            'stdout': streams['stdout'],
            'stderr': streams['stderr'],
            'notice': noticed,
            'encoding': (kwargs.get('encoding') or locale.getencoding())
            if text
            else None,
        }
        self._close(index, record)
        return done

    def format(
        self: _Recorder,
        name: str,
        original: Callable[..., Any],
        args: Sequence[Any],
        kwargs: Mapping[str, Any],
    ) -> Any:
        """Make one call of a format function, recorded if a test makes it.

        The outermost call is the recorded one: what a format function
        calls is its own business. The arguments are written down before
        the call, which may change them.
        """
        depth = getattr(self.formatting, 'depth', 0)
        if depth or not (self.test and self.formats):
            return original(*args, **kwargs)
        try:
            call = {
                'function': name,
                'args': parity_values.plain(list(args)),
                'kwargs': parity_values.plain(dict(kwargs)),
            }
        except TypeError as e:
            call = {'function': name, 'unformed': f'{e}'}
        self.formatting.depth = 1
        try:
            value = original(*args, **kwargs)
        except Exception as e:
            self._seen(call, parity_values.raised(e))
            raise
        finally:
            self.formatting.depth = 0
        self._seen(call, parity_values.returned(value))
        return value

    def notice(self: _Recorder, event: Any) -> None:
        """Note one notice the engine emitted."""
        name = getattr(event, 'name', None)
        if not (self.test and isinstance(name, str)):
            return
        kind = name.removesuffix('_EVENT').lower()
        self.kinds['notices'][kind] += 1
        if (self.notices is not None) and (self.owner == threading.get_ident()):
            self.notices.append(f'{event.description}')
            self.noticed.append(kind)

    # ------ helper methods

    def _seen(self: _Recorder, call: dict[str, Any], outcome: dict[str, Any]) -> None:
        """Count one format call under the test that made it."""
        call['outcome'] = outcome
        key = json.dumps(call, sort_keys=True)
        entry = self.seen.setdefault(key, {**call, 'count': 0, 'tests': []})
        entry['count'] += 1
        if self.test not in entry['tests']:
            entry['tests'].append(self.test)

    def _trees(self: _Recorder, texts: Sequence[str]) -> dict[str, Any]:
        """Return the trees of a call: the top entries of the temporary folder it may read.

        They are the entries the test under way has made or written, and
        the ones that ``texts`` name: a test may run on a tree an earlier
        test's fixture made.

        Returns:
            The temporary folder as ``base`` and the names of the entries
            as ``names``.

        Raises:
            _Unformed: If no temporary folder is made yet, or if the texts
                name the temporary folder itself: the call's tree is then
                not one the recorder can copy.

        """
        if self.base is None:
            raise _Unformed('the call ran before the temporary folder was made')
        prefix = re.escape(f'{self.base}{os.sep}')
        entries = _entries(self.base)
        named = {name for name in entries if entries[name] != self.held.get(name)}
        for text in texts:
            for part in text.split(os.pathsep):
                if part == f'{self.base}':
                    raise _Unformed('the call names the whole temporary folder')
                named.update(re.findall(rf'{prefix}([^{re.escape(os.sep)}]+)', part))
        return {'base': f'{self.base}', 'names': sorted(named)}

    def _inside(self: _Recorder, path: str, what: str) -> None:
        """Refuse a call whose ``what``, at ``path``, lies outside the temporary folder.

        The replay runs the command at the call's own paths, on the trees
        it put there. A path outside the temporary folder is one no copy
        holds, and a command would run on whatever lies there.

        Raises:
            _Unformed: If ``path`` is not below the temporary folder.

        """
        if self.base not in pathlib.Path(os.path.realpath(path)).parents:
            raise _Unformed(f"the call's {what} lies outside the temporary folder")

    def _close(self: _Recorder, index: int, record: dict[str, Any]) -> None:
        """Copy the trees as the call left them, and write the record.

        A copy that fails leaves the call with no command line; the test
        goes on as it would without the recorder.
        """
        if 'unformed' not in record:
            try:
                self._copy(index, 'after', record)
            except OSError as e:
                record['unformed'] = f'{e}'
        if record['shared']:
            self.fixtures[record['shared']] = self.serves
        _append(self.folder / _CALLS, record)

    def _copy(self: _Recorder, index: int, moment: str, record: dict[str, Any]) -> None:
        """Copy the trees of ``record`` as they stand, into the store."""
        target = self.folder / f'{index}' / moment
        target.mkdir(parents=True)
        sealed: list[list[Any]] = []
        present = []
        for name in record['trees']['names']:
            source = os.path.join(record['trees']['base'], name)
            if os.path.lexists(source):
                present.append(name)
                _copy(source, f'{target / name}', name, sealed)
        record[moment] = {'names': present, 'sealed': sealed}


class _Clock:
    """The clock of one recorded call: fixed for the call, and read back.

    The engine's own clock method reads ``WIKI_FREEZE_TIME``, so the call
    runs with the variable set to the instant it starts at, unless the
    test set the variable to an instant itself. The engine reads a blank
    value as unset, and so does the clock. A test that has put its own
    clock method in place keeps it, and the stamps the method returns say
    which instant the call ran at.
    """

    def __init__(
        self: _Clock, wiki: Any, original: Optional[Callable[..., Any]]
    ) -> None:
        """Initialize the clock of a call on ``wiki``."""
        self.wiki = wiki
        self.stamps: list[str] = []
        self.started = dt.datetime.now(dt.UTC)
        # where the clock method the call reads is held: the instance, or
        # the first class that defines it
        holders = [wiki, *type(wiki).__mro__]
        self.holder = next(item for item in holders if _NOW in vars(item))
        self.method = vars(self.holder)[_NOW]
        self.own = self.method is original
        self.kept = os.environ.get(CLOCK)
        self.set = bool((self.kept or '').strip())

    def __enter__(self: _Clock) -> _Clock:
        """Fix the clock, and listen to a clock method the test put in place."""
        if not self.set:
            os.environ[CLOCK] = _instant(self.started)
        if not self.own:
            method = self.method
            stamps = self.stamps

            @functools.wraps(method)
            def heard(*args: Any, **kwargs: Any) -> Any:
                """Return the stamp of the test's clock, and note it."""
                stamp = method(*args, **kwargs)
                stamps.append(stamp)
                return stamp

            setattr(self.holder, _NOW, heard)
        return self

    def __exit__(self: _Clock, *args: object) -> None:
        """Put the clock back as the test had it."""
        if not self.own:
            setattr(self.holder, _NOW, self.method)
        if not self.set:
            if self.kept is None:
                del os.environ[CLOCK]
            else:
                os.environ[CLOCK] = self.kept

    def instant(self: _Clock) -> Optional[str]:
        """Return the instant the call ran at, as ``WIKI_FREEZE_TIME`` takes it.

        Returns:
            The instant to replay the call at; ``None`` where the test
            set the variable itself and the engine's own clock read it.

        Raises:
            _Unformed: If the test's clock gave two stamps, or a stamp no
                instant renders as under the wiki's zone and format: the
                variable then cannot state the clock.

        """
        if self.own or not self.stamps:
            return None if self.set else _instant(self.started)
        if len(set(self.stamps)) > 1:
            raise _Unformed("the test's clock gave more than one stamp")
        stamp = self.stamps[0]
        try:
            policy = getattr(self.wiki, _POLICY)
            parsed = dt.datetime.strptime(stamp, policy['format'])
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=policy['zone'])
            rendered = parsed.astimezone(policy['zone']).strftime(policy['format'])
        except Exception as e:
            raise _Unformed(
                f"the test's clock gave a stamp no instant states: {e}"
            ) from e
        if rendered != stamp:
            raise _Unformed("the test's clock gave a stamp no instant states")
        return _instant(parsed)


class _Spawner:
    """Stands in the helper for the module it starts commands with."""

    def __init__(
        self: _Spawner, module: ModuleType, helper: ModuleType, recorder: _Recorder
    ) -> None:
        """Initialize the stand-in for ``module`` in ``helper``."""
        self._module = module
        self._helper = helper
        self._recorder = recorder

    def __getattr__(self: _Spawner, name: str) -> Any:
        """Return what the module holds under ``name``."""
        return getattr(self._module, name)

    def run(self: _Spawner, argv: Sequence[str], **kwargs: Any) -> Any:
        """Run a command; one of the console script is recorded."""
        script = getattr(self._helper, _WIKI, None)
        if script and argv and (f'{argv[0]}' == f'{script}'):
            return self._recorder.spawn(self._module.run, argv, kwargs)
        return self._module.run(argv, **kwargs)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the option that names the plan of the recording."""
    group = parser.getgroup('parity')
    plan_help = 'JSON file that names the store and what is recorded.'
    group.addoption('--parity-record', type=pathlib.Path, required=True, help=plan_help)


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    """Wrap the engine and the format functions before a test module is imported.

    A test module may bind a format function by name as it is imported,
    so the functions are wrapped before pytest imports the first of them.
    """
    path: pathlib.Path = early_config.known_args_namespace.parity_record
    plan = json.loads(path.read_text(encoding='utf-8'))
    recorder = _Recorder(plan)
    early_config.stash[_RECORDER] = recorder
    # wrap the engine's methods and its notice hooks, one hook for each kind
    module, _, name = plan['wiki'].partition(':')
    wiki = getattr(importlib.import_module(module), name)
    recorder.original = vars(wiki)[_NOW]
    for method in _METHODS:
        setattr(wiki, method, _engine(recorder, method, getattr(wiki, method)))
    notices = []
    for name, hook in list(vars(wiki).items()):
        if name.startswith(_HOOK) and (name != _FUNNEL) and inspect.isfunction(hook):
            setattr(wiki, name, _hook(recorder, hook))
            notices.append(name[len(_HOOK) :])
    recorder.known = {'notices': sorted(notices), 'issues': _issues(plan['issue'])}
    # wrap the public format functions
    formats = importlib.import_module(plan['format'])
    for name, function in list(vars(formats).items()):
        public = not name.startswith('_') and (name not in _DECORATORS)
        own = inspect.isfunction(function) and (function.__module__ == formats.__name__)
        if public and own:
            setattr(formats, name, _format(recorder, name, function))


def pytest_configure(config: pytest.Config) -> None:
    """Give the process its folder in the store, once pytest names the process."""
    worker = getattr(config, 'workerinput', {}).get('workerid', _MAIN)
    config.stash[_RECORDER].open(worker)


@pytest.fixture(scope='session', autouse=True)
def _parity_base(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Tell the recorder the temporary folder, and give the process a home in it.

    The home lies in the temporary folder so that a call's copies hold
    what the call writes there.
    """
    recorder = request.config.stash[_RECORDER]
    recorder.base = tmp_path_factory.getbasetemp().resolve()
    home = recorder.base / _HOME
    home.mkdir()
    (home / '.gitconfig').write_bytes(b'')
    patch = pytest.MonkeyPatch()
    patch.setenv('HOME', f'{home}')
    patch.setenv('GIT_CONFIG_GLOBAL', f'{home / ".gitconfig"}')
    yield
    patch.undo()


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Refuse to run tests whose commands the recorder cannot watch.

    Raises:
        pytest.UsageError: If the helper module does not hold the module
            it starts commands with under its own name: every command of
            the tests would then run with no record, and the replay would
            pass without them.

    """
    if not items:
        return
    helper = importlib.import_module(config.stash[_RECORDER].plan['helper'])
    if not isinstance(vars(helper).get(_STARTER), (ModuleType, _Spawner)):
        raise pytest.UsageError(
            f'{helper.__name__} does not start its commands through a module it'
            f' holds as {_STARTER}; the recorder cannot watch them.'
        )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Start recording for the test, before its first fixture runs.

    The helper of the CLI tests is imported by then, so this is where its
    commands are first watched.
    """
    recorder = item.config.stash[_RECORDER]
    helper = importlib.import_module(recorder.plan['helper'])
    module = vars(helper)[_STARTER]
    if not isinstance(module, _Spawner):
        vars(helper)[_STARTER] = _Spawner(module, helper, recorder)
    recorder.start(item.nodeid, getattr(item, 'fixturenames', ()))


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(
    fixturedef: pytest.FixtureDef[Any],
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Record the calls a shared fixture makes as the fixture's own.

    A fixture of a wider scope than one test is set up inside the first
    test that asks for it, and serves every other test that does. Which
    test comes first turns on how the tests are spread over the processes.
    """
    recorder = request.config.stash[_RECORDER]
    kept = (recorder.shared, recorder.serves)
    if fixturedef.scope != _OWN:
        recorder.shared = f'{fixturedef.baseid}::{fixturedef.argname}'
        recorder.serves = (fixturedef.baseid, fixturedef.argname)
    yield
    recorder.shared, recorder.serves = kept


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Write what the process recorded."""
    session.config.stash[_RECORDER].close()


# ------ helper functions


def _engine(
    recorder: _Recorder, method: str, original: Callable[..., Any]
) -> Callable[..., Any]:
    """Return the engine method ``original``, recorded."""

    @functools.wraps(original)
    def recorded(self: Any, *args: Any, **kwargs: Any) -> Any:
        """Make the call through the recorder."""
        return recorder.engine(method, original, self, args, kwargs)

    return recorded


def _issues(named: str) -> list[str]:
    """Return the issue kinds of the engine whose issue class is ``named``.

    An issue's kind is a keyword where the issue is made, so the kinds are
    read from the source of the package that holds the class.

    Raises:
        pytest.UsageError: If an issue is made with no kind keyword, or
            with one that is no string literal: the source then does not
            list the kinds.

    """
    module, _, name = named.partition(':')
    package = importlib.import_module(module.partition('.')[0])
    kinds = set()
    for path in sorted(pathlib.Path(f'{package.__file__}').parent.rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            made = (isinstance(called, ast.Name) and (called.id == name)) or (
                isinstance(called, ast.Attribute) and (called.attr == name)
            )
            if not made:
                continue
            values = [item.value for item in node.keywords if item.arg == _KIND]
            literal = [
                value.value
                for value in values
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            ]
            if len(literal) != 1:
                raise pytest.UsageError(
                    f'{path}:{node.lineno}: an issue is made with a kind that is'
                    f' no string literal; the recorder cannot list the kinds.'
                )
            kinds.update(literal)
    return sorted(kinds)


def _hook(recorder: _Recorder, original: Callable[..., Any]) -> Callable[..., Any]:
    """Return the notice hook ``original``, with its event noted."""

    @functools.wraps(original)
    def noted(self: Any, *args: Any, **kwargs: Any) -> Any:
        """Run the hook and note the event it handled."""
        event = original(self, *args, **kwargs)
        recorder.notice(event)
        return event

    return noted


def _format(
    recorder: _Recorder, name: str, original: Callable[..., Any]
) -> Callable[..., Any]:
    """Return the format function ``original``, recorded."""

    @functools.wraps(original)
    def recorded(*args: Any, **kwargs: Any) -> Any:
        """Make the call through the recorder."""
        return recorder.format(name, original, args, kwargs)

    return recorded


def _update(arguments: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return the options and positionals of an ``update`` call, in full mode."""
    options = ['--full']
    if arguments['check']:
        options.append('--check')
    return options, _scope(arguments)


def _lint(arguments: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return the options and positionals of a ``lint`` call, as JSON."""
    return ['--json'], _scope(arguments)


def _read(arguments: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return the options and positionals of a ``read`` call."""
    start, stop, on = arguments['start'], arguments['stop'], arguments['on']
    options = []
    if (start is not None) or (stop is not None) or (on != 'lines'):
        if on not in _UNITS:
            raise _Unformed(f'read has no option for the unit {on!r}')
        low = '' if start is None else f'{_whole(start)}'
        high = '' if stop is None else f'{_whole(stop)}'
        options.append(f'--{on}={low}:{high}')
    return options, [_text(arguments['name'])]


def _search(arguments: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return the options and positionals of a ``search`` call, as JSON."""
    options = ['--json']
    if arguments['limit'] is not None:
        options.append(f'--limit={_whole(arguments["limit"])}')
    if arguments['tag'] is not None:
        options.append(f'--tag={_text(arguments["tag"])}')
    if arguments['prefix']:
        options.append('--prefix')
    if arguments['raw']:
        options.append('--raw')
    return options, [_text(arguments['query']), *_scope(arguments)]


def _match(arguments: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return the options and positionals of a ``match`` call, with its lines."""
    options = ['--lines']
    if arguments['field'] is not None:
        options.append(f'--field={_text(arguments["field"])}')
    if arguments['ignore_case']:
        options.append('--ignore-case')
    if arguments['all_files']:
        options.append('--all')
    return options, [_text(arguments['pattern']), *_scope(arguments)]


def _map(arguments: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return the options and positionals of a ``map`` call."""
    options = []
    if arguments['depth'] is not None:
        options.append(f'--depth={_whole(arguments["depth"])}')
    if not arguments['desc']:
        options.append('--no-desc')
    if arguments['desc_limit'] is not None:
        options.append(f'--desc-limit={_whole(arguments["desc_limit"])}')
    category = arguments['category']
    if category is not None:
        if not isinstance(category, str):
            category = ','.join(_text(item) for item in category)
        options.append(f'--category={category}')
    if arguments['markdown'] is not None:
        options.append('--markdown' if arguments['markdown'] else '--no-markdown')
    if not arguments['words']:
        options.append('--no-words')
    return options, _scope(arguments)


def _new(arguments: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return the options and positionals of a ``new`` call."""
    options = [
        f'--desc={_text(arguments["desc"])}',
        f'--content={_text(arguments["content"])}',
    ]
    return options, [_text(arguments['name'])]


# the command line of each recorded method: its options and its positionals,
# in the output mode that prints the most, and the parameters they state
_FORMS = {
    'update': (_update, ('name', 'check')),
    'lint': (_lint, ('name',)),
    'read': (_read, ('name', 'start', 'stop', 'on')),
    'search': (_search, ('query', 'name', 'limit', 'tag', 'prefix', 'raw')),
    'match': (_match, ('pattern', 'name', 'field', 'ignore_case', 'all_files')),
    'map': (
        _map,
        ('name', 'depth', 'desc', 'desc_limit', 'category', 'markdown', 'words'),
    ),
    'new': (_new, ('name', 'desc', 'content')),
}


def _scope(arguments: Mapping[str, Any]) -> list[str]:
    """Return the positional that scopes a call to a subtree, if it has one."""
    if arguments['name'] is None:
        return []
    return [_text(arguments['name'])]


def _text(value: Any) -> str:
    """Return an argument that a command line can carry: a string.

    Raises:
        _Unformed: If ``value`` is anything else, or holds a null
            character.

    """
    if (not isinstance(value, str)) or ('\0' in value):
        raise _Unformed(f'no argument states {value!r}')
    return f'{value}'


def _whole(value: Any) -> int:
    """Return an argument that an integer option can carry.

    Raises:
        _Unformed: If ``value`` is no integer.

    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Unformed(f'no integer option states {value!r}')
    return value


def _summary(method: str, result: Any) -> dict[str, Any]:
    """Return what a call returned, as far as its command line shows it."""
    if method in ('update', 'lint'):
        return {'returned': [f'{item}' for item in result]}
    if method in ('read', 'map', 'new'):
        return {'returned': f'{result}'}
    return {'returned': len(result)}


def _instant(moment: dt.datetime) -> str:
    """Return ``moment`` in the form ``WIKI_FREEZE_TIME`` takes."""
    return moment.astimezone(dt.UTC).isoformat()


def _entries(folder: pathlib.Path) -> dict[str, int]:
    """Return the entries of ``folder``, each with its modification time."""
    with os.scandir(folder) as listing:
        return {
            item.name: item.stat(follow_symlinks=False).st_mtime_ns for item in listing
        }


def _append(path: pathlib.Path, row: Mapping[str, Any]) -> None:
    """Append ``row`` to the file at ``path`` as one line of JSON."""
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(row) + '\n')


def _copy(source: str, target: str, relpath: str, sealed: list[list[Any]]) -> None:
    """Copy the path ``source`` to the new path ``target``, with what is below it.

    The copy is made with library calls: a test may leave no ``cp`` on
    ``PATH``. Modes and modification times are kept. A folder or a file
    its owner cannot read is opened for the copy and closed again; its
    copy stays open, and ``sealed`` takes its path and its true mode, so
    the store can be read and the replay can seal its own copy. A path that
    goes away while its folder is copied is left out: git keeps a lock
    file in a repository for a moment after a commit.
    """
    try:
        status = os.lstat(source)
    except FileNotFoundError:
        return
    mode = stat.S_IMODE(status.st_mode)
    if stat.S_ISLNK(status.st_mode):
        os.symlink(os.readlink(source), target)
    elif stat.S_ISDIR(status.st_mode):
        closed = (mode & _OPEN_FOLDER) != _OPEN_FOLDER
        if closed:
            os.chmod(source, mode | _OPEN_FOLDER)
            sealed.append([relpath, mode])
        try:
            os.mkdir(target)
            for name in os.listdir(source):
                _copy(
                    os.path.join(source, name),
                    os.path.join(target, name),
                    f'{relpath}/{name}',
                    sealed,
                )
        finally:
            if closed:
                os.chmod(source, mode)
        os.chmod(target, mode | _OPEN_FOLDER)
    elif stat.S_ISREG(status.st_mode):
        closed = not (mode & _OPEN_FILE)
        if closed:
            os.chmod(source, mode | _OPEN_FILE)
            sealed.append([relpath, mode])
        try:
            shutil.copyfile(source, target)
        except FileNotFoundError:
            return
        finally:
            if closed:
                os.chmod(source, mode)
        os.chmod(target, mode | _OPEN_FILE)
    else:
        # a socket or a pipe has no copy: the replay runs without it
        return
    times = (status.st_atime_ns, status.st_mtime_ns)
    os.utime(target, ns=times, follow_symlinks=False)
