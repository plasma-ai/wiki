"""Test the ``parity.record`` and ``parity.replay`` modules.

The whole of v1's core and CLI suites is recorded and replayed through the
command line, which ``test_main`` covers for one test file. This module
holds each rule of the recorder and of the replay against a small checkout
of tests shaped like v1's: core tests that call the real engine in process,
a helper module and a CLI test that calls the console script through it,
and a format test. The tests run under the oracle side's pytest, and every
replayed command runs on a real side.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys
from collections.abc import Sequence
from typing import Optional

import pytest

from parity import compare, dispatch, oracle, record, replay, selfcheck, side, suite

from .conftest import _needs_git, _needs_unprivileged

__all__ = [
    'test_every_recorded_call_replays_as_its_command_line',
    'test_call_no_command_line_states_is_a_finding',
    'test_replay_that_departs_from_the_call_is_a_finding',
    'test_routed_test_is_no_finding',
    'test_difference_fails_a_routed_test_unless_a_registry_entry_explains_it',
    'test_clock_a_test_fixes_is_the_clock_of_the_replay',
    'test_tree_its_owner_cannot_read_is_replayed_sealed',
    'test_second_engine_takes_the_calls_of_the_ported_commands',
    'test_doctored_replay_is_reported',
    'test_format_calls_are_recorded_and_made_again',
    'test_recording_lists_the_kinds_each_test_met',
    'test_test_the_recorder_breaks_is_a_finding',
]

pytestmark = _needs_git

# a helper module shaped like the one v1's CLI tests take the console script
# from: the script beside the interpreter, run from a folder that is also its
# home, with its streams read as text
_HELPER = """
import os, pathlib, shutil, subprocess, sys

VENV_BIN = pathlib.Path(sys.executable).parent
WIKI = shutil.which('wiki', path=VENV_BIN)


def _wiki(cwd, *args):
    env = dict(os.environ)
    env['PATH'] = f'{VENV_BIN}{os.pathsep}{env["PATH"]}'
    env['HOME'] = str(cwd)
    return subprocess.run(
        [WIKI, *args], cwd=cwd, capture_output=True, text=True, env=env
    )
"""

# what every core test module starts with: the engine, and a wiki with one
# page that an update has yet to index
_HEAD = """
import pathlib

import pytest

from wiki.core.wiki import Wiki


def _wiki(path):
    wiki = Wiki(path)
    wiki.init(name='root')
    (path / 'notes').mkdir()
    page = '---\\ndesc: About alpha.\\n---\\n\\n# Alpha\\n\\nThe alpha page.\\n'
    (path / 'notes' / 'alpha.md').write_text(page, encoding='utf-8')
    return wiki
"""

# one call of each recorded method, with a result of each kind: a pending
# and a finished update, issues, a hit and a miss, and a call that raises
_COMMANDS = (
    _HEAD
    + """

def test_every_command(tmp_path):
    wiki = _wiki(tmp_path)
    assert wiki.update(check=True)
    assert wiki.lint()
    assert wiki.update()
    assert wiki.update() == []
    assert wiki.new('guides', desc='About guides.', content='Text.')
    assert 'alpha page' in wiki.read('notes/alpha')
    assert wiki.read('notes/alpha', start=0, stop=2, on='words')
    assert wiki.search('alpha', limit=1)
    assert wiki.search('zqxjkv') == []
    assert wiki.match('ALPHA', name='notes', ignore_case=True)
    assert wiki.match('zqxjkv') == []
    assert 'notes' in wiki.map(depth=1, words=False)
    with pytest.raises(FileNotFoundError):
        wiki.read('missing')
"""
)

# commands of the console script through the helper: ones that write stamps,
# one that reads, and one the argument parser ends
_CLI = """
from .conftest import _wiki


def test_commands(tmp_path):
    root = str(tmp_path / 'w')
    made = _wiki(tmp_path, 'init', 'root', '--path', root)
    assert made.returncode == 0
    assert 'WIKI_FREEZE_TIME' not in made.stderr
    page = '---\\ndesc: About alpha.\\n---\\n\\n# Alpha\\n\\nThe alpha page.\\n'
    (tmp_path / 'w' / 'alpha.md').write_text(page, encoding='utf-8')
    updated = _wiki(tmp_path, 'update', '--path', root)
    assert updated.returncode == 0
    assert 'WIKI_FREEZE_TIME' not in updated.stderr
    assert 'alpha page' in _wiki(tmp_path, 'read', 'alpha', '--path', root).stdout
    assert _wiki(tmp_path, 'lint', '--nope').returncode == 2
