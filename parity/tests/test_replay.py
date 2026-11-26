"""Test the ``parity.record``, ``parity.replay``, ``parity.homes`` and ``parity.kinds`` modules.

The whole of v1's core and CLI suites is recorded and replayed through the
command line, which ``test_main`` covers for one test file. This module
holds each rule of the recorder, of the replay and of the account a whole
replay gives of v1's tests and kinds, against a small checkout of tests
shaped like v1's: core tests that call the real engine in process, a helper
module and a CLI test that calls the console script through it, and a
format test. The tests run under the oracle side's pytest, and every
replayed command runs on a real side.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import pathlib
import sys
from collections.abc import Sequence
from typing import Optional

import pytest

from parity import (
    compare,
    dispatch,
    homes,
    kinds,
    oracle,
    record,
    replay,
    selfcheck,
    side,
    standin,
    suite,
)
from parity.routing import Route

from .conftest import _needs_git, _needs_unprivileged

__all__ = [
    'test_every_recorded_call_replays_as_its_command_line',
    'test_call_no_command_line_states_is_a_finding',
    'test_replay_that_departs_from_the_call_is_a_finding',
    'test_command_is_held_to_what_the_call_left_and_printed',
    'test_command_is_held_to_what_the_call_returned_raised_and_noted',
    'test_command_under_a_running_clock_is_not_replayed',
    'test_record_of_a_path_outside_the_recording_is_not_played',
    'test_routed_test_is_no_finding',
    'test_difference_fails_a_routed_test_unless_a_registry_entry_explains_it',
    'test_call_of_a_shared_fixture_belongs_to_no_test',
    'test_call_on_input_outside_the_supported_set_is_a_finding',
    'test_clock_a_test_fixes_is_the_clock_of_the_replay',
    'test_tree_its_owner_cannot_read_is_replayed_sealed',
    'test_entry_a_test_wrote_is_among_the_trees_of_its_calls',
    'test_second_engine_takes_the_calls_of_the_ported_commands',
    'test_side_that_leaves_another_tree_is_a_difference',
    'test_registry_entry_excuses_nothing_between_two_builds_of_v1',
    'test_doctored_replay_is_reported',
    'test_command_the_helper_does_not_start_is_counted',
    'test_helper_the_recorder_cannot_watch_is_an_error',
    'test_format_calls_are_recorded_and_made_again',
    'test_format_call_with_no_json_form_is_a_finding',
    'test_recording_lists_the_kinds_each_test_met',
    'test_kind_list_is_held_to_the_engine_and_to_the_replay',
    'test_issue_kind_the_source_does_not_name_ends_the_recording',
    'test_every_test_has_one_home',
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

# the same helper with the function that starts a command bound by name, so
# the module holds nothing the recorder can stand in for
_UNWATCHED = _HELPER.replace('shutil, subprocess, sys', 'shutil, sys').replace(
    'return subprocess.run(',
    'from subprocess import run\n    return run(',
)

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
# and a finished update, issues, a hit and a miss, a pattern that starts
# with a dash, and a call that raises
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
    assert wiki.match('-zqxjkv') == []
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

# more calls that no command line states: a parameter that no option of the
# command carries, as a later v1 may add one; a wiki outside the temporary
# folder; a command started in a folder outside it; and a command the test
# leaves the clock running for
_WIDER = (
    _HEAD
    + """
import inspect


def test_wider(tmp_path):
    wiki = _wiki(tmp_path)
    update = Wiki.update.__wrapped__
    wider = lambda self, name=None, *, check=False, fast=False: None
    update.__signature__ = inspect.signature(wider)
    try:
        with pytest.raises(TypeError):
            wiki.update(fast=True)
    finally:
        del update.__signature__
"""
)
_OUTSIDE = (
    _HEAD
    + """

def test_outside(tmp_path_factory):
    root = tmp_path_factory.getbasetemp().parent / 'outside'
    root.mkdir()
    assert _wiki(root).update()
"""
)
_AWAY = """
from .conftest import _wiki


def test_away(tmp_path_factory):
    away = tmp_path_factory.getbasetemp().parent / 'away'
    away.mkdir()
    assert _wiki(away, '--version').returncode == 0
"""
_CLI_RUNNING = """
from .conftest import _wiki


def test_running_clock(tmp_path, monkeypatch):
    monkeypatch.setenv('WIKI_FREEZE_TIME', ' ')
    made = _wiki(tmp_path, 'init', 'root', '--path', str(tmp_path / 'w'))
    assert made.returncode == 0
    assert 'WIKI_FREEZE_TIME' not in made.stderr
"""

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

# a test whose call reads a file that nothing of the call names: an ignore
# file beside the test's folder, which only the repository's own config names
_ASIDE = (
    _HEAD
    + """
import subprocess


def test_aside(tmp_path):
    wiki = _wiki(tmp_path)
    ignore = tmp_path.parent / 'aside_ignore'
    ignore.write_text('alpha.md\\n', encoding='utf-8')
    for args in (['init', '-q'], ['config', 'core.excludesFile', str(ignore)]):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True)
    assert wiki.update()
"""
)

# a wiki in a repository: an update that writes, a read, and a check that
# finds nothing left to write
_REPO = (
    _HEAD
    + """
import subprocess


