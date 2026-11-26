"""Test the ``parity.__main__`` module.

Drives the harness command line as a gate does: sides come from the oracle
file or a TOML file, corpora from a TOML file, and the verdict is the exit
code plus the result files.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import shutil
import sys
from collections.abc import Sequence
from typing import Any, Optional

import pytest

from parity import (
    __main__ as cli,
    fixtures,
    kinds,
    matrix,
    oracle,
    perturb,
    ported,
    registry,
    routing,
    runner,
    selfcheck,
    side,
    suite,
    terminal,
)

from .conftest import NOW, REPO_ROOT, WORDED, _needs_bytes, _needs_git

__all__ = [
    'test_v1_sides_run_as_the_oracle_names_them',
    'test_matrix_command_strips_only_a_declared_notice',
    'test_keep_leaves_the_snapshot_and_every_tree',
    'test_matrix_command_passes_an_excused_command_only_when_told',
    'test_run_that_would_compare_nothing_is_refused',
    'test_harness_error_exits_2',
    'test_oracle_check_tells_a_failed_check_from_a_run_that_cannot_start',
    'test_failed_selfcheck_keeps_its_streams_and_trees',
    'test_registry_command_proves_the_classifiers',
    'test_suite_command_runs_v1s_tests_and_the_terminal_table',
    'test_failed_suite_keeps_its_output_and_trees',
    'test_suite_whose_terminal_table_cannot_run_keeps_the_tests_result',
    'test_suite_that_would_prove_nothing_is_refused',
    'test_replay_command_records_and_replays_v1s_tests',
    'test_failed_replay_keeps_its_output_and_trees',
    'test_row_for_a_test_the_replay_holds_fails_the_replay',
    'test_whole_replay_accounts_for_v1s_tests_and_kinds',
    'test_replay_that_would_prove_nothing_is_refused',
    'test_fixtures_command_runs_the_families_on_v1_and_on_an_engine',
    'test_failed_family_keeps_its_streams_and_its_checkouts',
    'test_difference_at_a_path_that_is_not_utf8_is_listed',
    'test_fixtures_run_that_would_compare_nothing_is_refused',
    'test_perturb_command_runs_a_loop_and_a_corpus',
    'test_fresh_seeds_are_printed_before_a_tree_runs',
    'test_failed_perturbation_keeps_its_result_and_its_streams',
    'test_whole_perturbation_run_holds_the_kind_list',
    'test_tree_that_ends_in_an_error_leaves_the_other_results',
    'test_perturbation_run_that_would_compare_nothing_is_refused',
]

# a side that runs the command it is given and prints everything but the
# WIKI_FREEZE_TIME notice, as a wrapper that fixes the clock by itself does
_RELAY = f"""
import subprocess, sys
result = subprocess.run(sys.argv[1:], capture_output=True)
notice = {side.NOTICE!r}
stderr = result.stderr
if stderr.startswith(notice):
    stderr = stderr[len(notice):]
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(stderr)
sys.exit(result.returncode)
"""

# a side that runs the command it is given, unless the command is `read`
_UNREADABLE = """
import os, sys
if 'read' in sys.argv[1:]:
    sys.exit(7)
os.execv(sys.argv[1], sys.argv[1:])
"""

# an engine that runs the command it is given, and on every ``read`` makes
# the folder its first argument names
_SQUATTER = """
import os, sys
if 'read' in sys.argv[2:]:
    os.makedirs(sys.argv[1], exist_ok=True)
os.execv(sys.argv[2], sys.argv[2:])
"""

# a side that runs the command it is given and adds a line to what it prints
_WORDY = """
import subprocess, sys
result = subprocess.run(sys.argv[1:], capture_output=True)
sys.stdout.buffer.write(result.stdout + b'more\\n')
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
"""

# a side that runs the command it is given and leaves a file in the wiki
# whose name is no valid UTF-8
_STRAY = """
import os, subprocess, sys
result = subprocess.run(sys.argv[1:], capture_output=True)
if '--path' in sys.argv:
    root = os.fsencode(sys.argv[sys.argv.index('--path') + 1])
    with open(os.path.join(root, b'stray-\\xe9.txt'), 'wb'):
        pass
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
"""

# two of v1's CLI tests: they call `map`, `init` and `read`
_TESTS = 'tests/test_cli/test_regressions.py'
# one small file of v1's core tests, two of whose tests the routing table names
_CORE_TESTS = 'tests/test_core/test_read.py'

# a checkout of tests shaped like v1's, small enough to replay whole: a core
# test with a pending update, which the `argv` control doctors, and an update
# that writes, which the `side` control doctors; a core test that calls
# nothing the recorder records; and the helper module of the CLI tests
_CHECKOUT = {
    'pytest.ini': '[pytest]\n',
    'tests/__init__.py': '',
    'tests/test_core/__init__.py': '',
    'tests/test_core/test_calls.py': """
from wiki.core.wiki import Wiki


def _wiki(path):
    wiki = Wiki(path)
    wiki.init(name='root')
    (path / 'notes').mkdir()
    page = '---\\ndesc: About alpha.\\n---\\n\\n# Alpha\\n\\nThe alpha page.\\n'
    (path / 'notes' / 'alpha.md').write_text(page, encoding='utf-8')
    return wiki


def test_update(tmp_path):
    wiki = _wiki(tmp_path)
    assert wiki.update(check=True)
    assert wiki.update()


def test_python(tmp_path):
    assert _wiki(tmp_path)
