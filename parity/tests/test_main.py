"""Test the ``parity.__main__`` module.

Drives the harness command line as a gate does: sides come from the oracle
file or a TOML file, corpora from a TOML file, and the verdict is the exit
code plus the result files.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import sys
from collections.abc import Sequence
from typing import Optional

import pytest

from parity import __main__ as cli, oracle, registry, runner, selfcheck, side

from .conftest import NOW, _needs_git

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

# a side that runs the command it is given and words the text of `re` its
# own way, as a second engine may
_WORDED = """
import subprocess, sys
result = subprocess.run(sys.argv[1:], capture_output=True)
stderr = result.stderr.replace(b'unterminated subpattern', b'unclosed group')
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(stderr)
sys.exit(result.returncode)
"""

# a describe command: what it prints comes from the side's own environment
_DESCRIBE = "import os; print(os.environ['EXAMPLE'], os.environ['WIKI_FREEZE_TIME'])"


@_needs_git
def test_v1_sides_run_as_the_oracle_names_them(
    tmp_path: pathlib.Path,
    corpus: runner.Corpus,
) -> None:
    """The oracle and campaign sides need no sides file, and say what they are.

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
        *('--a', 'oracle', '--b', 'campaign'),
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
    worded = [sys.executable, '-c', _WORDED, *engine.argv]
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
