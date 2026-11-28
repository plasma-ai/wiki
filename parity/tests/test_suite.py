"""Test the ``parity.suite`` module.

The whole of v1's CLI suite runs through the command line in
``test_main``. This module holds each rule of the run against a small
folder of tests shaped like v1's: a helper module that names the console
script, tests that call it, and packages that import their test modules.
The tests run under the oracle side's pytest, and their calls reach real
sides.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import signal
import sys
from collections.abc import Sequence
from typing import Optional

import pytest

from parity import dispatch, oracle, side, suite

from .conftest import _needs_git

__all__ = [
    'test_every_call_of_a_test_goes_through_the_dispatcher',
    'test_what_a_module_takes_at_import_is_the_dispatcher',
    'test_test_that_does_not_pass_is_a_finding',
    'test_test_file_that_is_not_loaded_is_a_finding',
    'test_ported_commands_go_to_the_second_engine',
    'test_test_holds_the_version_to_the_side_that_prints_it',
    'test_call_that_does_not_prove_its_side_is_a_finding',
    'test_call_outside_the_dispatcher_or_a_trace_is_a_finding',
    'test_tests_the_settings_leave_out_are_listed',
    'test_side_that_no_test_called_is_a_finding',
    'test_run_that_ran_no_test_is_an_error',
    'test_run_started_with_interrupts_ignored_still_interrupts',
]

pytestmark = _needs_git

# the folder of the tests, relative to their checkout, as in v1
_FOLDER = 'tests/test_cli'

# a helper module shaped like the one v1's CLI tests take the console script
# from: the script beside the interpreter, and its folder at the head of PATH
_HELPER = """
import os, pathlib, shutil, subprocess, sys

VENV_BIN = pathlib.Path(sys.executable).parent
WIKI = shutil.which('wiki', path=VENV_BIN)


def _env():
    env = dict(os.environ)
    env['PATH'] = f'{VENV_BIN}{os.pathsep}{env["PATH"]}'
    return env


def _wiki(*args):
    return subprocess.run([WIKI, *args], capture_output=True, text=True, env=_env())
"""
# the same helper without the folder: a seam the plugin cannot point anywhere
_SEAMLESS = _HELPER.replace('VENV_BIN', 'BIN')

# tests that reach the console script each of the three ways v1's tests do,
# and two that share a fixture which calls it
_CALLS = """
import subprocess

import pytest

from .conftest import WIKI, _env, _wiki


@pytest.fixture(scope='module')
def shared():
    assert _wiki('--help').returncode == 0


def test_by_the_helper():
    assert _wiki('--version').returncode == 0


def test_by_the_modules_own_name():
    assert subprocess.run([WIKI, '--version'], env=_env()).returncode == 0


def test_by_the_bare_name():
    assert subprocess.run(['wiki', '--version'], env=_env()).returncode == 0


def test_with_a_shared_fixture(shared):
    assert _wiki('--version').returncode == 0


def test_with_it_again(shared):
    assert _wiki('--version').returncode == 0
"""

# a module that takes the console script while it is imported: as a command
# it keeps, as a parameter of a test, and in a call it makes there and then
_EARLY = """
import subprocess

import pytest

from .conftest import WIKI, _env

ARGV = [WIKI, '--version']
DONE = subprocess.run(ARGV, env=_env())


@pytest.mark.parametrize('path', [WIKI])
def test_by_what_the_module_kept(path):
    assert DONE.returncode == 0
    assert subprocess.run(ARGV, env=_env()).returncode == 0
    assert subprocess.run([path, '--version'], env=_env()).returncode == 0
"""

# one test for each outcome a test can have
_OUTCOMES = """
import pytest

from .conftest import _wiki


@pytest.fixture
def broken():
    yield
    raise RuntimeError('teardown')


def test_passes():
    assert _wiki('--version').returncode == 0


def test_fails():
    assert _wiki('--version').returncode == 1


def test_errors(broken):
    pass


@pytest.mark.skip(reason='left out')
def test_skipped():
    pass


@pytest.mark.xfail(reason='known')
def test_expected_failure():
    assert _wiki('--version').returncode == 1


@pytest.mark.xfail(reason='known')
def test_unexpected_pass():
    pass
