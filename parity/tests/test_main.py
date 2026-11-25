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

import pytest

from parity import __main__ as cli, oracle, registry, runner, side

from .conftest import NOW

__all__ = [
    'test_v1_sides_run_as_the_oracle_names_them',
    'test_matrix_command_strips_only_a_declared_notice',
    'test_run_that_would_compare_nothing_is_refused',
    'test_failed_selfcheck_keeps_its_streams_and_trees',
    'test_registry_command_proves_the_classifiers',
]

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='requires git')

# a side that runs the command it is given and prints everything but the
# WIKI_NOW notice, as a wrapper that fixes the clock by itself does
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


# a describe command: what it prints comes from the side's own environment
_DESCRIBE = "import os; print(os.environ['EXAMPLE'], os.environ['WIKI_NOW'])"


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
    corpora = tmp_path / 'corpora.toml'
    corpora.write_text(
        f'[example]\n'
        f'checkout = {json.dumps(f"{corpus.checkout}")}\n'
        f'root = {json.dumps(corpus.root)}\n',
        encoding='utf-8',
    )
    out = tmp_path / 'out'
    argv = [
        'matrix',
        *('--a', 'oracle', '--b', 'campaign'),
        *('--corpora', f'{corpora}', '--stage', 'write'),
        *('--work', f'{tmp_path / "work"}', '--out', f'{out}', '--now', NOW),
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
    """A side's ``WIKI_NOW`` notice is dropped only where the side declares it.

    The engine prints the notice and a wrapper side prints none, so the
    two are equal once the engine's notice is stripped. With the notice
    undeclared the same pair differs on stderr, and the command exits 1
    and keeps the streams that differ.
    """
    relay = tmp_path / 'relay.py'
    relay.write_text(_RELAY, encoding='utf-8')
    sides = tmp_path / 'sides.toml'
    sides.write_text(
        f'[engine]\n'
        f'argv = {json.dumps(engine.argv)}\n'
        f'notice = {json.dumps(declared)}\n'
        f'env = {{ EXAMPLE = "engine" }}\n'
        f'describe = {json.dumps([sys.executable, "-c", _DESCRIBE])}\n'
        f'[relay]\n'
        f'argv = {json.dumps([sys.executable, f"{relay}", *engine.argv])}\n'
        f'notice = false\n',
        encoding='utf-8',
    )
    corpora = tmp_path / 'corpora.toml'
    corpora.write_text(
        f'[example]\n'
        f'checkout = {json.dumps(f"{corpus.checkout}")}\n'
        f'root = {json.dumps(corpus.root)}\n',
        encoding='utf-8',
    )
    out = tmp_path / 'out'
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', 'engine', '--b', 'relay'),
        *('--corpora', f'{corpora}', '--stage', 'write'),
        *('--work', f'{tmp_path / "work"}', '--out', f'{out}', '--now', NOW),
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
    assert not (tmp_path / 'work' / 'example').exists()


@pytest.mark.parametrize(
    argnames=('broken', 'message'),
    argvalues=[
        ('stage', 'invalid choice'),
        ('corpora', 'declares no corpus'),
        ('side', 'does not start'),
        ('identity', 'is not the engine it names'),
        ('name', 'No side is named'),
        ('taken', 'names as v1 sides'),
        ('registry', 'has the status'),
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
    """A mistyped stage or side, an empty corpora file and a dead side are no pass.

    Each would compare nothing and report no difference. A sides file that
    takes the name of a v1 side would pass something else off as the
    oracle, and so would a v1 side that runs another build than the one it
    is expected to be. A registry file that breaks its rules could excuse
    what nobody approved. The command exits 2 with a message instead,
    before it snapshots anything or writes a result file.
    """
    sides = tmp_path / 'sides.toml'
    prefix = list(engine.argv)
    if broken == 'side':
        prefix = [sys.executable, '-c', 'import nonexistent_module']
    declared = 'oracle' if broken == 'taken' else 'engine'
    sides.write_text(f'[{declared}]\nargv = {json.dumps(prefix)}\n', encoding='utf-8')
    corpora = tmp_path / 'corpora.toml'
    if broken != 'corpora':
        corpora.write_text(
            f'[example]\n'
            f'checkout = {json.dumps(f"{corpus.checkout}")}\n'
            f'root = {json.dumps(corpus.root)}\n',
            encoding='utf-8',
        )
    else:
        corpora.write_text('', encoding='utf-8')
    out = tmp_path / 'out'
    name = 'enigne' if broken == 'name' else declared
    if broken == 'identity':
        # the oracle side is handed out as a build its checkout does not hold
        expected = {**engine.identity, 'build': 'another'}
        other = dataclasses.replace(engine, identity=expected)
        monkeypatch.setattr(oracle, 'side', lambda *_: other)
        name = 'oracle'
    if broken == 'registry':
        # the registry file holds an entry under a status it does not have
        text = registry.PATH.read_text(encoding='utf-8')
        changed = tmp_path / 'differences.toml'
        changed.write_text(text.replace('"approved"', '"agreed"'), encoding='utf-8')
        monkeypatch.setattr(registry, 'PATH', changed)
    argv = [
        'matrix',
        *('--sides', f'{sides}', '--a', name, '--b', name),
        *('--corpora', f'{corpora}'),
        *('--stage', 'wirte' if broken == 'stage' else 'write'),
        *('--work', f'{tmp_path / "work"}', '--out', f'{out}', '--now', NOW),
    ]
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / 'work').exists()
    assert not out.exists()


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
    sides = tmp_path / 'sides.toml'
    seeded = "import os; print(os.environ.get('PYTHONHASHSEED'))"
    sides.write_text(
        f'[seeded]\n'
        f'argv = {json.dumps([sys.executable, "-c", seeded])}\n'
        f'notice = false\n',
        encoding='utf-8',
    )
    corpora = tmp_path / 'corpora.toml'
    corpora.write_text(
        f'[example]\n'
        f'checkout = {json.dumps(f"{corpus.checkout}")}\n'
        f'root = {json.dumps(corpus.root)}\n',
        encoding='utf-8',
    )
    out = tmp_path / 'out'
    work = tmp_path / 'work'
    argv = [
        'selfcheck',
        *('--sides', f'{sides}', '--side', 'seeded', '--corpora', f'{corpora}'),
        *('--work', f'{work}', '--out', f'{out}', '--now', NOW),
    ]
    assert cli.main(argv) == 1
    document = json.loads((out / 'example.json').read_text(encoding='utf-8'))
    verdicts = {check['name']: check['passed'] for check in document['checks']}
    assert verdicts['clean'] is False
    stream = out / 'example' / 'clean' / 'pristine' / '000 seeded seed 2.out'
    assert stream.read_bytes() == b'2\n'
    tree = work / 'example' / 'failed' / 'clean' / 'pristine' / 'seeded seed 2' / 'tree'
    assert (tree / corpus.root / '_index.md').is_file()


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
    corpora = tmp_path / 'corpora.toml'
    corpora.write_text(
        f'[example]\n'
        f'checkout = {json.dumps(f"{corpus.checkout}")}\n'
        f'root = {json.dumps(corpus.root)}\n',
        encoding='utf-8',
    )
    out = tmp_path / 'out'
    argv = [
        'registry',
        *('--side', 'oracle', '--corpora', f'{corpora}'),
        *('--work', f'{tmp_path / "work"}', '--out', f'{out}', '--now', NOW),
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
    }
    # the snapshot and every clone are gone
    assert not (tmp_path / 'work' / 'example').exists()
    assert cli.main([*argv, '--release']) == (1 if waits else 0)