""",
    'tests/test_core/test_format.py': '',
    'tests/test_cli/__init__.py': '',
    'tests/test_cli/conftest.py': """
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
""",
    'tests/test_cli/test_commands.py': '',
}
# a describe command: what it prints comes from the side's own environment
_DESCRIBE = "import os; print(os.environ['EXAMPLE'], os.environ['WIKI_FREEZE_TIME'])"


@_needs_git
def test_v1_sides_run_as_the_oracle_names_them(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """The oracle and alt sides need no sides file, and say what they are.

    Both run the oracle commit, each under its own interpreter and pins,
    and the example wiki reads the same to both. The result file records
    the identity each side proved on every command and the facts of its
    interpreter.
    """
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'matrix',
        *('--a', 'oracle', '--b', 'alt'),
        *('--corpora', f'{corpora}', '--stage', 'write'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == 0
    named = oracle.load()
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    for record in document['sides']:
        build = named.builds[record['name']]
        assert record['version'] == named.version
        assert record['identity']['build'] == named.commit
        facts = json.loads(record['describe'])
        assert facts['python'] == build.python
        assert facts['libyaml'] == build.libyaml


@_needs_git
@pytest.mark.parametrize(
    argnames=('declared', 'code'),
    argvalues=[(True, 0), (False, 1)],
    ids=['declared', 'undeclared'],
)
def test_matrix_command_strips_only_a_declared_notice(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
    declared: bool,
    code: int,
) -> None:
    """A ``WIKI_FREEZE_TIME`` notice is dropped only where a side declares it.

    The engine prints the notice and a wrapper side prints none, so the
    two are equal once the engine's notice is stripped. With the notice
    undeclared the same pair differs on stderr, and the command exits 1
    and keeps the streams that differ.
    """
    relay = tmp_path / 'relay.py'
    relay.write_text(_RELAY, encoding='utf-8')
    prefix = json.dumps(engine.argv)
    notice = json.dumps(declared)
    describe = json.dumps([sys.executable, '-c', _DESCRIBE])
    relayed = json.dumps([sys.executable, f'{relay}', *engine.argv])
    sides = tmp_path / 'sides.toml'
    sides.write_text(
        f'[engine]\n'
        f'argv = {prefix}\n'
        f'notice = {notice}\n'
        f'env = {{ EXAMPLE = "engine" }}\n'
        f'describe = {describe}\n'
        f'[relay]\n'
        f'argv = {relayed}\n'
        f'notice = false\n',
        encoding='utf-8',
    )
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', 'engine', '--b', 'relay'),
        *('--corpora', f'{corpora}', '--stage', 'write'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == code
    # the result file names both sides, what they report as their version,
    # and what the one side that has a describe command says it is made of
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    version = oracle.load().version
    assert [item['version'] for item in document['sides']] == [version] * 2
    described = [item['describe'] for item in document['sides']]
    assert described == [f'engine {NOW}', None]
    (stage,) = document['stages']
    aspects = {difference['aspect'] for difference in stage['differences']}
    kept = out / 'example' / 'write' / '000 engine.err'
    if declared:
        assert aspects == set()
        assert not kept.exists()
    else:
        assert aspects == {'stderr'}
        assert kept.read_bytes().startswith(side.NOTICE)
    # the snapshot and every clone are gone
    assert not (work / 'example').exists()


@_needs_git
def test_keep_leaves_the_snapshot_and_every_tree(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
) -> None:
    """A matrix run with ``--keep`` leaves its work folder in place.

    Two sides of one engine agree on the ``write`` stage, so the command
    exits 0, and the snapshot and the tree each side ended the stage with
    are still there to look at.
    """
    sides = _sides_file(tmp_path, engine)
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', 'engine', '--b', 'other'),
        *('--corpora', f'{corpora}', '--stage', 'write', '--keep'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == 0
    assert (work / 'example' / 'snapshot' / corpus.root).is_dir()
    for name in ('engine', 'other'):
        tree = work / 'example' / 'kept' / 'write' / name / 'tree'
        assert (tree / corpus.root / '_index.md').is_file()


@_needs_git
@pytest.mark.parametrize(
    argnames=('options', 'code'),
    argvalues=[([], 1), (['--registered'], 0)],
    ids=['two builds of v1', 'second engine'],
)
def test_matrix_command_passes_an_excused_command_only_when_told(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
    capsys: pytest.CaptureFixture[str],
    options: list[str],
    code: int,
) -> None:
    """A difference the registry lists passes only against a second engine.

    The second side words the text of an invalid pattern its own way, which
    the registry excuses in a second engine. Between two builds of v1
    nothing is registered, so the command lists the excused command and
    exits 1, and exits 0 only where ``--registered`` says what the second
    side is.
    """
    sides = tmp_path / 'sides.toml'
    worded = [sys.executable, '-c', WORDED, *engine.argv]
    sides.write_text(f'[worded]\nargv = {json.dumps(worded)}\n', encoding='utf-8')
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', 'oracle', '--b', 'worded'),
        *('--corpora', f'{corpora}', '--stage', 'query'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main([*argv, *options]) == code
    lines = capsys.readouterr().out.splitlines()
    assert 'example: 0 differences' in lines
    assert '  registered: query: worded: match (: D1' in lines


@_needs_git
@pytest.mark.parametrize(
    argnames=('broken', 'message'),
    argvalues=[
        # the command line
        ('stage', 'invalid choice'),
        ('instant', 'must be an RFC 3339 date-time'),
        ('year', 'must be an RFC 3339 date-time'),
        ('corpus', 'declares no corpus named'),
        ('unknown', 'No side is named'),
        ('twice', 'both name'),
        # the sides and corpora files
        ('corpora', 'declares no corpus.'),
        ('argv', 'argv must be'),
        ('taken', 'names as v1 sides'),
        # the registry file
        ('registry', 'has the status'),
        # the checkout and the work folder
        ('worktree', '.git is not a folder'),
        ('work', 'the harness makes it new'),
        # the sides themselves
        ('side', 'does not start'),
        ('missing', 'does not start'),
        ('silent', 'does not start'),
        ('identity', 'is not the engine it names'),
        ('describe', 'describe exited'),
        ('undescribed', 'describe does not start'),
    ],
    ids=[
        'stage',
        'instant',
        'year',
        'corpus',
        'unknown',
        'twice',
        'corpora',
        'argv',
        'taken',
        'registry',
        'worktree',
        'work',
        'side',
        'missing',
        'silent',
        'identity',
        'describe',
        'undescribed',
    ],
)
def test_run_that_would_compare_nothing_is_refused(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    engine: side.Side,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    broken: str,
    message: str,
) -> None:
    """A run with nothing to compare is no pass.

    A mistyped stage, a ``--now`` that is no instant or lies outside the
    years an engine takes, a corpus or a side no file declares, one side
    named twice, an empty corpora file, an ``argv`` that is no list, a
    checkout that is a linked worktree, a work folder that exists, a side
    that fails, is missing or prints no version, and a ``describe``
    command that fails or is missing: each would compare nothing, a side
    with itself, or two sides that fail alike, and report no difference.
    A sides file that takes the name of a v1 side would pass something
    else off as the oracle, and so would a v1 side that runs another
    build than the one it is expected to be. A registry file that breaks
    its rules could excuse what nobody approved. The command exits 2 with
    a message instead, before it snapshots anything or writes a result
    file, and leaves a work folder that was there as it stood.
    """
    # a working side beside the one a case breaks
    nowhere = tmp_path / 'nowhere'
    argvs = {
        'side': [sys.executable, '-c', 'import nonexistent_module'],
        'missing': [f'{nowhere}'],
        'silent': [sys.executable, '-c', 'pass'],
        'argv': 'wiki',
    }
    describes = {
        'describe': [sys.executable, '-c', 'raise SystemExit(1)'],
        'undescribed': [f'{nowhere}'],
    }
    prefix = argvs.get(broken)
    describe = describes.get(broken)
    sides = _sides_file(tmp_path, engine, argv=prefix, describe=describe)
    # the first side: a declared one, or a v1 side the case breaks
    first = 'engine'
    if broken == 'taken':
        taken = json.dumps(engine.argv)
        sides.write_text(f'[oracle]\nargv = {taken}\n', encoding='utf-8')
        first = 'oracle'
    elif broken == 'identity':
        # the oracle side is handed out as a build its checkout does not hold
        expected = {**engine.identity, 'build': 'another'}
        other = dataclasses.replace(engine, identity=expected)
        monkeypatch.setattr(oracle, 'side', lambda *_: other)
        first = 'oracle'
    # the registry file holds an entry under a status it does not have
    if broken == 'registry':
        text = registry.PATH.read_text(encoding='utf-8')
        changed = tmp_path / 'differences.toml'
        changed.write_text(text.replace('"approved"', '"agreed"'), encoding='utf-8')
        monkeypatch.setattr(registry, 'PATH', changed)
    # the corpora file, the checkout and the work folder
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    if broken == 'corpora':
        corpora.write_text('', encoding='utf-8')
    elif broken == 'worktree':
        checkout = tmp_path / 'worktree'
        checkout.mkdir()
        (checkout / '.git').write_text('gitdir: /elsewhere\n', encoding='utf-8')
        linked = dataclasses.replace(corpus, checkout=checkout)
        corpora = _corpora_file(tmp_path, linked)
    elif broken == 'work':
        (work / 'example').mkdir(parents=True)
        (work / 'example' / 'notes.txt').write_text('kept\n', encoding='utf-8')
    # the second side, the stage, the corpus and the instant the case names
    seconds = {'unknown': 'absent', 'twice': 'engine'}
    second = seconds.get(broken, 'other')
    stage = 'wirte' if broken == 'stage' else 'write'
    picked = ['--corpus', 'absent'] if broken == 'corpus' else []
    instants = {'instant': 'tomorrow', 'year': '1000-06-01T00:00:00Z'}
    now = instants.get(broken, NOW)
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', first, '--b', second),
        *('--corpora', f'{corpora}', *picked, '--stage', stage),
        *('--work', f'{work}', '--out', f'{out}', '--now', now),
    ]
    held = sorted(tmp_path.rglob('*'))
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    # nothing was snapshotted or written: every folder holds what it held
    assert sorted(tmp_path.rglob('*')) == held
    assert not out.exists()


def test_harness_error_exits_2(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An error of the harness itself is exit 2, not the exit 1 of a difference.

    A sides file that does not exist is an error no message is written
    for. The command prints the traceback and exits 2, so a gate that
    reads exit 1 as two sides that differ never takes a broken run for
    one.
    """
    sides = tmp_path / 'absent.toml'
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', 'engine', '--b', 'other'),
        *('--corpora', f'{sides}'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == 2
    assert 'FileNotFoundError' in capsys.readouterr().err
    assert not work.exists()
    assert not out.exists()


@pytest.mark.parametrize(
    argnames=('broken', 'code', 'message'),
    argvalues=[
        # the file names a commit that is not the merge base with v1
        pytest.param('base', 1, 'base: FAILED', marks=_needs_git),
        # the host has no git, so the command cannot read the repository
        ('git', 2, 'FileNotFoundError'),
    ],
    ids=['base', 'git'],
)
def test_oracle_check_tells_a_failed_check_from_a_run_that_cannot_start(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    broken: str,
    code: int,
    message: str,
) -> None:
    """A failed ``oracle check`` is exit 1, and one that cannot run is exit 2.

    The gate of every pull request reads the exit code alone. An oracle
    file that names another commit than the merge base with v1 fails
    ``base``: the command prints the verdict and exits 1. On a host with
    no git the command cannot read the repository: it prints the traceback
    and exits 2, so a gate that reads exit 1 as an oracle file that fell
    behind v1 never takes a broken run for one.
    """
    if broken == 'base':
        named = dataclasses.replace(oracle.load(), commit='0' * 40)
        monkeypatch.setattr(oracle, 'load', lambda: named)
    else:
        monkeypatch.setenv('PATH', f'{tmp_path}')
    # the command as the gate runs it
    assert cli.main(['oracle', 'check', '--skip', 'level']) == code
    printed = capsys.readouterr()
    assert message in (printed.out if broken == 'base' else printed.err)


@_needs_git
def test_failed_selfcheck_keeps_its_streams_and_trees(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """A self-check that fails leaves what is needed to see why.

    The side prints its hash seed, so its two seeded runs differ on every
    command and the clean check fails. The command exits 1, the differing
    streams of the failed check are saved beside the result file, and the
    work folder keeps the trees its runs ended with.
    """
    # a side that prints its hash seed
    code = "import os; print(os.environ.get('PYTHONHASHSEED'))"
    seeded = json.dumps([sys.executable, '-c', code])
    sides = tmp_path / 'sides.toml'
    sides.write_text(f'[seeded]\nargv = {seeded}\nnotice = false\n', encoding='utf-8')
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'selfcheck',
        *('--sides', f'{sides}', '--side', 'seeded', '--corpora', f'{corpora}'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    # the check fails and says so in the result file
    assert cli.main(argv) == 1
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    verdicts = {check['name']: check['passed'] for check in document['checks']}
    assert verdicts['clean'] is False
    # the differing stream and the tree it ended with are kept, here for the
    # second of the side's two seeded runs
    seed = selfcheck._SEEDS[-1]
    run = f'seeded seed {seed}'
    stream = out / 'example' / 'clean' / 'pristine' / f'000 {run}.out'
    assert stream.read_bytes() == f'{seed}\n'.encode()
    tree = work / 'example' / 'failed' / 'clean' / 'pristine' / run / 'tree'
    assert (tree / corpus.root / '_index.md').is_file()


@_needs_git
def test_registry_command_proves_the_classifiers(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The registry check passes with v1, and says what a release waits for.

    Every fixture of a classifier is excused under its entry or reported,
    as its tree's check expects, and the result file holds both lists with
    the status of every entry. An entry that names no test yet is printed
    and fails the command only under ``--release``.
    """
    corpora = _corpora_file(tmp_path, corpus)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'registry',
        *('--side', 'oracle', '--corpora', f'{corpora}'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == 0
    waits = [
        line for line in capsys.readouterr().out.splitlines() if 'names no' in line
    ]
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    entries = registry.load()
    assert document['registry'] == {id: entry.status for id, entry in entries.items()}
    assert waits == [f'registry: {line}' for line in registry.unready(entries)]
    checks = {check['name']: check for check in document['checks']}
    assert all(check['passed'] for check in checks.values())
    excused = {item['entry'] for check in checks.values() for item in check['excused']}
    assert excused == {'D1', 'D2', 'D3'}
    reported = {item['case'] for check in checks.values() for item in check['found']}
    assert reported == {
        'changed prefix',
        'changed parameter message',
        'supported construct',
        'unnamed field',
    }
    # the snapshot and every clone are gone
    assert not (work / 'example').exists()
    assert cli.main([*argv, '--release']) == (1 if waits else 0)


@_needs_git
@pytest.mark.parametrize(
    argnames=('second', 'commands'),
    argvalues=[(None, []), ('alt', ['read'])],
    ids=['v1 alone', 'second engine'],
)
def test_suite_command_runs_v1s_tests_and_the_terminal_table(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    second: Optional[str],
    commands: list[str],
) -> None:
    """The CLI suite passes with v1 alone, and with v1's other build as engine.

    Two of v1's own tests run from the oracle checkout, then the terminal
    table. With no engine every call is v1's. With ``read`` ported, the
    calls of ``read`` go to the alt side, which is the same commit:
    the tests pass on both, and the command prints how many calls each
    side took. The result file records each test with its calls, and the
    work folder is gone. The folders are named from where the command is
    started, though pytest runs in the oracle checkout, and pytest leaves
    no cache in that checkout.
    """
    listed = tmp_path / 'ported.toml'
    listed.write_text(f'commands = {json.dumps(commands)}\n', encoding='utf-8')
    monkeypatch.setattr(ported, 'PATH', listed)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = ['suite', '--test', _TESTS, '--jobs', '0']
    argv += ['--work', work.name, '--out', out.name]
    if second is not None:
        argv += ['--engine', second]
    assert cli.main(argv) == 0
    lines = capsys.readouterr().out.splitlines()
    assert 'suite: 2 tests: 2 passed; 0 left out' in lines
    assert 'suite: 0 tests called no side' in lines
    assert 'terminal: 0 differences' in lines
    document = json.loads((out / 'suite.json').read_text(encoding='utf-8'))
    named = oracle.load()
    names = ['oracle'] if second is None else ['oracle', second]
    assert [record['name'] for record in document['sides']] == names
    assert {record['identity']['build'] for record in document['sides']} == {
        named.commit
    }
    assert document['ported'] == commands
    assert [test['outcome'] for test in document['tests']] == ['passed'] * 2
    counts = document['counts']
    assert set(counts['sides']) == set(names)
    assert all(count['calls'] for count in counts['sides'].values())
    assert set(counts['commands']) == set(commands)
    sent = {name for case in document['terminal']['cases'] for name in case['sent']}
    assert sent == {names[-1]}
    assert f'suite: {names[-1]}: ' in '\n'.join(lines)
    assert b'2 passed' in (out / 'suite' / 'pytest.txt').read_bytes()
    assert not (work / 'suite').exists()
    assert not (named.checkout / '.pytest_cache').exists()


@_needs_git
def test_failed_suite_keeps_its_output_and_trees(
    tmp_path: pathlib.Path,
    engine: side.Side,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A suite that fails leaves what is needed to see why.

    The second engine answers ``--version`` and fails every ``read``. The
    test that reads a page fails on its own assertion and every case of
    the terminal table differs, so the command exits 1. It prints the
    failed test and each difference, and keeps pytest's output, both
    outputs of the differing cases, and the work folder, which holds the
    wikis the tests made.
    """
    relay = tmp_path / 'relay.py'
    relay.write_text(_UNREADABLE, encoding='utf-8')
    prefix = json.dumps([sys.executable, f'{relay}', *engine.argv])
    sides = tmp_path / 'sides.toml'
    sides.write_text(f'[unreadable]\nargv = {prefix}\n', encoding='utf-8')
    listed = tmp_path / 'ported.toml'
    listed.write_text('commands = ["read"]\n', encoding='utf-8')
    monkeypatch.setattr(ported, 'PATH', listed)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = ['suite', '--test', _TESTS, '--jobs', '0']
    argv += ['--sides', f'{sides}', '--engine', 'unreadable']
    argv += ['--work', f'{work}', '--out', f'{out}']
    assert cli.main(argv) == 1
    lines = capsys.readouterr().out.splitlines()
    assert f'  failed: {_TESTS}::test_broken_pipe_is_quiet' in lines
    assert 'terminal: 9 differences' in lines
    assert '  terminal: dispatcher: error: unexpected: exit 7' in lines
    assert b'1 failed, 1 passed' in (out / 'suite' / 'pytest.txt').read_bytes()
    stream = out / 'suite' / f'error {terminal.DISPATCHED}.out'
    assert stream.read_bytes() == b''
    assert (work / 'suite' / 'terminal' / 'tree' / 'wiki' / '_index.md').is_file()
    trees = work / 'suite' / 'tests' / 'tmp'
    assert list(trees.rglob(runner.WIKI_SETTINGS))


@_needs_git
def test_suite_whose_terminal_table_cannot_run_keeps_the_tests_result(
    tmp_path: pathlib.Path,
    engine: side.Side,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error of the terminal table does not take the tests' result with it.

    The second engine passes every test, and with its first ``read`` it
    makes a folder where the terminal table makes its own, so the table
    ends in an error of the harness. The command exits 2 with the
    traceback. The result file and pytest's output are written by then:
    they hold the tests' outcomes and no table.
    """
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    relay = tmp_path / 'relay.py'
    relay.write_text(_SQUATTER, encoding='utf-8')
    taken = work / 'suite' / 'terminal' / 'home'
    prefix = json.dumps([sys.executable, f'{relay}', f'{taken}', *engine.argv])
    sides = tmp_path / 'sides.toml'
    sides.write_text(f'[squatter]\nargv = {prefix}\n', encoding='utf-8')
    listed = tmp_path / 'ported.toml'
    listed.write_text('commands = ["read"]\n', encoding='utf-8')
    monkeypatch.setattr(ported, 'PATH', listed)
    argv = ['suite', '--test', _TESTS, '--jobs', '0']
    argv += ['--sides', f'{sides}', '--engine', 'squatter']
    argv += ['--work', f'{work}', '--out', f'{out}']
    assert cli.main(argv) == 2
    assert 'FileExistsError' in capsys.readouterr().err
    document = json.loads((out / 'suite.json').read_text(encoding='utf-8'))
    assert [test['outcome'] for test in document['tests']] == ['passed'] * 2
    assert 'terminal' not in document
    assert b'2 passed' in (out / 'suite' / 'pytest.txt').read_bytes()


@_needs_git
@pytest.mark.parametrize(
    argnames=('broken', 'message'),
    argvalues=[
        # the engine
        ('reference', 'the side v1 runs as'),
        ('unknown', 'No side is named'),
        ('unported', 'lists no command'),
        # the list of ported commands
        ('list', 'unknown key'),
        # the work folder
        ('work', 'the harness makes it new'),
        ('repository', 'lies inside the repository'),
        ('wiki', 'which holds .wiki/settings.json'),
    ],
    ids=['reference', 'unknown', 'unported', 'list', 'work', 'repository', 'wiki'],
)
def test_suite_that_would_prove_nothing_is_refused(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    broken: str,
    message: str,
) -> None:
    """A suite run that would prove nothing, or write where it must not, is no pass.

    An engine that is v1 itself or that no file declares, an engine no
    ported command would reach, and a list of ported commands that breaks
    its rules would each run v1 against v1 under another name. A work
    folder inside a repository would let a test's ``init`` register the
    merge driver there, and one below a wiki would fail every test that
    makes a wiki. The command exits 2 with a message instead, before it
    runs a test or writes a result file.
    """
    listed = tmp_path / 'ported.toml'
    texts = {
        'unported': 'commands = []\n',
        'list': 'commands = ["read"]\nrest = "v1"\n',
    }
    text = texts.get(broken, 'commands = ["read"]\n')
    listed.write_text(text, encoding='utf-8')
    monkeypatch.setattr(ported, 'PATH', listed)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    if broken == 'work':
        (work / 'suite').mkdir(parents=True)
    elif broken == 'repository':
        (tmp_path / 'repository' / '.git').mkdir(parents=True)
        work = tmp_path / 'repository' / 'work'
    elif broken == 'wiki':
        (tmp_path / 'outer' / '.wiki').mkdir(parents=True)
        settings = tmp_path / 'outer' / '.wiki' / 'settings.json'
        settings.write_text('{}', encoding='utf-8')
        work = tmp_path / 'outer' / 'work'
    engines = {'reference': 'oracle', 'unknown': 'absent'}
    second = engines.get(broken, 'alt')
    argv = ['suite', '--engine', second, '--work', f'{work}', '--out', f'{out}']
    held = sorted(tmp_path.rglob('*'))
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    assert sorted(tmp_path.rglob('*')) == held
    assert not out.exists()


@_needs_git
@pytest.mark.parametrize(
    argnames=('second', 'commands'),
    argvalues=[(None, []), ('alt', ['read'])],
    ids=['v1 alone', 'second engine'],
)
def test_replay_command_records_and_replays_v1s_tests(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    second: Optional[str],
    commands: list[str],
) -> None:
    """The replay passes with v1 alone, and with v1's other build as engine.

    One of v1's core test files runs from the oracle checkout under the
    recorder. With no engine every call is replayed on v1 under two hash
    seeds, and the two tests of the file that the routing table names
    keep the run green though no command repeats their calls. With
    ``read`` ported, the ``read`` calls alone are replayed, on v1, on the
    alt side and on that side with one thread. The result file
    records each call with its runs, and the work folder is gone.
    """
    listed = tmp_path / 'ported.toml'
    listed.write_text(f'commands = {json.dumps(commands)}\n', encoding='utf-8')
    monkeypatch.setattr(ported, 'PATH', listed)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = ['replay', '--test', _CORE_TESTS, '--jobs', '0']
    argv += ['--work', work.name, '--out', out.name]
    if second is not None:
        argv += ['--engine', second]
    assert cli.main(argv) == 0
    lines = capsys.readouterr().out.splitlines()
    assert 'replay: 0 differences' in lines
    document = json.loads((out / 'replay.json').read_text(encoding='utf-8'))
    assert document['findings'] == []
    counts = document['counts']
    assert counts['tests_routed'] == 2
    assert counts['unformed'] + counts['unfaithful'] > 0
    runs = ['oracle seed 1', 'oracle seed 2']
    if second is not None:
        runs = ['oracle', second, f'{second} on one thread']
        assert list(counts['commands']) == ['core read']
    assert document['runs'] == runs
    replayed = [call for call in document['calls'] if call['runs']]
    assert replayed
    assert all(list(call['runs']) == runs for call in replayed)
    assert ('formats' in document) == (second is None)
    assert b' passed' in (out / 'replay' / 'pytest.txt').read_bytes()
    assert not (work / 'replay').exists()


@_needs_git
def test_failed_replay_keeps_its_output_and_trees(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replay that fails leaves what is needed to see why.

    With an empty routing table, the calls of the file that no command
    repeats are findings, so the command exits 1. It prints each one
    under its test, and keeps pytest's output, both streams of every run
    of an unfaithful call, and the work folder, which holds the copies of
    the trees.
    """
    table = tmp_path / 'routing.toml'
    table.write_text('', encoding='utf-8')
    monkeypatch.setattr(routing, 'PATH', table)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = ['replay', '--test', _CORE_TESTS, '--jobs', '0']
    argv += ['--work', f'{work}', '--out', f'{out}']
    assert cli.main(argv) == 1
    lines = capsys.readouterr().out.splitlines()
    test = f'{_CORE_TESTS}::test_read_slice_units'
    assert f"  unformed: {test}: read has no option for the unit 'paragraphs'" in lines
    assert f'  unfaithful: {test}: exit code 2, the call asks for 0' in lines
    document = json.loads((out / 'replay.json').read_text(encoding='utf-8'))
    assert {finding['kind'] for finding in document['findings']} == {
        'unformed',
        'unfaithful',
    }
    assert list((out / 'replay').glob('* oracle seed 2.err'))
    assert list((work / 'replay' / 'record' / 'store').rglob('before'))


@_needs_git
@pytest.mark.parametrize(
    argnames='row',
    argvalues=['to = "rust"', 'to = "dropped"\ncite = "D6"'],
    ids=['plain', 'registry'],
)
def test_row_for_a_test_the_replay_holds_fails_the_replay(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    row: str,
) -> None:
    """A routing row for a test the replay holds is reported, not kept.

    The table routes the tests of the file that no command repeats, and
    one more whose calls all replay as their commands. That row would
    take a test out of the replay for nothing, so the command exits 1
    and names it. A row that rests on a registry entry is held the same
    way: the test's wiki has no hook file, so none of its calls is one
    the entry is about.
    """
    committed = routing.PATH.read_text(encoding='utf-8')
    held = f'{_CORE_TESTS}::test_read_line_slicing'
    extra = f'[[route]]\ntests = ["{held}"]\n{row}\nreason = "None."\n'
    table = tmp_path / 'routing.toml'
    table.write_text(f'{committed}\n{extra}', encoding='utf-8')
    monkeypatch.setattr(routing, 'PATH', table)
    out = tmp_path / 'out'
    argv = ['replay', '--test', _CORE_TESTS, '--jobs', '0']
    argv += ['--work', f'{tmp_path / "work"}', '--out', f'{out}']
    assert cli.main(argv) == 1
    lines = capsys.readouterr().out.splitlines()
    assert f'  unneeded: {held}: every call of the test replays as its command' in lines


@_needs_git
def test_whole_replay_accounts_for_v1s_tests_and_kinds(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replay of every test of v1 alone ends with the account, and fails on it.

    The command runs with no ``--test`` on a small checkout in the place
    of v1's tests. The routing table is empty, so the test that calls
    nothing the recorder records has no home, and the kind list leaves
    out one kind of the engine. The command exits 1 and prints both
    findings, and no other: both controls find a call to doctor and are
    reported. The result file holds the findings beside the homes and
    the kind list.
    """
    checkout = tmp_path / 'checkout'
    for relpath, text in _CHECKOUT.items():
        path = checkout / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    python = suite.source(oracle.load()).python
    small = suite.Source(python=python, checkout=checkout)
    monkeypatch.setattr(suite, 'source', lambda named: small)
    table = tmp_path / 'routing.toml'
    table.write_text('', encoding='utf-8')
    monkeypatch.setattr(routing, 'PATH', table)
    # give every kind of the committed list but one the perturbation for a home
    committed = kinds.load()
    lines = []
    for family in kinds.FAMILIES:
        lines.append(f'[{family}]\n')
        lines.extend(
            f'{kind} = "{kinds.PERTURBATION}"\n'
            for kind in committed[family]
            if kind != 'link_add'
        )
    listed = tmp_path / 'kinds.toml'
    listed.write_text(''.join(lines), encoding='utf-8')
    monkeypatch.setattr(kinds, 'PATH', listed)
    out = tmp_path / 'out'
    argv = ['replay', '--jobs', '0']
    argv += ['--work', f'{tmp_path / "work"}', '--out', f'{out}']
    assert cli.main(argv) == 1
    printed = capsys.readouterr().out.splitlines()
    homeless = 'tests/test_core/test_calls.py::test_python'
    expected = [
        f'  homeless: {homeless}',
        '  kind: notice link_add: the list gives it no home',
    ]
    assert [line for line in printed if line.startswith('  ')] == expected
    document = json.loads((out / 'replay.json').read_text(encoding='utf-8'))
    assert [
        (finding['kind'], finding['subject']) for finding in document['findings']
    ] == [
        ('homeless', homeless),
        ('kind', 'notice link_add'),
    ]
    assert document['controls'] == {
        'argv': {'doctored': 1, 'reported': 1},
        'side': {'doctored': 5, 'reported': 5},
    }
    assert document['homes']['homeless'] == [homeless]
    assert document['homes']['homes'] == {
        'routed': 0,
        'suite': 0,
        'replay': 1,
        'format': 0,
    }
    assert (
        sum(document['kind_list']['notices'].values()) == len(committed['notices']) - 1
    )


@_needs_git
@pytest.mark.parametrize(
    argnames=('broken', 'message'),
    argvalues=[
        ('reference', "--engine names 'oracle'"),
        ('unported', 'lists no command'),
        ('table', 'gives no reason'),
        ('kinds', 'unknown key'),
        ('work', 'exists'),
    ],
)
def test_replay_that_would_prove_nothing_is_refused(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    broken: str,
    message: str,
) -> None:
    """A replay that would prove nothing is refused before it runs a test.

    An engine that is v1 itself and an engine no ported command would
    reach would replay v1 against v1 under another name, and a routing
    table that breaks its rules would take tests out of the replay with
    nothing behind them, as a kind list that breaks its rules would a
    kind. The command exits 2 with a message, and writes no result file.
    """
    listed = tmp_path / 'ported.toml'
    text = 'commands = []\n' if broken == 'unported' else 'commands = ["read"]\n'
    listed.write_text(text, encoding='utf-8')
    monkeypatch.setattr(ported, 'PATH', listed)
    table = tmp_path / 'routing.toml'
    row = '[[route]]\ntests = ["tests/a.py::test_b"]\nto = "rust"\n'
    table.write_text(row if broken == 'table' else '', encoding='utf-8')
    monkeypatch.setattr(routing, 'PATH', table)
    listed = tmp_path / 'kinds.toml'
    listed.write_text('events = 1\n' if broken == 'kinds' else '', encoding='utf-8')
    monkeypatch.setattr(kinds, 'PATH', listed)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    if broken == 'work':
        (work / 'replay').mkdir(parents=True)
    second = 'oracle' if broken == 'reference' else 'alt'
    argv = ['replay', '--engine', second, '--work', f'{work}', '--out', f'{out}']
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    assert not out.exists()


@_needs_git
@pytest.mark.parametrize(
    argnames=('second', 'families'),
    argvalues=[(None, ['control', 'nested']), ('alt', ['nested'])],
    ids=['v1 alone', 'second engine'],
)
def test_fixtures_command_runs_the_families_on_v1_and_on_an_engine(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    second: Optional[str],
    families: list[str],
) -> None:
    """The fixture families pass with v1 alone, and with v1's other build.

    The command writes the families it is told to and runs each: the
    matrix and then the family's own stages, or those alone for a family
    whose wiki ``update`` refuses. With no engine both sides are v1 under
    two hash seeds; with one, v1 is compared with it and with it on one
    thread. Each family gets a result file, and the work folder is gone.
    """
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = ['fixtures', '--work', f'{work}', '--out', f'{out}', '--now', NOW]
    for name in families:
        argv += ['--family', name]
    if second is not None:
        argv += ['--engine', second]
    assert cli.main(argv) == 0
    lines = capsys.readouterr().out.splitlines()
    sides = ['oracle']
    runs = ['oracle seed 1', 'oracle seed 2']
    if second is not None:
        sides = ['oracle', second]
        runs = ['oracle', second, f'{second} on one thread']
    stages = {}
    for name in families:
        assert f'{name}: 0 differences' in lines
        document = json.loads((out / f'{name}.json').read_text(encoding='utf-8'))
        assert [item['name'] for item in document['sides']] == sides
        stages[name] = [stage['name'] for stage in document['stages']]
        own = [stage for stage in document['stages'] if stage['name'] == 'fixture']
        (stage,) = own
        assert stage['runs'] == runs
        assert stage['differences'] == []
        assert all(len(case['runs']) == len(runs) for case in stage['cases'])
    assert stages['nested'] == ['fixture']
    if 'control' in stages:
        assert stages['control'][:5] == list(matrix.STAGES)
        assert stages['control'][5:] == ['fixture', 'fixture forced']
    assert not (work / 'fixtures').exists()


@_needs_git
def test_failed_family_keeps_its_streams_and_its_checkouts(
    tmp_path: pathlib.Path,
    engine: side.Side,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A family that shows a difference fails the run and leaves it to be read.

    An engine that prints one line more than v1 differs on every command.
    The command exits 1, prints each difference under its family, saves
    the streams of every run of a command that differs, and keeps the
    checkouts it wrote, so the family can be run again by hand.
    """
    relay = [sys.executable, '-c', _WORDY, *engine.argv]
    sides = _sides_file(tmp_path, engine, argv=relay)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'fixtures',
        '--family',
        'nested',
        '--sides',
        f'{sides}',
        '--engine',
        'engine',
    ]
    argv += ['--work', f'{work}', '--out', f'{out}', '--now', NOW]
    assert cli.main(argv) == 1
    lines = capsys.readouterr().out.splitlines()
    assert '  fixture: engine: map: stdout' in lines
    assert '  fixture: engine on one thread: map: stdout' in lines
    document = json.loads((out / 'nested.json').read_text(encoding='utf-8'))
    (stage,) = document['stages']
    assert {difference['aspect'] for difference in stage['differences']} == {'stdout'}
    saved = sorted(path.name for path in (out / 'nested' / 'fixture').iterdir())
    assert '000 engine.out' in saved
    assert '000 oracle.out' in saved
    checkout = work / 'fixtures' / 'trees' / 'nested'
    assert (checkout / 'wiki' / '.wiki' / 'settings.json').is_file()


@_needs_git
@_needs_bytes
def test_difference_at_a_path_that_is_not_utf8_is_listed(
    tmp_path: pathlib.Path,
    engine: side.Side,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A difference at a path no stream can print is listed, byte by escape.

    An engine that leaves a file whose name is no valid UTF-8 differs from
    v1 at that path. The run reports it as any difference, exit 1, with
    the byte spelled as an escape in the summary and in the result file.
    """
    relay = [sys.executable, '-c', _STRAY, *engine.argv]
    sides = _sides_file(tmp_path, engine, argv=relay)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = ['fixtures', '--family', 'nested', '--sides', f'{sides}']
    argv += ['--engine', 'engine']
    argv += ['--work', f'{work}', '--out', f'{out}', '--now', NOW]
    assert cli.main(argv) == 1
    lines = capsys.readouterr().out.splitlines()
    assert any(line.endswith('wiki/stray-\\udce9.txt') for line in lines)
    document = json.loads((out / 'nested.json').read_text(encoding='utf-8'))
    (stage,) = document['stages']
    details = {difference['detail'] for difference in stage['differences']}
    assert any(detail.endswith('wiki/stray-\udce9.txt') for detail in details)


@_needs_git
@pytest.mark.parametrize(
    argnames=('broken', 'message'),
    argvalues=[
        ('reference', "--engine names 'oracle'"),
        ('unknown', 'No side is named'),
        ('family', 'No fixture family is named'),
        ('twice', 'named twice'),
        ('entry', "a second engine refuses, by registry entry: {'refused-page': 'D4'}"),
        ('both', '--every runs every family'),
        ('every', 'needs a file system'),
        ('work', 'exists'),
        ('repository', 'inside the repository'),
    ],
)
def test_fixtures_run_that_would_compare_nothing_is_refused(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    broken: str,
    message: str,
) -> None:
    """A fixtures run that would compare nothing is refused before it writes.

    An engine that is v1 itself or that no file declares, a family the
    generator does not have or is given twice, a tree a second engine
    refuses, every family and some at once, every family on a file system
    that cannot hold one, and a work folder that exists or lies in a
    repository: the command exits 2 with a message, writes no family and
    no result file, and leaves a work folder that was there as it stood.
    """
    if broken == 'every' and not fixtures.lacking(tmp_path):
        pytest.skip('the file system holds every family')
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    if broken == 'work':
        (work / 'fixtures').mkdir(parents=True)
    elif broken == 'repository':
        (work / '.git').mkdir(parents=True)
    seconds = {'reference': 'oracle', 'unknown': 'absent'}
    families = {
        'family': ['nseted'],
        'twice': ['nested', 'nested'],
        'entry': ['nested', 'refused-page'],
        'every': [],
    }
    argv = ['fixtures', '--work', f'{work}', '--out', f'{out}']
    for family in families.get(broken, ['nested']):
        argv += ['--family', family]
    if broken in seconds:
        argv += ['--engine', seconds[broken]]
    if broken in ('both', 'every'):
        argv += ['--every']
    held = sorted(tmp_path.rglob('*'))
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    assert sorted(tmp_path.rglob('*')) == held
    assert not out.exists()


@_needs_git
@pytest.mark.parametrize(
    argnames=('mode', 'second'),
    argvalues=[('loop', None), ('corpus', 'alt')],
    ids=['loop', 'corpus'],
)
def test_perturb_command_runs_a_loop_and_a_corpus(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    second: Optional[str],
) -> None:
    """A perturbed tree passes with v1 alone, and with v1's other build.

    A loop runs each seed on each small tree it is told to, through the
    short stage and the stages of the two trees made from it, and gives
    the tree one result file with a row for each seed. A corpus runs once,
    under the seed the command names, through the matrix and the same two
    stages; its result file holds the seed, what the perturbation did and
    the kinds its trees showed. Either way the work folder is gone.
    """
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = ['perturb', mode, '--work', f'{work}', '--out', f'{out}', '--now', NOW]
    if mode == 'loop':
        argv += ['--family', 'hello', '--seed', '3', '--seed', '4', '--jobs', '2']
    else:
        argv += ['--corpora', f'{_corpora_file(tmp_path, corpus)}', '--seed', '3']
    if second is not None:
        argv += ['--engine', second]
    assert cli.main(argv) == 0
    lines = capsys.readouterr().out.splitlines()
    assert not (work / 'perturb').exists()
    if mode == 'loop':
        assert lines[:2] == [
            'hello seed 3: 0 differences',
            'hello seed 4: 0 differences',
        ]
        assert lines[-1].startswith('perturb: 2 seeds on 1 tree: 0 of 2 failed')
        document = json.loads((out / 'hello.json').read_text(encoding='utf-8'))
        assert [item['name'] for item in document['sides']] == ['oracle']
        rows = [
            {key: row[key] for key in ('seed', 'differences', 'findings', 'excused')}
            for row in document['seeds']
        ]
        assert rows == [
            {'seed': seed, 'differences': 0, 'findings': 0, 'excused': 0}
            for seed in (3, 4)
        ]
        assert sorted(path.name for path in out.iterdir()) == [
            'hello.json',
            'perturb.json',
        ]
        return
    assert 'example seed 3: 0 differences' in lines
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    assert [item['name'] for item in document['sides']] == ['oracle', second]
    assert document['seed'] == 3
    assert [stage['name'] for stage in document['stages']] == [
        *matrix.STAGES,
        'unindexed',
        'conflicted',
    ]
    (stage,) = [stage for stage in document['stages'] if stage['name'] == 'conflicted']
    assert stage['runs'] == ['oracle', second, f'{second} on one thread']
    assert stage['exits']['oracle'] == stage['exits'][second]
    assert stage['exits']['oracle']['2'] == 2
    assert document['findings'] == []
    assert list(document['kinds']) == [perturb.EDITED, 'unindexed', 'conflicted']
    applied = document['perturbation']['applied']
    assert [name for name, paths in applied.items() if not paths] == []


@_needs_git
def test_fresh_seeds_are_printed_before_a_tree_runs(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run that draws its seeds names them first, and records each.

    A fresh seed is known nowhere else until its tree has run, so a run
    that ended in an error could not be run again. The seeds a loop draws
    are those its rows and its result file hold.
    """
    out = tmp_path / 'out'
    argv = ['perturb', 'loop', '--family', 'hello', '--fresh', '2', '--jobs', '2']
    argv += ['--work', f'{tmp_path / "work"}', '--out', f'{out}', '--now', NOW]
    assert cli.main(argv) == 0
    first, *lines = capsys.readouterr().out.splitlines()
    label, _, drawn = first.rpartition(': ')
    assert label == 'perturb: fresh seeds'
    seeds = [int(seed) for seed in drawn.split()]
    assert len(set(seeds)) == 2
    assert lines[:2] == [f'hello seed {seed}: 0 differences' for seed in seeds]
    document = json.loads((out / 'hello.json').read_text(encoding='utf-8'))
    assert [row['seed'] for row in document['seeds']] == seeds
    document = json.loads((out / 'perturb.json').read_text(encoding='utf-8'))
    assert document['seeds'] == seeds


@_needs_git
def test_failed_perturbation_keeps_its_result_and_its_streams(
    tmp_path: pathlib.Path,
    engine: side.Side,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tree that differs under a seed fails the loop and leaves it to be read.

    An engine that prints one line more than v1 differs on every command.
    The command exits 1 and prints each difference under its tree and
    seed. The seed that failed gets a whole result file, with what the
    perturbation did, and the streams of every run of a command that
    differs; the tree's own file counts the differences in the seed's row.
    """
    relay = [sys.executable, '-c', _WORDY, *engine.argv]
    sides = _sides_file(tmp_path, engine, argv=relay)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = ['perturb', 'loop', '--family', 'hello', '--seed', '3']
    argv += ['--sides', f'{sides}', '--engine', 'engine']
    argv += ['--work', f'{work}', '--out', f'{out}', '--now', NOW]
    assert cli.main(argv) == 1
    lines = capsys.readouterr().out.splitlines()
    assert '  conflicted: engine: update: stdout' in lines
    assert '  unindexed: engine on one thread: map: stdout' in lines
    assert lines[-1].startswith('perturb: 1 seed on 1 tree: 1 of 1 failed')
    document = json.loads((out / 'hello.json').read_text(encoding='utf-8'))
    (row,) = document['seeds']
    assert row['seed'] == 3
    assert row['differences'] > 0
    whole = json.loads((out / 'hello' / '3.json').read_text(encoding='utf-8'))
    assert whole['seed'] == 3
    assert whole['perturbation']['applied']['dirty-edit']
    aspects = {
        difference['aspect']
        for stage in whole['stages']
        for difference in stage['differences']
    }
    assert aspects == {'stdout'}
    saved = sorted(path.name for path in (out / 'hello' / '3' / 'edited').iterdir())
    assert '000 engine.out' in saved
    assert '000 oracle.out' in saved


@_needs_git
def test_whole_perturbation_run_holds_the_kind_list(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run of every tree fails on a kind of the perturbation that none shows.

    Here the one small tree is the example wiki outside any repository, so
    no change names the kinds that need one, and no tree shows them. A
    run that names no tree holds the kind list to what its trees showed:
    it exits 1 and names each such kind.
    """

    def plain(
        folder: pathlib.Path, **kwargs: object
    ) -> tuple[list[runner.Corpus], list]:
        """Write the example wiki as a folder with no repository."""
        ignore = shutil.ignore_patterns('cache', '.obsidian')
        source = REPO_ROOT / 'examples' / 'hello'
        shutil.copytree(source, folder / 'plain' / 'hello', ignore=ignore)
        tree = runner.Corpus(label='plain', checkout=folder / 'plain', root='hello')
        return [tree], []

    monkeypatch.setattr(perturb, 'trees', plain)
    out = tmp_path / 'out'
    argv = ['perturb', 'loop', '--seed', '3']
    argv += ['--work', f'{tmp_path / "work"}', '--out', f'{out}', '--now', NOW]
    assert cli.main(argv) == 1
    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if line.startswith('  ')] == [
        '  kind: notice gitignore_skip: no tree of the run shows it',
        '  kind: notice restamp: no tree of the run shows it',
        '  kind: issue gitignored_link_target: no tree of the run shows it',
    ]
    assert lines[-1].startswith('perturb: 1 seed on 1 tree: 0 of 1 failed')
    document = json.loads((out / 'perturb.json').read_text(encoding='utf-8'))
    assert document['trees'] == ['plain']
    assert document['seeds'] == [3]
    assert [finding['subject'] for finding in document['findings']] == [
        'notice gitignore_skip',
        'notice restamp',
        'issue gitignored_link_target',
    ]


@_needs_git
def test_tree_that_ends_in_an_error_leaves_the_other_results(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tree that ends in an error under one seed costs the loop no other result.

    A copy or a tap that fails ends one tree under one seed, and a loop
    of many seeds runs for hours. The seed that ended in an error is
    printed and recorded with the error, every other seed has its row, and
    the command exits 2: the run is not a whole one.
    """
    run = perturb.run

    def flaky(tree: runner.Corpus, seed: int, **kwargs: Any) -> perturb.Trial:
        """Run the tree under the seed, unless the seed is the one that fails."""
        if seed == 4:
            raise RuntimeError('cp failed (exit 1)')
        return run(tree, seed, **kwargs)

    monkeypatch.setattr(perturb, 'run', flaky)
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = ['perturb', 'loop', '--family', 'hello', '--jobs', '1']
    argv += ['--seed', '4', '--seed', '3']
    argv += ['--work', f'{work}', '--out', f'{out}', '--now', NOW]
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[:2] == [
        'hello seed 4: error: RuntimeError: cp failed (exit 1)',
        'hello seed 3: 0 differences',
    ]
    assert lines[-1].startswith(
        'perturb: 2 seeds on 1 tree: 0 of 2 failed, 1 ended in an error'
    )
    assert 'RuntimeError: cp failed (exit 1)' in captured.err
    document = json.loads((out / 'hello.json').read_text(encoding='utf-8'))
    first, second = document['seeds']
    assert first == {'seed': 4, 'error': 'RuntimeError: cp failed (exit 1)'}
    assert (second['seed'], second['differences']) == (3, 0)
    document = json.loads((out / 'perturb.json').read_text(encoding='utf-8'))
    assert (document['failed'], document['errors']) == (0, 1)
    assert (work / 'perturb').is_dir()


@_needs_git
@pytest.mark.parametrize(
    argnames=('broken', 'message'),
    argvalues=[
        ('reference', "--engine names 'oracle'"),
        ('unknown', 'No side is named'),
        ('family', 'No tree of the loop is named'),
        ('both', '--every runs every tree'),
        ('fresh', '--fresh must be at least 1'),
        ('drawn', '--fresh draws the seeds'),
        ('corpus', 'no --corpora file declares one'),
        ('named', '--corpora names the trees of the loop'),
        ('label', 'declares no corpus named'),
        ('twice', '--corpus names a corpus twice'),
        ('rootless', 'is not a folder'),
        ('jobs', '--jobs must be at least 1'),
        ('kinds', 'no operator names it'),
        ('seeds', 'seeds must be a list'),
        ('work', 'exists'),
        ('repository', 'inside the repository'),
    ],
)
def test_perturbation_run_that_would_compare_nothing_is_refused(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    broken: str,
    message: str,
) -> None:
    """A perturbation run that would compare nothing is refused before it writes.

    An engine that is v1 itself or that no file declares, a tree the loop
    does not have, every tree and some at once, fresh seeds that are none
    or come with named ones, a corpus with no file to declare it or that
    its file does not declare, trees named twice over, a corpus named
    twice, a corpus whose wiki root is no folder behind one whose root is,
    no tree at a time, a kind list that disagrees with the operators, a
    seed list that breaks a rule, and a work folder that exists or lies in
    a repository: the command exits 2 with a message, writes no tree and no
    result file, and leaves a work folder that was there as it stood.
    """
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    if broken == 'work':
        (work / 'perturb').mkdir(parents=True)
    elif broken == 'repository':
        (work / '.git').mkdir(parents=True)
    elif broken == 'kinds':
        listed = tmp_path / 'kinds.toml'
        text = kinds.PATH.read_text(encoding='utf-8')
        home = f'write_skip = "{kinds.PERTURBATION}"'
        listed.write_text(text.replace('write_skip = ', f'{home} #'), encoding='utf-8')
        monkeypatch.setattr(kinds, 'PATH', listed)
    elif broken == 'seeds':
        listed = tmp_path / 'seeds.toml'
        listed.write_text('seeds = []\n', encoding='utf-8')
        monkeypatch.setattr(perturb, 'PATH', listed)
    corpora = _corpora_file(tmp_path, corpus)
    if broken == 'rootless':
        checkout = json.dumps(f'{corpus.checkout}')
        with open(corpora, 'a', encoding='utf-8') as handle:
            handle.write(f'[rootless]\ncheckout = {checkout}\nroot = "nowhere"\n')
    extra = {
        'reference': ['--family', 'hello', '--engine', 'oracle'],
        'unknown': ['--family', 'hello', '--engine', 'absent'],
        'family': ['--family', 'nested'],
        'both': ['--family', 'hello', '--every'],
        'fresh': ['--fresh', '0'],
        'drawn': ['--fresh', '2', '--seed', '3'],
        'corpus': ['--corpus', 'example'],
        'named': ['--corpora', f'{corpora}', '--family', 'hello'],
        'label': ['--corpora', f'{corpora}', '--corpus', 'absent'],
        'twice': ['--corpora', f'{corpora}', '--corpus', 'example'] * 2,
        'rootless': ['--corpora', f'{corpora}', '--seed', '3'],
        'jobs': ['--family', 'hello', '--seed', '3', '--jobs', '0'],
        'seeds': ['--family', 'hello'],
    }
    argv = ['perturb', 'loop', '--work', f'{work}', '--out', f'{out}']
    argv += extra.get(broken, ['--family', 'hello', '--seed', '3'])
    held = sorted(tmp_path.rglob('*'))
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    assert sorted(tmp_path.rglob('*')) == held
    assert not out.exists()


# ------ helpers


def _sides_file(
    tmp_path: pathlib.Path,
    engine: side.Side,
    *,
    argv: Optional[object] = None,
    describe: Optional[Sequence[str]] = None,
) -> pathlib.Path:
    """Write a sides file with two sides of the engine and return its path.

    ``engine`` and ``other`` both run the command of the side ``engine``;
    ``argv`` and ``describe`` replace those keys of ``engine``, for a
    test that breaks that side.
    """
    if argv is None:
        argv = engine.argv
    if describe is None:
        describe = []
    first = json.dumps(argv)
    other = json.dumps(engine.argv)
    described = json.dumps(describe)
    path = tmp_path / 'sides.toml'
    path.write_text(
        f'[engine]\nargv = {first}\ndescribe = {described}\n[other]\nargv = {other}\n',
        encoding='utf-8',
    )
    return path


def _corpora_file(tmp_path: pathlib.Path, corpus: runner.Corpus) -> pathlib.Path:
    """Write a corpora file that declares ``corpus`` and return its path."""
    checkout = json.dumps(f'{corpus.checkout}')
    root = json.dumps(corpus.root)
    path = tmp_path / 'corpora.toml'
    text = f'[example]\ncheckout = {checkout}\nroot = {root}\n'
    path.write_text(text, encoding='utf-8')
    return path