"""

# one test that calls a root option and a command
_PAIR = """
from .conftest import _wiki


def test_pair():
    assert _wiki('--version').returncode == 0
    assert _wiki('--help').returncode == 0
"""

# a test file that fails while it is imported, and one that skips itself there
_UNLOADABLE = """
import nothing_here
"""
_UNWANTED = """
import pytest

pytest.skip('left out', allow_module_level=True)
"""

# a helper module that ends the run as failed though every test passed
_FAILING = f"""{_HELPER}

def pytest_sessionfinish(session):
    session.exitstatus = 1
"""

# a test that calls through the dispatcher, one that runs the engine by its
# own command, and one that calls with no trace file
_STRAY = """
import subprocess

from .conftest import WIKI, _env, _wiki


def test_traced():
    assert _wiki('--version').returncode == 0


def test_outside_the_dispatcher():
    assert subprocess.run([*{argv!r}, '--version'], env=_env()).returncode == 0


def test_outside_the_trace():
    env = _env()
    del env[{trace!r}]
    assert subprocess.run([WIKI, '--version'], env=env).returncode == 0
"""

# pytest settings that leave the marked test out, as v1's leave its online one
_SETTINGS = "[pytest]\naddopts = -m 'not online'\nmarkers = online: left out\n"
_MARKED = """
import pytest

from .conftest import _wiki


def test_kept():
    assert _wiki('--version').returncode == 0


@pytest.mark.online
def test_left_out():
    pass
"""

# a test that calls nothing
_IDLE = """
def test_idle():
    pass
"""

# a test whose command interrupts itself, as a test of Ctrl-C does
_INTERRUPTED = """
import signal, subprocess, sys

from .conftest import _wiki


def test_interrupt_ends_a_command():
    assert _wiki('--version').returncode == 0
    code = 'import os, signal; os.kill(os.getpid(), signal.SIGINT)'
    done = subprocess.run([sys.executable, '-c', code], capture_output=True)
    assert done.returncode == -signal.SIGINT
"""

# an engine that succeeds only under its side's own variable, and leaves an
# identity record nobody asked for when it is handed a trace file
_OWN = f"""
import json, os, sys
trace = os.environ.get({side.TRACE!r})
if trace:
    with open(trace, 'a') as handle:
        handle.write(json.dumps({{'engine': 'own'}}) + chr(10))
sys.exit(os.environ.get('PARITY_OWN') != 'own')
"""

# a test that holds what `--version` prints to the version its module names,
# as v1's does with the version of the package it imports, and makes one call
# that stays on v1
_VERSION = """
from .conftest import _wiki

WIKI_VERSION = 'the version of the package the tests import'


def test_version():
    assert _wiki('--version').stdout.strip() == WIKI_VERSION
    assert _wiki('--help').returncode == 0
"""

# an engine that leaves the identity record it is given and prints the
# version the record states
_STATED = f"""
import json, os, sys
with open(os.environ[{side.TRACE!r}], 'a') as handle:
    handle.write(sys.argv[1] + chr(10))
print(json.loads(sys.argv[1])['version'])
"""

# an engine that runs the command it is given and leaves the identity record
# as its mode says: once, a second time, or not at all
_RELAY = f"""
import os, pathlib, subprocess, sys
mode, argv = sys.argv[1], sys.argv[2:]
env = dict(os.environ)
trace = pathlib.Path(env[{side.TRACE!r}])
if mode == 'never':
    del env[{side.TRACE!r}]
code = subprocess.run(argv, env=env).returncode
if mode == 'twice':
    lines = trace.read_bytes().splitlines(keepends=True)
    trace.write_bytes(b''.join([*lines, lines[-1]]))