def test_repo(tmp_path):
    wiki = _wiki(tmp_path)
    subprocess.run(['git', '-C', str(tmp_path), 'init', '-q'], check=True)
    assert wiki.update()
    assert 'alpha page' in wiki.read('notes/alpha')
    assert wiki.update(check=True) == []
"""
)

# an update that writes the file its trees list first, in a repository that
# holds a search index: the folder and the page sort ahead of every other
# name, and the repository's own files and the caches ahead of the settings
_EARLY = (
    _HEAD
    + """
import subprocess


def test_early(tmp_path_factory):
    root = tmp_path_factory.mktemp('aaa')
    wiki = _wiki(root)
    subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)
    page = '---\\ndesc: About dash.\\n---\\n\\n# Dash\\n\\nThe dash page.\\n'
    (root / '-dash.md').write_text(page, encoding='utf-8')
    assert wiki.search('zqxjkv') == []
    assert wiki.update()
"""
)

# a help page through the helper, which the parser prints
_CLI_HELP = """
from .conftest import _wiki


def test_help(tmp_path):
    assert _wiki(tmp_path, 'lint', '--help').returncode == 0
"""

# commands of the console script that a test starts by itself, beside one
# through the helper and with none
_CLI_OWN = """
import subprocess

from .conftest import WIKI, _wiki


def test_own(tmp_path):
    assert _wiki(tmp_path, '--version').returncode == 0
    assert subprocess.run([WIKI, '--version'], capture_output=True).returncode == 0
"""
_CLI_ALONE = """
import subprocess

from .conftest import WIKI


def test_alone():
    assert subprocess.run([WIKI, '--version'], capture_output=True).returncode == 0
"""

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

# format calls with no JSON form: an argument that is bytes, and a function
# of another module that returns a compiled pattern
_BYTES = """
import pytest

from wiki.core import format


def test_bytes():
    with pytest.raises(Exception):
        format.parse_page(b'no text')
"""
_PATTERN = """
import re


def test_pattern():
    assert re.compile('a').pattern == 'a'
"""

# the same call on a folder that holds a hook file
_HOOKED = _UNDECLARED.replace(
    "    assert 'alpha page'",
    "    (tmp_path / '.wiki').mkdir()\n"
    "    (tmp_path / '.wiki' / 'wiki.py').write_text('', encoding='utf-8')\n"
    "    assert 'alpha page'",
)

# a fixture that two tests share, which makes a call no command line states
_SHARED = (
    _HEAD
    + """

@pytest.fixture(scope='module')
def shared(tmp_path_factory):
    wiki = _wiki(tmp_path_factory.mktemp('shared'))
    with pytest.raises(ValueError):
        wiki.read('notes/alpha', start=0, stop=1, on='paragraphs')
    return wiki


def test_first(shared):
    assert 'alpha page' in shared.read('notes/alpha')


def test_second(shared):
    assert shared.update()
"""
)

# calls on input outside the supported set: a timestamp format with a
# fraction of a second, and a pattern with an atomic group
_FRACTION = (
    _HEAD
    + """
import json


def test_fraction(tmp_path):
    _wiki(tmp_path)
    path = tmp_path / '.wiki' / 'settings.json'
    settings = json.loads(path.read_text(encoding='utf-8'))
    settings['timestamp'] = {'format': '%Y-%m-%dT%H:%M:%S.%fZ'}
    path.write_text(json.dumps(settings), encoding='utf-8')
    assert Wiki(tmp_path).update()


def test_atomic(tmp_path):
    wiki = _wiki(tmp_path)
    assert wiki.update()
    assert wiki.match('(?>alpha)')
"""
)

# tests with each home, and with none: a core test that makes a call and one
# that makes none, and CLI tests that run a command, that read the tree a
# shared fixture made with one, that run none, and that the host skips
_HOMES = (
    _HEAD
    + """

def test_call(tmp_path):
    assert _wiki(tmp_path).update()


def test_no_call(tmp_path):
    assert _wiki(tmp_path)
"""
)
_CLI_HOMES = """
import pytest

from .conftest import _wiki


@pytest.fixture(scope='module')
def made(tmp_path_factory):
    base = tmp_path_factory.mktemp('made')
    assert _wiki(base, 'init', 'root', '--path', str(base / 'w')).returncode == 0
    return base / 'w'


def test_command(tmp_path):
    assert _wiki(tmp_path, '--version').returncode == 0


def test_tree(made):
    assert (made / '_index.md').is_file()


def test_tree_again(made):
    assert (made / '.wiki').is_dir()


def test_python():
    assert True


def test_elsewhere():
    pytest.skip('needs another host')