"""

# calls that no command line states: a unit `read` has no option for, a clock
# whose stamp no instant renders as, and a wiki that is the temporary folder
_UNIT = (
    _HEAD
    + """

def test_unit(tmp_path):
    wiki = _wiki(tmp_path)
    with pytest.raises(ValueError):
        wiki.read('notes/alpha', start=0, stop=1, on='paragraphs')
"""
)
_STAMP = (
    _HEAD
    + """

def test_stamp(tmp_path, monkeypatch):
    wiki = _wiki(tmp_path)
    monkeypatch.setattr(Wiki, '_utc_now', lambda self: 'no instant')
    assert wiki.update()
"""
)
_WHOLE = (
    _HEAD
    + """

def test_whole(tmp_path):
    with pytest.raises(Exception):
        Wiki(tmp_path.parent).read('missing')
"""
)

# calls that v1's command line does not repeat: an engine on a folder that
# declares no wiki, which the command line refuses, and a test that writes a
# file while its call runs
_UNDECLARED = (
    _HEAD
    + """

def test_undeclared(tmp_path):
    page = '---\\ndesc: About alpha.\\n---\\n\\n# Alpha\\n\\nThe alpha page.\\n'
    (tmp_path / 'alpha.md').write_text(page, encoding='utf-8')
    assert 'alpha page' in Wiki(tmp_path).read('alpha')
"""
)
_RACE = (
    _HEAD
    + """

def test_race(tmp_path, monkeypatch):
    wiki = _wiki(tmp_path)
    real = Wiki._utc_now

    def racy(self):
        (tmp_path / 'stray.txt').write_text('written mid-call', encoding='utf-8')
        return real(self)

    monkeypatch.setattr(Wiki, '_utc_now', racy)
    assert wiki.update()
"""
)

# a test that fixes the engine's clock, in process and for a command, and
# reads the stamp back from what each wrote
_CLOCK = (
    _HEAD
    + """

def test_clock(tmp_path, monkeypatch):
    wiki = _wiki(tmp_path)
    monkeypatch.setattr(Wiki, '_utc_now', lambda self: '2001-02-03T04:05:06Z')
    assert wiki.update()
    assert 'created: 2001-02-03T04:05:06Z' in wiki.read('notes/alpha')
"""
)
_CLI_CLOCK = """
from .conftest import _wiki


def test_command_clock(tmp_path, monkeypatch):
    root = str(tmp_path / 'w')
    assert _wiki(tmp_path, 'init', 'root', '--path', root).returncode == 0
    monkeypatch.setenv('WIKI_FREEZE_TIME', '2001-02-03T04:05:06Z')
    made = _wiki(
        tmp_path, 'new', 'guides', '--desc', 'About.', '--content', 'Text.', '--path', root
    )
    assert made.stderr.startswith('WIKI_FREEZE_TIME is set')
    index = (tmp_path / 'w' / 'guides' / '_index.md').read_text(encoding='utf-8')
    assert 'created: 2001-02-03T04:05:06Z' in index
"""

# a test that takes a folder away from its owner for one call
_SEALED = (
    _HEAD
    + """

def test_sealed(tmp_path):
    wiki = _wiki(tmp_path)
    assert wiki.update()
    (tmp_path / 'notes').chmod(0)
    try:
        with pytest.raises(PermissionError):
            wiki.lint()
    finally:
        (tmp_path / 'notes').chmod(0o755)
"""
)

# format calls with a value of each kind the JSON form marks, one that
# raises, and one whose outcome turns on a value its arguments do not hold
_FORMATS = """
import pytest

from wiki.core import format


def test_formats():
    assert format.quote('a: b') != 'a: b'
    assert format.quote('a: b') != 'a: b'
    assert format.parse_page('---\\nname: x\\n---\\n\\nbody\\n')[1]
    assert format.match_link_row('[[a|b]]: c')[0] == 'a'
    assert format.no_lint_lines('text\\n') == set()
    with pytest.raises(Exception):
        format.parse_page(None)
"""
_HIDDEN = """
import re

from wiki.core import format


def test_hidden(monkeypatch):
    monkeypatch.setattr(format, '_LINK_ROW', re.compile('never'))
    assert format.match_link_row('[[a|b]]: c') is None
"""

# a side that runs the command it is given and adds a byte to its stdout
_LOUD = """
import subprocess, sys
done = subprocess.run(sys.argv[1:], capture_output=True)
sys.stdout.buffer.write(done.stdout + b'!')
sys.stderr.buffer.write(done.stderr)
sys.exit(done.returncode)
"""

# a test that passes without the recorder and fails under it: it reads the
# attribute the recorder puts in place of the engine's method
_BROKEN = (
    _HEAD
    + """