sys.exit(code)
"""


def test_every_call_of_a_test_goes_through_the_dispatcher(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A test reaches the dispatcher by the helper, by its own name and by PATH.

    v1's tests run the console script through the helper's runner, through
    the path they import from the helper, and by the bare name on the
    ``PATH`` the helper builds, as git does for the merge driver. Each of
    the three is one call on the v1 side, with its identity record. A
    fixture that two tests share is built inside the first of them, so its
    call counts for that test.
    """
    result = _run(tmp_path, _CALLS, reference=engine)
    assert result.findings == ()
    tests = result.report['tests']
    calls = {_name(test['id']): test['calls'] for test in tests}
    assert calls == {
        'test_by_the_helper': {engine.name: 1},
        'test_by_the_modules_own_name': {engine.name: 1},
        'test_by_the_bare_name': {engine.name: 1},
        'test_with_a_shared_fixture': {engine.name: 2},
        'test_with_it_again': {engine.name: 1},
    }
    assert all(test['identified'] for test in tests)
    assert result.report['counts']['sides'] == {engine.name: {'tests': 5, 'calls': 6}}


def test_what_a_module_takes_at_import_is_the_dispatcher(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A test module never sees the console script of its own venv.

    The helper names the dispatcher before a test module imports it. So a
    command the module builds at import and a path it hands a test as a
    parameter both reach the dispatcher, and count for the test that runs
    them. A call the module makes while it is imported belongs to no test,
    and is reported as one.
    """
    result = _run(tmp_path, _EARLY, reference=engine)
    assert result.findings == (suite.Finding('untraced', '1 call'),)
    (test,) = result.report['tests']
    assert test['outcome'] == 'passed'
    assert test['calls'] == {engine.name: 2}
    assert test['identified'] is True


def test_test_that_does_not_pass_is_a_finding(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """Every outcome but a pass is reported, a skip and an expected failure too.

    A test that is skipped, or that fails as its mark expects, asserted
    nothing about the engine its calls went to, so a run that counted it
    as done would prove less than it says.
    """
    result = _run(tmp_path, _OUTCOMES, reference=engine)
    found = {(finding.kind, _name(finding.subject)) for finding in result.findings}
    assert found == {
        ('failed', 'test_fails'),
        ('error', 'test_errors'),
        ('skipped', 'test_skipped'),
        ('xfailed', 'test_expected_failure'),
        ('xpassed', 'test_unexpected_pass'),
    }
    assert result.report['counts']['outcomes'] == {
        'error': 1,
        'failed': 1,
        'passed': 1,
        'skipped': 1,
        'xfailed': 1,
        'xpassed': 1,
    }
    assert b'RuntimeError: teardown' in result.log


@pytest.mark.parametrize(
    argnames=('other', 'jobs', 'outcome'),
    argvalues=[(_UNLOADABLE, 2, 'error'), (_UNWANTED, None, 'skipped')],
    ids=['fails in workers', 'skips itself'],
)
def test_test_file_that_is_not_loaded_is_a_finding(
    tmp_path: pathlib.Path,
    engine: side.Side,
    other: str,
    jobs: Optional[int],
    outcome: str,
) -> None:
    """A test file that is not loaded is reported, though no test of it ran.

    Where workers run the tests, pytest goes on past a file that fails
    while it is imported, and a file may skip itself there. No report of
    a test names such a file. Its tests would be missing from a run that
    passes, so the file itself is reported, with the outcome its tests
    would have had.
    """
    result = _run(tmp_path, _PAIR, reference=engine, other=other, jobs=jobs)
    assert result.findings == (suite.Finding(outcome, f'{_FOLDER}/test_other.py'),)
    assert result.report['counts']['outcomes'] == {outcome: 1, 'passed': 1}


def test_ported_commands_go_to_the_second_engine(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A call belongs to the second engine exactly when its command is ported.

    The test calls ``--version``, which is ported, and ``--help``, which is
    not, so each side takes one call. The second engine is a command that
    succeeds only under its side's own variable, so the test passes only
    if the call ran under it. It has no identity, so it gets no trace file
    and owes no record. A ported command that no test called is reported:
    the run proved nothing about it.
    """
    second = side.Side(
        name='second',
        argv=(sys.executable, '-c', _OWN),
        env={'PARITY_OWN': 'own'},
    )
    commands = ['--version', 'lint']
    result = _run(tmp_path, _PAIR, reference=engine, engine=second, commands=commands)
    (test,) = result.report['tests']
    assert test['outcome'] == 'passed'
    assert test['calls'] == {engine.name: 1, 'second': 1}
    assert test['commands'] == {'--version': 1}
    assert test['identified'] is True
    assert result.findings == (suite.Finding('unused', 'command lint'),)
    assert result.report['counts']['commands'] == {
        '--version': {'tests': 1, 'calls': 1},
        'lint': {'tests': 0, 'calls': 0},
    }


@pytest.mark.parametrize('ported', [False, True], ids=['v1', 'second engine'])
def test_test_holds_the_version_to_the_side_that_prints_it(
    tmp_path: pathlib.Path,
    engine: side.Side,
    ported: bool,
) -> None:
    """A test expects of ``--version`` the version of the side the call goes to.

    v1's test compares what ``--version`` prints with the version of the
    package it imports, which is v1's. Once ``--version`` is ported the call
    goes to a second engine, which has a version of its own. The module's
    name is set to the version the side that takes the call states, so the
    test passes on either side, and it still holds the output to a version
    the command did not supply.
    """
    identity = {'engine': 'second', 'version': '9.9.9', 'build': 'commit'}
    second = side.Side(
        name='second',
        argv=(sys.executable, '-c', _STATED, json.dumps(identity)),
        identity=identity,
    )
    result = _run(
        tmp_path,
        _VERSION,
        reference=engine,
        engine=second if ported else None,
        commands=['--version'] if ported else [],
    )
    assert result.findings == ()
    (test,) = result.report['tests']
    assert test['outcome'] == 'passed'
    calls = {engine.name: 1, 'second': 1} if ported else {engine.name: 2}
    assert test['calls'] == calls


@pytest.mark.parametrize(
    argnames=('wrote', 'build', 'identified'),
    argvalues=[
        ('once', None, True),
        ('twice', None, False),
        ('never', None, False),
        ('once', 'another', False),
    ],
    ids=['once', 'twice', 'never', 'another build'],
)
def test_call_that_does_not_prove_its_side_is_a_finding(
    tmp_path: pathlib.Path,
    engine: side.Side,
    wrote: str,
    build: Optional[str],
    identified: bool,
) -> None:
    """A test's calls must leave the records of the sides they went to.

    The second engine is v1 behind a relay. A call that leaves its record
    twice ran the engine twice, one that leaves none did not run it, and a
    record of another build is another engine: each is reported under the
    test that made the call, though the test itself passes.
    """
    relay = tmp_path / 'relay.py'
    relay.write_text(_RELAY, encoding='utf-8')
    argv = (sys.executable, f'{relay}', wrote, *engine.argv)
    identity = dict(engine.identity)
    if build is not None:
        identity['build'] = build
    second = dataclasses.replace(engine, name='second', argv=argv, identity=identity)
    result = _run(
        tmp_path,
        _PAIR,
        reference=engine,
        engine=second,
        commands=['--version'],
    )
    (test,) = result.report['tests']
    assert test['outcome'] == 'passed'
    assert test['identified'] is identified
    expected = () if identified else (suite.Finding('identity', test['id']),)
    assert result.findings == expected


def test_call_outside_the_dispatcher_or_a_trace_is_a_finding(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """An engine run past the dispatcher, or with no trace file, is reported.

    A test that starts the engine by its own command leaves a record no
    call of the dispatcher asked for. A call made under an environment
    without the trace variable belongs to no test: the dispatcher notes it
    in a file of the run's own, and the run reports how many there were.
    """
    body = _STRAY.format(argv=list(engine.argv), trace=side.TRACE)
    result = _run(tmp_path, body, reference=engine)
    found = {(finding.kind, _name(finding.subject)) for finding in result.findings}
    assert found == {
        ('identity', 'test_outside_the_dispatcher'),
        ('untraced', '1 call'),
    }
    assert result.report['counts']['untraced'] == 1


@pytest.mark.parametrize('jobs', [None, 2], ids=['one process', 'workers'])
def test_tests_the_settings_leave_out_are_listed(
    tmp_path: pathlib.Path,
    engine: side.Side,
    jobs: Optional[int],
) -> None:
    """A test that the tests' own pytest settings leave out is listed, not lost.

    v1's settings leave out the test that needs the network. The run
    follows them, and the result names each test it left out, whether one
    process collected the tests or several workers did.
    """
    result = _run(tmp_path, _MARKED, reference=engine, settings=_SETTINGS, jobs=jobs)
    assert result.findings == ()
    assert [_name(test['id']) for test in result.report['tests']] == ['test_kept']
    assert [_name(id) for id in result.report['deselected']] == ['test_left_out']


def test_side_that_no_test_called_is_a_finding(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A run in which a side took no call proved nothing about that side."""
    result = _run(tmp_path, _IDLE, reference=engine)
    assert result.findings == (suite.Finding('unused', f'side {engine.name}'),)
    assert result.report['counts']['uncalled'] == 1


@pytest.mark.parametrize(
    argnames=('helper', 'body', 'other', 'jobs'),
    argvalues=[
        (_SEAMLESS, _CALLS, '', None),
        (_SEAMLESS, _CALLS, '', 2),
        (_HELPER, '', '', None),
        (_HELPER, _PAIR, _UNLOADABLE, None),
        (_FAILING, _PAIR, '', None),
    ],
    ids=['seam', 'seam in workers', 'empty', 'unloadable file', 'exit code'],
)
def test_run_that_ran_no_test_is_an_error(
    tmp_path: pathlib.Path,
    engine: side.Side,
    helper: str,
    body: str,
    other: str,
    jobs: Optional[int],
) -> None:
    """A run that cannot point the tests at the dispatcher is no result.

    A helper module that names the console script another way leaves the
    tests calling the script of their own venv, and a folder with no test
    runs nothing. One process stops at a test file it cannot load and runs
    no test. And a run that pytest ends as failed, with every test passed,
    failed in a way no row tells. Each would end with nothing to report,
    so the run raises instead of returning a result that reads as a pass.
    """
    with pytest.raises(RuntimeError, match='did not run the tests'):
        _run(
            tmp_path,
            body,
            reference=engine,
            helper=helper,
            other=other,
            jobs=jobs,
        )


def test_run_started_with_interrupts_ignored_still_interrupts(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A test's interrupt reaches its command, however the run was started.

    A shell starts a detached job with interrupts ignored, and every
    process below it inherits that. A test that interrupts a command, as
    v1's test of Ctrl-C does, would then interrupt nothing and fail in
    every detached run.
    """
    held = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        result = _run(tmp_path, _INTERRUPTED, reference=engine)
    finally:
        signal.signal(signal.SIGINT, held)
    assert result.findings == ()


# ------ helpers


def _run(
    tmp_path: pathlib.Path,
    body: str,
    *,
    reference: side.Side,
    engine: Optional[side.Side] = None,
    commands: Sequence[str] = (),
    helper: str = _HELPER,
    other: str = '',
    settings: str = '[pytest]\n',
    jobs: Optional[int] = None,
) -> suite.Result:
    """Run a folder of tests shaped like v1's through a new dispatcher.

    The folder holds ``helper`` as its helper module, ``body`` as its test
    module and ``other`` as a second one, and runs under the oracle side's
    interpreter with ``settings`` as its pytest settings. As in v1, each
    package imports what it holds, so the first import of the helper
    imports the test module too; ``other`` is a module no package names.
    """
    checkout = tmp_path / 'checkout'
    folder = checkout / _FOLDER
    folder.mkdir(parents=True)
    (checkout / 'pytest.ini').write_text(settings, encoding='utf-8')
    package = 'from .test_cli import *\n'
    (checkout / 'tests' / '__init__.py').write_text(package, encoding='utf-8')
    package = 'from .test_calls import *\n' if body else ''
    (folder / '__init__.py').write_text(package, encoding='utf-8')
    (folder / 'conftest.py').write_text(helper, encoding='utf-8')
    if body:
        (folder / 'test_calls.py').write_text(body, encoding='utf-8')
    if other:
        (folder / 'test_other.py').write_text(other, encoding='utf-8')
    python = suite.source(oracle.load()).python
    source = suite.Source(python=python, checkout=checkout)
    work = tmp_path / 'work'
    dispatcher = dispatch.build(
        work / 'bin',
        reference=reference,
        engine=engine,
        commands=commands,
    )
    return suite.run(source, dispatcher, work=work / 'tests', jobs=jobs)


def _name(id: str) -> str:
    """Return the last part of a test's id: the name of its function."""
    return id.rpartition('::')[2]