"""

# a side that runs the command it is given and adds a byte to its stdout
_LOUD = """
import subprocess, sys
done = subprocess.run(sys.argv[1:], capture_output=True)
sys.stdout.buffer.write(done.stdout + b'!')
sys.stderr.buffer.write(done.stderr)
sys.exit(done.returncode)
"""

# a side that runs the command it is given and marks its stdout when it is
# told to run on one thread
_MARKED = """
import os, subprocess, sys
done = subprocess.run(sys.argv[1:], capture_output=True)
mark = b'1' if os.environ.get('WIKI_THREADS') == '1' else b''
sys.stdout.buffer.write(done.stdout + mark)
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
    argnames=('core', 'cli', 'reason'),
    argvalues=[
        (_UNIT, '', "no option for the unit 'paragraphs'"),
        (_WIDER, '', 'no option states the argument fast'),
        (_STAMP, '', 'a stamp no instant states'),
        (_WHOLE, '', 'the whole temporary folder'),
        (_OUTSIDE, '', "the call's wiki lies outside the temporary folder"),
        ('', _AWAY, 'working directory lies outside the temporary folder'),
    ],
    ids=['unit', 'parameter', 'stamp', 'whole', 'outside', 'away'],
)
def test_call_no_command_line_states_is_a_finding(
    tmp_path: pathlib.Path,
    engine: side.Side,
    core: str,
    cli: str,
    reason: str,
) -> None:
    """A call the recorder cannot write as a command is reported, never skipped.

    ``read`` has no option for an unknown unit, and no option carries a
    parameter the command line does not know. ``WIKI_FREEZE_TIME`` cannot
    make the engine stamp a text that is no instant. A wiki that is the
    temporary folder itself has no tree of its own to copy, and a wiki or
    a working directory outside that folder is a path no copy holds. A
    replay of some other command, or on some other tree, would compare
    some other scenario, so each call is a finding under its test.
    """
    recording = _record(tmp_path, engine, core=core, cli=cli)
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