def test_broken(tmp_path):
    assert Wiki.update.__qualname__ == 'Wiki.update'
    assert Wiki.__dict__['update'].__code__.co_name == 'update'
"""
)


def test_every_recorded_call_replays_as_its_command_line(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """Each call of a test is one command, replayed on the tree the call found.

    The core test makes thirteen calls over the seven recorded methods,
    and the CLI test four commands through the helper. Each is replayed
    from the copy of its own tree under two hash seeds: the two runs show
    no difference, and v1's command line does what the call did in its
    test, so nothing is a finding. A helper command runs under a clock the
    recorder fixes, and the test still reads the stderr of a command with
    no fixed clock.
    """
    recording = _record(tmp_path, engine, core=_COMMANDS, cli=_CLI)
    assert recording.tests.report['counts']['outcomes'] == {'passed': 2}
    result = replay.run(recording, selfcheck._seeded(engine))
    assert (result.findings, result.differences, result.excused) == ((), (), ())
    counts = result.report['counts']
    replayed = {name: count['replayed'] for name, count in counts['commands'].items()}
    assert replayed == {
        'cli init': 1,
        'cli lint': 1,
        'cli read': 1,
        'cli update': 1,
        'core lint': 1,
        'core map': 1,
        'core match': 2,
        'core new': 1,
        'core read': 3,
        'core search': 2,
        'core update': 3,
    }
    assert (counts['calls'], counts['replayed'], counts['tests']) == (17, 17, 2)
    # every call ran on both seeds
    assert all(len(call['runs']) == 2 for call in result.report['calls'])


@pytest.mark.parametrize(
    argnames=('body', 'reason'),
    argvalues=[
        (_UNIT, "no option for the unit 'paragraphs'"),
        (_STAMP, 'a stamp no instant states'),
        (_WHOLE, 'the whole temporary folder'),
    ],
    ids=['unit', 'stamp', 'whole'],
)
def test_call_no_command_line_states_is_a_finding(
    tmp_path: pathlib.Path,
    engine: side.Side,
    body: str,
    reason: str,
) -> None:
    """A call the recorder cannot write as a command is reported, never skipped.

    ``read`` has no option for an unknown unit, ``WIKI_FREEZE_TIME``
    cannot make the engine stamp a text that is no instant, and a wiki
    that is the temporary folder itself has no tree of its own to copy. A
    replay of some other command would compare some other scenario, so
    each call is a finding under its test.
    """
    recording = _record(tmp_path, engine, core=body)
    result = replay.run(recording, selfcheck._seeded(engine))
    unformed = [finding for finding in result.findings if finding.kind == 'unformed']
    assert len(unformed) == 1
    assert reason in unformed[0].detail
    assert result.report['counts']['unformed'] == 1


@pytest.mark.parametrize(
    argnames=('body', 'problem'),
    argvalues=[
        (_UNDECLARED, 'exit code 2, the call asks for 0'),
        (_RACE, 'stray.txt'),
    ],
    ids=['undeclared', 'race'],
)
def test_replay_that_departs_from_the_call_is_a_finding(
    tmp_path: pathlib.Path,
    engine: side.Side,
    body: str,
    problem: str,
) -> None:
    """A command that does not do what the test's call did is reported.

    The command line refuses a folder that declares no wiki, where the
    engine class reads it; and a file a test writes while its call runs
    is in the tree the call left and not in the one the command leaves.
    Both runs of the replay agree with each other all the same, so only
    the comparison with the test's own call tells that the replay ran
    another scenario.
    """
    recording = _record(tmp_path, engine, core=body)
    result = replay.run(recording, selfcheck._seeded(engine))
    assert result.differences == ()
    unfaithful = [
        finding for finding in result.findings if finding.kind == 'unfaithful'
    ]
    assert len(unfaithful) == 1
    assert problem in unfaithful[0].detail


def test_routed_test_is_no_finding(tmp_path: pathlib.Path, engine: side.Side) -> None:
    """A test the routing table names may have calls the replay cannot hold.

    Its calls are still replayed and compared, and only the finding about
    the command line is left out. A row for a test whose calls all replay
    as their commands is a finding of its own: the replay holds that
    test, and the row would take it out for nothing.
    """
    recording = _record(tmp_path, engine, core=_UNDECLARED)
    (call,) = recording.calls
    result = replay.run(recording, selfcheck._seeded(engine), routed=[call.test])
    assert result.findings == ()
    counts = result.report['counts']
    assert (counts['unfaithful'], counts['tests_routed']) == (1, 1)
    (row,) = result.report['calls']
    assert row['routed'] is True
    assert len(row['runs']) == 2
    assert replay.unneeded([call.test], result.report) == []
    # a test the replay holds, routed by the id of its function
    recording = _record(tmp_path / 'other', engine, core=_COMMANDS)
    result = replay.run(recording, selfcheck._seeded(engine))
    test = recording.calls[0].test
    assert [
        (finding.kind, finding.subject)
        for finding in replay.unneeded([test, 'tests/test_x.py::test_y'], result.report)
    ] == [('unneeded', test)]
    assert replay.unneeded([test], result.report, differing=[test]) == []


def test_difference_fails_a_routed_test_unless_a_registry_entry_explains_it(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A routing row does not hide what two sides do differently.

    The second side adds a byte to every stdout, so each call differs.
    The differences fail the replay for a test that is not routed and
    for one that is. Only a row that rests on a registry entry takes
    them out of the verdict, and the report keeps them.
    """
    recording = _record(tmp_path, engine, core=_UNDECLARED)
    (call,) = recording.calls
    loud = tmp_path / 'loud.py'
    loud.write_text(_LOUD, encoding='utf-8')
    other = side.Side(name='loud', argv=(sys.executable, f'{loud}', *engine.argv))
    sides = [engine, other]
    expected = (compare.Difference(call.test, 'loud', 'main-0 read', 'stdout'),)
    for routed in ([], [call.test]):
        result = replay.run(recording, sides, routed=routed, second=True)
        assert result.differences == expected
    result = replay.run(
        recording,
        sides,
        routed=[call.test],
        explained=[call.test],
        second=True,
    )
    assert (result.findings, result.differences) == ((), ())
    (row,) = result.report['calls']
    assert [found['aspect'] for found in row['differences']] == ['stdout']