def test_command_is_held_to_what_the_call_left_and_printed(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """Each part of what a call did is held by itself.

    Three recorded calls are changed after the fact, each in one thing.
    An ``update`` replayed under another clock writes the same files
    with other stamps, which only the tree tells. A ``read`` whose test
    is said to have written a page, with the bytes it had, wrote a path
    the command does not write. And a helper command whose test is said
    to have read another stdout does not print what the test read.
    """
    recording = _record(tmp_path, engine, core=_COMMANDS, cli=_CLI)
    core = [call for call in recording.calls if call.source == 'core']
    # the second update of the test, which wrote the stamps, under another clock
    wrote = [call for call in core if call.command == 'update'][1]
    env = {**wrote.env, 'WIKI_FREEZE_TIME': '2001-02-03T04:05:06Z'}
    found = _problems(recording, engine, dataclasses.replace(wrote, env=env))
    assert found
    assert all(problem.startswith('tree: ') for problem in found)
    # a read, with a page its test wrote again as it was
    read = next(call for call in core if call.command == 'read')
    (page,) = read.after.folder.glob('*/notes/alpha.md')
    os.utime(page, ns=(1, 1))
    relpath = page.relative_to(read.after.folder).as_posix()
    assert _problems(recording, engine, read) == [f'written: {relpath}']
    # a helper command, with another stdout than its test read
    shown = next(call for call in recording.calls if call.source == 'cli')
    outcome = {**shown.outcome, 'stdout': 'another text'}
    changed = dataclasses.replace(shown, outcome=outcome)
    assert _problems(recording, engine, changed) == ['stdout']


def test_command_is_held_to_what_the_call_returned_raised_and_noted(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """Each row of the fidelity table is held by itself.

    One recorded call of each command is said to have returned something
    else, of the kind that asks for the same exit code: a longer list, one
    more hit, another text. The command then prints another result than
    the call returned, and nothing else is wrong. A call that is said to
    have raised where the command passes, to have raised another error,
    or to have ended with something that is no error, is told apart the
    same way, and so is a ``lint`` call said to have emitted a notice its
    command does not print.
    """
    recording = _record(tmp_path, engine, core=_COMMANDS)
    calls = recording.calls
    by_command: dict[str, record.Call] = {}
    for call in calls:
        by_command.setdefault(call.command, call)
    # the first call of each command, and the update that wrote; each returned
    # a list, a text or a count that is not empty
    wrote = [call for call in calls if call.command == 'update'][1]
    results = [
        (by_command['update'], [*by_command['update'].outcome['returned'], 'never.md']),
        (wrote, [*wrote.outcome['returned'], 'never.md']),
        (by_command['lint'], [*by_command['lint'].outcome['returned'], 'Never.']),
        (by_command['new'], 'never/_index.md'),
        (by_command['read'], 'Never.'),
        (by_command['map'], 'Never.'),
        (by_command['search'], by_command['search'].outcome['returned'] + 1),
        (by_command['match'], by_command['match'].outcome['returned'] + 1),
    ]
    for call, returned in results:
        assert call.outcome['returned']
        changed = dataclasses.replace(call, outcome={'returned': returned})
        assert _problems(recording, engine, changed) == [
            'the command prints another result than the call returned'
        ]
    # a read that passed, said to have raised, and one that raised another error
    read, missing = [call for call in calls if call.command == 'read'][::2]
    outcomes = [
        (read, 'ValueError', True, 'the call raised ValueError, the command exited 0'),
        (
            read,
            'KeyboardInterrupt',
            False,
            'the call ended with KeyboardInterrupt, which no command reports',
        ),
        (
            missing,
            'ValueError',
            True,
            'the command reports another error than ValueError',
        ),
    ]
    for call, raised, error, problem in outcomes:
        outcome = {'raised': raised, 'text': 'Never.', 'error': error}
        changed = dataclasses.replace(call, outcome=outcome)
        assert _problems(recording, engine, changed) == [problem]
    # a lint, said to have emitted a notice its command does not print
    noted = dataclasses.replace(by_command['lint'], notices=('Never.',))
    assert _problems(recording, engine, noted) == [
        'the command prints other notices than the call emitted'
    ]


def test_command_under_a_running_clock_is_not_replayed(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A command its test leaves the clock running for runs as the test started it.

    The test sets ``WIKI_FREEZE_TIME`` to a blank value, which an engine
    reads as unset. The recorder puts no clock of its own in its place:
    the command prints no notice, and the test reads what it wrote. A
    replay would write the stamps of its own moment, so the call has no
    command line, and is a finding unless the test is routed.
    """
    recording = _record(tmp_path, engine, cli=_CLI_RUNNING)
    assert recording.tests.report['counts']['outcomes'] == {'passed': 1}
    (call,) = recording.calls
    assert call.outcome['notice'] is False
    sides = selfcheck._seeded(engine)
    result = replay.run(recording, sides)
    assert [(finding.kind, finding.detail) for finding in result.findings] == [
        ('unformed', 'the test leaves the clock running')
    ]
    assert replay.run(recording, sides, routed=[call.test]).findings == ()


def test_record_of_a_path_outside_the_recording_is_not_played(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A record that names a temporary folder outside the recording is refused.

    The replay removes that folder and places the call's trees there, so
    a record is trusted with no path but one below the recording's own
    folder. The call is a finding, and nothing is made at the path.
    """
    recording = _record(tmp_path, engine, core=_COMMANDS)
    elsewhere = tmp_path / 'elsewhere'
    call = dataclasses.replace(recording.calls[0], base=elsewhere)
    result = replay.run(dataclasses.replace(recording, calls=(call,)), [engine])
    assert [(finding.kind, finding.detail) for finding in result.findings] == [
        ('unformed', "the call's trees lie outside the recording")
    ]
    assert not elsewhere.exists()


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
    them out of the verdict, on a call the entry is about, and the
    report keeps them. The test's folder holds a hook file, so its call
    is one the entry of hooked wikis is about, and no call of the entry
    of the skill install. A row whose entry is about no call of its test
    is not needed.
    """
    recording = _record(tmp_path, engine, core=_HOOKED)
    (call,) = recording.calls
    loud = tmp_path / 'loud.py'
    loud.write_text(_LOUD, encoding='utf-8')
    other = side.Side(name='loud', argv=(sys.executable, f'{loud}', *engine.argv))
    sides = [engine, other]
    expected = (compare.Difference(call.test, 'loud', 'main-0 read', 'stdout'),)
    for explained in ({}, {call.test: 'D10'}):
        for routed in ([], [call.test]):
            result = replay.run(
                recording,
                sides,
                routed=routed,
                explained=explained,
                second=True,
            )
            assert result.differences == expected
    result = replay.run(
        recording,
        sides,
        routed=[call.test],
        explained={call.test: 'D6'},
        second=True,
    )
    assert (result.findings, result.differences) == ((), ())
    (row,) = result.report['calls']
    assert [found['aspect'] for found in row['differences']] == ['stdout']
    # between two builds of v1 the call shows no difference, and is still
    # one the entry is about
    seeded = selfcheck._seeded(engine)
    for entry, needed in (('D6', True), ('D10', False)):
        result = replay.run(
            recording,
            seeded,
            routed=[call.test],
            explained={call.test: entry},
        )
        (row,) = result.report['calls']
        assert row['about'] is needed
    recording = _record(tmp_path / 'held', engine, core=_COMMANDS)
    test = recording.calls[0].test
    result = replay.run(recording, seeded, routed=[test], explained={test: 'D10'})
    assert [finding.kind for finding in replay.unneeded([test], result.report)] == [
        'unneeded'
    ]


def test_call_of_a_shared_fixture_belongs_to_no_test(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A call a shared fixture makes takes no test's routing.

    The fixture is set up inside the first test that asks for it, and
    which test that is turns on how the tests are spread over the
    processes. Its call is recorded under the fixture's name, and each
    test that asks for the fixture lists it. So a row for the first test
    does not cover the fixture's call, which no command line states.
    """
    recording = _record(tmp_path, engine, core=_SHARED, jobs=0)
    fixture = 'tests/test_core/test_calls.py::shared'
    assert [call.shared for call in recording.calls] == [fixture, '', '']
    first, second = (call.test for call in recording.calls[1:])
    assert recording.shared == {first: [fixture], second: [fixture]}
    result = replay.run(recording, selfcheck._seeded(engine), routed=[first])
    assert [(finding.kind, finding.subject) for finding in result.findings] == [
        ('unformed', first)
    ]
    rows = result.report['calls']
    assert [(row['shared'], row['routed']) for row in rows] == [
        (fixture, False),
        ('', True),
        ('', False),
    ]
    # the row is judged by the test's own call, which replays as its command
    assert [finding.kind for finding in replay.unneeded([first], result.report)] == [
        'unneeded'
    ]


def test_call_on_input_outside_the_supported_set_is_a_finding(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A call that v1 runs on input a second engine refuses is reported.

    One test's wiki stamps with a fraction of a second, and the other
    matches a pattern with an atomic group: both are outside the
    supported set, and v1 runs the calls. Each such call is a finding,
    unless the test's row rests on the registry entry for refused input.
    A call on input inside the set is none.
    """
    recording = _record(tmp_path, engine, core=_FRACTION, jobs=0)
    seeded = selfcheck._seeded(engine)
    result = replay.run(recording, seeded)
    fraction, atomic = sorted({call.test for call in recording.calls}, reverse=True)
    assert [
        (finding.kind, finding.subject, finding.detail) for finding in result.findings
    ] == [
        ('unsupported', atomic, 'pattern: atomic group'),
        ('unsupported', fraction, 'timestamp.format: %f'),
    ]
    listed = [(row['test'], row['command']) for row in result.report['outside']]
    assert listed == [(fraction, 'update'), (atomic, 'match')]
    explained = {fraction: 'D3', atomic: 'D3'}
    result = replay.run(recording, seeded, routed=explained, explained=explained)
    assert result.findings == ()
    assert replay.unneeded(explained, result.report) == []


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


def test_entry_a_test_wrote_is_among_the_trees_of_its_calls(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A file a test writes beside its folder is there for the replayed command.

    The test keeps an ignore file outside its own folder, and only the
    config of its repository names it. The engine notes the page that
    file makes git ignore. No argument or variable of the call names the
    file, so it is one of the call's trees because the test wrote it.
    Without it the command leaves the same tree and prints no such note,
    and the replay is of another scenario: the notices tell.
    """
    recording = _record(tmp_path, engine, core=_ASIDE)
    assert recording.tests.report['counts']['outcomes'] == {'passed': 1}
    (call,) = recording.calls
    assert 'aside_ignore' in call.trees
    assert any('aside_ignore' in notice for notice in call.notices)
    result = replay.run(recording, selfcheck._seeded(engine))
    assert (result.findings, result.differences) == ((), ())
    # the same call on trees that lack the file
    names = tuple(name for name in call.before.names if name != 'aside_ignore')
    before = dataclasses.replace(call.before, names=names)
    after = dataclasses.replace(call.after, names=names)
    lacking = dataclasses.replace(call, before=before, after=after)
    recording = dataclasses.replace(recording, calls=(lacking,))
    result = replay.run(recording, selfcheck._seeded(engine))
    assert [(finding.kind, finding.detail) for finding in result.findings] == [
        ('unfaithful', 'the command prints other notices than the call emitted')
    ]


def test_second_engine_takes_the_calls_of_the_ported_commands(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """With a second engine, the calls of the ported commands are replayed.

    Each such call runs on v1, on the engine and on the engine with one
    thread, and every other call is left to a later run. The third run is
    told its thread count: an engine that marks what it prints on one
    thread differs there on every call, and nowhere else.
    """
    recording = _record(tmp_path, engine, core=_COMMANDS)
    other = oracle.side(oracle.load(), 'campaign')
    sides = [engine, other, replay.threaded(other)]
    result = replay.run(recording, sides, commands=['read', 'map'], second=True)
    assert (result.findings, result.differences) == ((), ())
    assert list(result.report['counts']['commands']) == ['core map', 'core read']
    names = [engine.name, other.name, f'{other.name} on one thread']
    assert all(list(call['runs']) == names for call in result.report['calls'])
    # an engine that marks its stdout when it is told to run on one thread
    script = tmp_path / 'marked.py'
    script.write_text(_MARKED, encoding='utf-8')
    marked = side.Side(name='marked', argv=(sys.executable, f'{script}', *engine.argv))
    sides = [engine, marked, replay.threaded(marked)]
    result = replay.run(recording, sides, commands=['read', 'map'], second=True)
    assert len(result.differences) == len(result.report['calls'])
    assert {(found.run, found.aspect) for found in result.differences} == {
        ('marked on one thread', 'stdout')
    }


def test_side_that_leaves_another_tree_is_a_difference(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """What a side writes, and where it may write, is compared path by path.

    The second side does one more thing behind one command of a wiki in
    a repository. A file it adds, a mode it changes and a file it writes
    again with the same bytes each show on that path. Behind a command
    that may write nothing outside the caches, the added file also
    breaks the read-only rule: a ``read`` and an ``update --check`` are
    such commands. Below the repository's own folder a side is not
    compared, so the same file there is no difference.
    """
    recording = _record(tmp_path, engine, core=_REPO)
    assert recording.tests.report['counts']['outcomes'] == {'passed': 1}
    update, read, check = recording.calls
    root = pathlib.Path(f'{update.root}').relative_to(update.base).as_posix()

    def differences(
        call: record.Call, kind: str, relpath: str
    ) -> list[tuple[str, str]]:
        """Return what shows when a side does ``kind`` to ``relpath`` behind ``call``."""
        other = replay._doctored(engine, kind, call, call.base / relpath)
        only = dataclasses.replace(recording, calls=(call,))
        result = replay.run(only, [engine, other])
        assert result.findings == ()
        assert {found.run for found in result.differences} <= {other.name}
        return [(found.aspect, found.detail) for found in result.differences]

    stray = f'{root}/stray.txt'
    settings = f'{root}/.wiki/settings.json'
    assert differences(update, 'extra', stray) == [('tree', stray), ('touched', stray)]
    assert differences(update, 'mode', settings) == [('tree', settings)]
    assert differences(update, 'rewrite', settings) == [('touched', settings)]
    assert differences(update, 'extra', f'{root}/.git/stray.txt') == []
    for call in (read, check):
        assert differences(call, 'extra', stray) == [
            ('tree', stray),
            ('touched', stray),
            ('wrote', stray),
        ]


def test_registry_entry_excuses_nothing_between_two_builds_of_v1(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A call the registry excuses is a finding unless a second engine made it.

    The second side prints a help page in another layout, which the
    registry excuses. Against a second engine the call is listed and
    nothing more. Between two builds of v1 no entry has a difference to
    explain, so the excused call is a finding.
    """
    recording = _record(tmp_path, engine, cli=_CLI_HELP)
    (call,) = recording.calls
    edits = [[list(call.argv), {'stdout': [r'(?s)\A.*\Z', 'Another layout.\n']}]]
    argv = (
        sys.executable,
        '-P',
        f'{standin.__file__}',
        json.dumps(edits),
        f'{len(engine.argv)}',
        *engine.argv,
    )
    other = side.Side(name='stand-in', argv=argv)
    result = replay.run(recording, [engine, other], second=True)
    assert (result.findings, result.differences) == ((), ())
    assert [entry.entry for entry in result.excused] == ['D2']
    result = replay.run(recording, [engine, other])
    assert [(finding.kind, finding.subject) for finding in result.findings] == [
        ('registered', call.test)
    ]


def test_doctored_replay_is_reported(tmp_path: pathlib.Path, engine: side.Side) -> None:
    """A replay of the wrong command, or on a side that differs, does not pass.

    The ``argv`` control drops ``--check`` from the one ``update`` call
    that found pending changes, so the command writes them: the replay
    must call it unfaithful. The ``side`` control changes one thing
    behind one command on one side, five times: a byte of stdout, one
    more file, and a byte, the mode and the inode of a file the command
    leaves alone. Each must show as the differences of that change. The
    file is one the tree rules compare and the command leaves alone,
    even where the trees list a page it writes, a file of the repository
    and a cache ahead of it: a change to one of those shows as nothing,
    or by another rule. A recording with no call to doctor proves
    nothing, and says so.
    """
    recording = _record(tmp_path, engine, core=_COMMANDS)
    counts = {'argv': 1, 'side': 5}
    for name in replay.CONTROLS:
        assert replay.control(recording, engine, name) == (
            [],
            {'doctored': counts[name], 'reported': counts[name]},
        )
    recording = _record(tmp_path / 'early', engine, core=_EARLY)
    wrote = recording.calls[-1].outcome['returned']
    assert any(name.endswith('-dash.md') for name in wrote)
    assert replay.control(recording, engine, 'side') == (
        [],
        {'doctored': 5, 'reported': 5},
    )
    recording = _record(tmp_path / 'other', engine, core=_UNIT)
    for name in replay.CONTROLS:
        findings, counts = replay.control(recording, engine, name)
        assert [finding.kind for finding in findings] == ['control']
        assert counts == {'doctored': 0, 'reported': 0}


def test_command_the_helper_does_not_start_is_counted(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A command that reaches a side with no record is counted under its test.

    A test that starts the console script by itself leaves no record of
    that command, and the report says how many each test ran that way.
    A recording that holds no command of the helper at all, though the
    tests ran some, is a finding: the replay would pass with every CLI
    test left out.
    """
    recording = _record(tmp_path, engine, cli=_CLI_OWN)
    result = replay.run(recording, selfcheck._seeded(engine))
    (call,) = recording.calls
    assert result.findings == ()
    assert result.report['unrecorded'] == {call.test: 1}
    assert replay.unrecorded(recording) == []
    recording = _record(tmp_path / 'other', engine, cli=_CLI_ALONE)
    result = replay.run(recording, selfcheck._seeded(engine))
    assert recording.calls == ()
    assert sum(result.report['unrecorded'].values()) == 1
    assert replay.unrecorded(recording) == [replay.Finding('unrecorded', '1 command')]


def test_helper_the_recorder_cannot_watch_is_an_error(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """A helper whose commands the recorder cannot watch is no recording.

    The recorder stands in for the module the helper starts commands
    with. A helper that binds the function by name leaves it nothing to
    stand in for, and every command of the CLI tests would run with no
    record. The run raises instead of returning a recording that reads
    as a pass.
    """
    with pytest.raises(RuntimeError, match='did not run the tests'):
        _record(tmp_path, engine, cli=_CLI, helper=_UNWATCHED)


def test_format_calls_are_recorded_and_made_again(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """Each distinct format call is recorded once, and gives its outcome again.

    A call made twice is one record with a count of two. A tuple and a set
    come back as what they were when the call is made again in a fresh
    interpreter, and a call that raises raises again. A
    call whose outcome turns on a value the test patched gives another
    outcome there, which is a finding. A routing row for a format test is
    needed only while one of its calls does that.
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
    # a row for a test whose format calls all give their outcome again is
    # not needed
    (test,) = {test for entry in recording.formats for test in entry['tests']}
    played = replay.run(recording, selfcheck._seeded(engine)).report
    unneeded = replay.unneeded([test], played, report['differing'], [test])
    assert [(finding.kind, finding.subject) for finding in unneeded] == [
        ('unneeded', test)
    ]
    recording = _record(tmp_path / 'other', engine, formats=_HIDDEN)
    work = tmp_path / 'made again'
    findings, report = replay.formats(recording, source, engine, work=work)
    assert [(finding.kind, finding.subject) for finding in findings] == [
        ('format', 'match_link_row')
    ]
    (test,) = report['differing']
    played = replay.run(recording, selfcheck._seeded(engine)).report
    assert replay.unneeded([test], played, report['differing'], [test]) == []


def test_format_call_with_no_json_form_is_a_finding(
    tmp_path: pathlib.Path,
    engine: side.Side,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A format call that JSON cannot state is reported, never passed over.

    An argument that is bytes has no JSON form, and neither has a
    compiled pattern that a function returns. Such a call cannot be made
    again and compared, so it is a finding unless every test that made it
    is routed. No format function returns such a value, so the second
    case records the functions of ``re`` in their place.
    """
    source = suite.source(oracle.load())
    recording = _record(tmp_path, engine, formats=_BYTES)
    (entry,) = recording.formats
    expected = [replay.Finding('format', 'parse_page', 'No JSON form for a bytes.')]
    work = tmp_path / 'made'
    assert replay.formats(recording, source, engine, work=work)[0] == expected
    work = tmp_path / 'made again'
    routed = entry['tests']
    assert replay.formats(recording, source, engine, work=work, routed=routed)[0] == []
    monkeypatch.setattr(record, '_FORMAT', 're')
    monkeypatch.setattr(replay, '_FORMAT', 're')
    recording = _record(tmp_path / 'other', engine, formats=_PATTERN)
    work = tmp_path / 'made once more'
    findings, _ = replay.formats(recording, source, engine, work=work)
    assert findings == [
        replay.Finding('format', 'compile', 'No JSON form for a Pattern.')
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
    ((test, met),) = recording.kinds.items()
    assert test.endswith('::test_every_command')
    assert met['notices']['link_add'] >= 1
    assert sum(met['issues'].values()) >= 1


def test_kind_list_is_held_to_the_engine_and_to_the_replay(
    tmp_path: pathlib.Path,
    engine: side.Side,
) -> None:
    """Every kind the engine has needs a home that shows it.

    The recording lists the kinds of the engine itself, and each call the
    kinds it met. A kind is at home in a test when a call of the test
    replays as its command and the command shows the kind: a ``lint``
    document names it, and any other command prints the notice. A home
    that the routing table sends to a test of the second engine holds a
    kind the test met. A list that lacks a kind of the engine, names a
    kind it does not have, or names a home that does not show its kind
    is a finding. The perturbation answers for its own kinds.
    """
    recording = _record(tmp_path, engine, core=_COMMANDS)
    assert {'link_add', 'write_skip'} <= set(recording.known['notices'])
    assert {'broken_link', 'requires_update'} <= set(recording.known['issues'])
    lint = next(call for call in recording.calls if call.command == 'lint')
    update = next(call for call in recording.calls if call.kinds['notices'])
    assert (update.command, len(update.kinds['notices'])) == (
        'update',
        len(update.notices),
    )
    test = lint.test
    issue = lint.kinds['issues'][0]
    seeded = selfcheck._seeded(engine)
    result = replay.run(recording, seeded)
    shown = [row['kinds'] for row in result.report['calls']]
    assert any('link_add' in row['notices'] for row in shown)
    assert any(issue in row['issues'] for row in shown)
    # a command line that prints no notice, and a lint that ends before its
    # document, show none of the kinds their calls met
    silent = dataclasses.replace(update, argv=('--version',))
    ended = dataclasses.replace(lint, argv=(*lint.argv, '--nope'))
    only = dataclasses.replace(recording, calls=(silent, ended))
    rows = replay.run(only, seeded).report['calls']
    assert [row['kinds'] for row in rows] == [{'notices': [], 'issues': []}] * 2
    # every kind at home in the perturbation, but two the test shows
    listed = {
        family: dict.fromkeys(recording.known[family], kinds.PERTURBATION)
        for family in kinds.FAMILIES
    }
    listed['notices']['link_add'] = test
    listed['issues'][issue] = test
    findings, counts = kinds.check(listed, recording, result.report, {})
    assert findings == []
    assert (counts['notices']['replay'], counts['issues']['replay']) == (1, 1)
    # a home the routing table sends to a test of the second engine
    for to, held in (('rust', True), ('dropped', False)):
        route = Route(tests=(test,), to=to, target='', cite='', reason='None.')
        findings, counts = kinds.check(listed, recording, result.report, {test: route})
        assert (not findings) is held
        assert counts['notices']['rust'] == int(held)
    # a list that lacks a kind, names an unknown one, or a home that shows nothing
    del listed['issues'][issue]
    listed['issues']['no_such_kind'] = test
    listed['notices']['write_skip'] = test
    findings, _ = kinds.check(listed, recording, result.report, {})
    assert [(finding.subject, finding.detail) for finding in findings] == [
        ('notice write_skip', f'no replayed call of {test} shows it'),
        (f'issue {issue}', 'the list gives it no home'),
        ('issue no_such_kind', 'v1 has no such kind'),
    ]


def test_issue_kind_the_source_does_not_name_ends_the_recording(
    tmp_path: pathlib.Path,
    engine: side.Side,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recorder lists the issue kinds only where the source spells each one.

    The kinds are read from the keyword of every place that makes an
    issue. A place that names no kind there would leave a kind off the
    list with nothing to show for it, so the recording ends. The test
    names an error class in the issue class's place, which the engine's
    source makes with no kind.
    """
    monkeypatch.setattr(record, '_ISSUE', 'wiki.core.wiki:ValueError')
    with pytest.raises(RuntimeError, match='no string literal'):
        _record(tmp_path, engine, core=_COMMANDS)


def test_every_test_has_one_home(tmp_path: pathlib.Path, engine: side.Side) -> None:
    """Each test v1 has is held by one part of the harness, or is a finding.

    The CLI suite holds a test that runs a command, and one that reads
    the tree a shared fixture made with a command, whichever test set the
    fixture up. The replay holds a core test with a call that replays as
    its command, and the format calls hold a format test. A test that
    calls nothing recorded is homeless until a row routes it, and one the
    host skips shows its home on another host. The table is held to the
    tests in turn: a row names a test v1 has, a dropped
    row cites a test the CLI suite holds, and a row that waits for the
    replay does not outlast the port of its command.
    """
    recording = _record(
        tmp_path,
        engine,
        core=_HOMES,
        cli=_CLI_HOMES,
        formats=_FORMATS,
        jobs=0,
    )
    python = suite.source(oracle.load()).python
    source = suite.Source(python=python, checkout=tmp_path / 'checkout')
    dispatcher = dispatch.build(tmp_path / 'bin', reference=engine)
    # the harness's own tests on v1's branch are no tests of v1
    own = tmp_path / 'checkout' / 'tests' / 'test_parity'
    own.mkdir()
    (own / '__init__.py').write_text('', encoding='utf-8')
    (own / 'test_own.py').write_text('def test_own():\n    pass\n', encoding='utf-8')
    collected = homes.collect(source, dispatcher, work=tmp_path / 'collect')
    cli, core = 'tests/test_cli/test_commands.py', 'tests/test_core/test_calls.py'
    assert collected == (
        f'{cli}::test_command',
        f'{cli}::test_tree',
        f'{cli}::test_tree_again',
        f'{cli}::test_python',
        f'{cli}::test_elsewhere',
        f'{core}::test_call',
        f'{core}::test_no_call',
        'tests/test_core/test_format.py::test_formats',
    )
    seeded = selfcheck._seeded(engine)
    result = replay.run(recording, seeded)
    work = tmp_path / 'made'
    _, formats = replay.formats(recording, source, engine, work=work)
    findings, report = homes.check(
        collected,
        recording,
        result.report,
        formats,
        {},
        folder=source.folder,
    )
    homeless = [f'{cli}::test_python', f'{core}::test_no_call']
    assert findings == [replay.Finding('homeless', test) for test in homeless]
    assert report['homes'] == {'routed': 0, 'suite': 3, 'replay': 1, 'format': 1}
    assert report['skipped'] == [f'{cli}::test_elsewhere']
    # a row for each, and three rows the tests do not bear out
    rows = [
        Route((f'{cli}::test_python',), 'dropped', '', f'{cli}::test_command', 'x'),
        Route((f'{core}::test_no_call',), 'replay', 'init', '', 'x'),
    ]
    routes = {test: route for route in rows for test in route.tests}
    findings, report = homes.check(
        collected,
        recording,
        result.report,
        formats,
        routes,
        folder=source.folder,
    )
    assert findings == []
    assert report['homes'] == {'routed': 2, 'suite': 3, 'replay': 1, 'format': 1}
    assert report['routed'] == {'dropped': 1, 'replay': 1}
    rows = [
        Route((f'{core}::test_gone',), 'rust', '', '', 'x'),
        Route((f'{cli}::test_python',), 'dropped', '', f'{core}::test_call', 'x'),
        Route((f'{core}::test_no_call',), 'replay', 'init', '', 'x'),
    ]
    routes = {test: route for route in rows for test in route.tests}
    findings, _ = homes.check(
        collected,
        recording,
        result.report,
        formats,
        routes,
        folder=source.folder,
        ported=['init'],
    )
    assert [(finding.kind, finding.subject) for finding in findings] == [
        ('absent', f'{core}::test_gone'),
        ('waiting', f'{core}::test_no_call'),
        ('cite', f'{core}::test_call'),
    ]


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
    helper: str = _HELPER,
    jobs: Optional[int] = None,
    only: Sequence[str] = (),
) -> record.Recording:
    """Record a checkout of tests shaped like v1's.

    The checkout holds ``core`` as a core test module, ``formats`` as the
    format test module and ``cli`` as a CLI test module beside ``helper``
    as the helper module. As in v1, each package imports what it holds.
    """
    checkout = tmp_path / 'checkout'
    modules = {
        'tests/test_core/test_calls.py': core,
        'tests/test_core/test_format.py': formats,
        'tests/test_cli/conftest.py': helper,
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


def _problems(
    recording: record.Recording,
    reference: side.Side,
    call: record.Call,
) -> list[str]:
    """Return what the replay of the one call ``call`` finds unfaithful."""
    only = dataclasses.replace(recording, calls=(call,))
    result = replay.run(only, selfcheck._seeded(reference))
    assert result.differences == ()
    return [part for finding in result.findings for part in finding.detail.split('; ')]