def test_clock_a_test_fixes_is_the_clock_of_the_replay(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A stamp a test fixes is the stamp the replayed command writes.

    One test puts its own clock method on the engine, and one sets
    ``WIKI_FREEZE_TIME`` for a command. Each replayed command writes the
    test's stamp into the page, so its tree is the tree the call left;
    under the replay's own clock the two would differ in every stamp.
    """
    recording = _record(tmp_path, engine, core=_CLOCK, cli=_CLI_CLOCK)
    assert recording.tests.report['counts']['outcomes'] == {'passed': 2}
    result = replay.run(recording, selfcheck._seeded(engine))
    assert (result.findings, result.differences) == ((), ())
    clocks = {
        call.command: dt.datetime.fromisoformat(call.env['WIKI_FREEZE_TIME'])
        for call in recording.calls
    }
    assert (
        clocks['update']
        == clocks['new']
        == dt.datetime(2001, 2, 3, 4, 5, 6, tzinfo=dt.UTC)
    )


@_needs_unprivileged
def test_tree_its_owner_cannot_read_is_replayed_sealed(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A folder a test closes to its owner is closed for the replayed command.

    The recorder opens the folder to copy it and closes it again, and the
    replay seals its own copy, so the command meets the folder the call
    met and does what the call did.
    """
    recording = _record(tmp_path, engine, core=_SEALED)
    assert recording.tests.report['counts']['outcomes'] == {'passed': 1}
    sealed = recording.calls[-1].before.sealed
    assert [(pathlib.PurePath(relpath).name, mode) for relpath, mode in sealed] == [
        ('notes', 0)
    ]
    result = replay.run(recording, selfcheck._seeded(engine))
    assert (result.findings, result.differences) == ((), ())


def test_second_engine_takes_the_calls_of_the_ported_commands(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """With a second engine, the calls of the ported commands are replayed.

    Each such call runs on v1, on the engine and on the engine with one
    thread, and every other call is left to a later run.
    """
    recording = _record(tmp_path, engine, core=_COMMANDS)
    other = oracle.side(oracle.load(), 'campaign')
    sides = [engine, other, replay.threaded(other)]
    result = replay.run(recording, sides, commands=['read', 'map'], second=True)
    assert (result.findings, result.differences) == ((), ())
    assert list(result.report['counts']['commands']) == ['core map', 'core read']
    names = [engine.name, other.name, f'{other.name} on one thread']
    assert all(list(call['runs']) == names for call in result.report['calls'])


def test_doctored_replay_is_reported(tmp_path: pathlib.Path, engine: side.Side) -> None:
    """A replay of the wrong command, or on a side that differs, does not pass.

    The ``argv`` control drops ``--check`` from the one ``update`` call
    that found pending changes, so the command writes them: the replay
    must call it unfaithful. The ``side`` control changes one byte of one
    command's stdout on one side, which must show as that one difference.
    A recording with no call to doctor proves nothing, and says so.
    """
    recording = _record(tmp_path, engine, core=_COMMANDS)
    for name in replay.CONTROLS:
        assert replay.control(recording, engine, name) == (
            [],
            {'doctored': 1, 'reported': 1},
        )
    recording = _record(tmp_path / 'other', engine, core=_UNIT)
    findings, counts = replay.control(recording, engine, 'argv')
    assert [finding.kind for finding in findings] == ['control']
    assert counts == {'doctored': 0, 'reported': 0}


def test_format_calls_are_recorded_and_made_again(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """Each distinct format call is recorded once, and gives its outcome again.

    A call made twice is one record with a count of two. A tuple and a set
    come back as what they were when the call is made again in a fresh
    interpreter, and a call that raises raises again. A
    call whose outcome turns on a value the test patched gives another
    outcome there, which is a finding.
    """
    recording = _record(tmp_path, engine, formats=_FORMATS)
    counts = sorted((entry['function'], entry['count']) for entry in recording.formats)
    assert counts == [
        ('match_link_row', 1),
        ('no_lint_lines', 1),
        ('parse_page', 1),
        ('parse_page', 1),
        ('quote', 2),
    ]
    outcomes = [next(iter(entry['outcome'])) for entry in recording.formats]
    assert sorted(outcomes) == ['raised', *['returned'] * 4]
    source = suite.source(oracle.load())
    work = tmp_path / 'made'
    findings, report = replay.formats(recording, source, engine, work=work)
    assert findings == []
    assert (report['calls'], report['distinct']) == (6, 5)
    recording = _record(tmp_path / 'other', engine, formats=_HIDDEN)
    work = tmp_path / 'made again'
    findings, _ = replay.formats(recording, source, engine, work=work)
    assert [(finding.kind, finding.subject) for finding in findings] == [
        ('format', 'match_link_row')
    ]


def test_recording_lists_the_kinds_each_test_met(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """The recording says which notice and issue kinds each test met in process.

    The first update of the test adds the page's link to its folder's
    index, and its lint reports issues.
    """
    recording = _record(tmp_path, engine, core=_COMMANDS)
    ((test, kinds),) = recording.kinds.items()
    assert test.endswith('::test_every_command')
    assert kinds['notices']['link_add'] >= 1
    assert sum(kinds['issues'].values()) >= 1


def test_test_the_recorder_breaks_is_a_finding(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A test that fails under the recorder is reported with the replay.

    The recorder stands in for the engine's methods, and a test that
    looks at the method itself sees the stand-in. Such a test did not
    run as v1 runs it, so its record is not v1's.
    """
    recording = _record(tmp_path, engine, core=_BROKEN)
    result = replay.run(recording, selfcheck._seeded(engine))
    assert [(finding.kind, finding.detail) for finding in result.findings] == [
        ('failed', '')
    ]


# ------ helpers


def _record(
    tmp_path: pathlib.Path,
    reference: side.Side,
    *,
    core: str = '',
    cli: str = '',
    formats: str = '',
    jobs: Optional[int] = None,
    only: Sequence[str] = (),
) -> record.Recording:
    """Record a checkout of tests shaped like v1's.

    The checkout holds ``core`` as a core test module, ``formats`` as the
    format test module and ``cli`` as a CLI test module beside the helper
    module. As in v1, each package imports what it holds.
    """
    checkout = tmp_path / 'checkout'
    modules = {
        'tests/test_core/test_calls.py': core,
        'tests/test_core/test_format.py': formats,
        'tests/test_cli/conftest.py': _HELPER,
        'tests/test_cli/test_commands.py': cli,
    }
    for relpath, text in modules.items():
        path = checkout / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    packages = {
        'tests': 'from .test_cli import *\nfrom .test_core import *\n',
        'tests/test_core': 'from .test_calls import *\nfrom .test_format import *\n',
        'tests/test_cli': 'from .test_commands import *\n',
    }
    for relpath, text in packages.items():
        (checkout / relpath / '__init__.py').write_text(text, encoding='utf-8')
    (checkout / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
    python = suite.source(oracle.load()).python
    source = suite.Source(python=python, checkout=checkout)
    work = tmp_path / 'work'
    dispatcher = dispatch.build(work / 'bin', reference=reference)
    return record.run(source, dispatcher, work=work / 'record', jobs=jobs, only=only)
